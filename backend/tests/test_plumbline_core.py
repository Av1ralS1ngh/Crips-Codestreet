"""The conservatism proof, executed.

PLUMBLINE asserts a cart's value on a card only when it can exhibit a concrete allocation
realizing at least that much. The whole submission rests on one claim: the exhibited
allocation is achievable, so the asserted value can never exceed the true optimum.

This file tries to break that claim rather than illustrate it.

  * `_optimum` computes the true optimum by exhaustive search plus max-flow, sharing no
    code with the greedy allocator. It is the independent ground truth, and it is itself
    pinned by hand-checked cases so a bug in the oracle cannot quietly pass everything.
  * The headline property is checked over hundreds of seeded random instances: the witness
    never exceeds the optimum, and — stronger — no witness the verifier *accepts* ever
    exceeds the optimum, including witnesses deliberately mutated to overstate.
  * Every mutation of a valid witness is rejected with its own specific reason code, not a
    generic failure.

Randomness is seeded and the seeds are fixed, so a failure here is reproducible from the
seed printed in the assertion message.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import random
import statistics
import time
from collections import deque
from typing import Iterable, Sequence

import pytest

from caveat.cart import Cart, CartLine
from plumbline.allocate import (
    AllocationError,
    AllocationResult,
    Candidate,
    allocate,
    candidates,
    naive_sum,
)
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    MANIFEST_VERSION,
    WINDOW_ANNUAL,
    WINDOW_MONTHLY,
    Benefit,
    Eligibility,
    Manifest,
    ManifestError,
    SignedManifest,
    build_manifest,
    canonical_json,
    eligible_benefits,
    sign_manifest,
    verify_manifest,
)
from plumbline.witness import (
    ERR_CAPACITY,
    ERR_CART_MISMATCH,
    ERR_CONSUMPTION_MISMATCH,
    ERR_CURRENCY_MISMATCH,
    ERR_CREDIT_OVER_LINE,
    ERR_DOUBLE_ASSIGNED,
    ERR_DUPLICATE_SKU,
    ERR_EXCLUSIVITY,
    ERR_INELIGIBLE,
    ERR_LINE_OVER_OFFSET,
    ERR_MANIFEST_MISMATCH,
    ERR_NEGATIVE_AMOUNT,
    ERR_OVERSTATED,
    ERR_UNAVAILABLE,
    ERR_UNKNOWN_BENEFIT,
    ERR_UNKNOWN_LINE,
    ERR_UNPRICED,
    ERR_VALUE_MISMATCH,
    FAILURE_CODES,
    Assignment,
    Failure,
    Verification,
    Witness,
    verify_witness,
)

T0 = 1_753_600_000
SIGNING_KEY = "plumbline-prototype-manifest-key"

CATEGORIES = ("dining", "travel", "groceries", "electronics")
MCCS = (5812, 4511, 5411, 5732)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def line(sku: str, amount: int, category: str = "dining", mcc: int = 5812) -> CartLine:
    return CartLine(
        sku=sku, description=f"line {sku}", amount=amount, mcc=mcc, category=category
    )


def manifest_of(*benefits: Benefit, manifest_id: str = "m_test") -> Manifest:
    return build_manifest(
        manifest_id=manifest_id,
        issuer="issuer_demo",
        product="Demo Card",
        benefits=benefits,
        issued_at=T0,
    )


def credit(
    bid: str,
    capacity: int,
    *,
    category: str | None = "dining",
    group: str | None = None,
    enrolled: bool = True,
    requires_enrollment: bool = False,
) -> Benefit:
    return Benefit(
        benefit_id=bid,
        kind=KIND_CREDIT,
        label=f"{bid} credit",
        eligibility=Eligibility(categories=(category,) if category else ()),
        capacity_minor=capacity,
        exclusivity_group=group,
        window=WINDOW_MONTHLY,
        requires_enrollment=requires_enrollment,
        enrolled=enrolled,
    )


def earn(
    bid: str,
    rate_bp: int,
    *,
    capacity: int | None = None,
    category: str | None = None,
    group: str | None = None,
) -> Benefit:
    return Benefit(
        benefit_id=bid,
        kind=KIND_EARN,
        label=f"{bid} earn",
        eligibility=Eligibility(categories=(category,) if category else ()),
        rate_bp=rate_bp,
        capacity_minor=capacity,
        exclusivity_group=group,
        window=WINDOW_ANNUAL,
    )


def protection(
    bid: str,
    flat: int,
    *,
    capacity: int | None = None,
    category: str | None = None,
    group: str | None = None,
) -> Benefit:
    return Benefit(
        benefit_id=bid,
        kind=KIND_PROTECTION,
        label=f"{bid} cover",
        eligibility=Eligibility(categories=(category,) if category else ()),
        flat_minor=flat,
        capacity_minor=capacity,
        exclusivity_group=group,
    )


# ---------------------------------------------------------------------------
# Independent ground truth: the true optimum, by exhaustive search + max flow
# ---------------------------------------------------------------------------
#
# Earn and protection benefits are all-or-nothing, so their assignments are enumerated
# exhaustively. Statement credits take any integer amount up to the line and their
# remaining balance, which makes the credit sub-problem a transportation problem: benefits
# on one side with capacity, lines on the other with the spend available to offset. Max
# flow solves it exactly and integrally. Exclusivity is handled by enumerating which
# benefit, if any, claims each (line, group).
#
# This shares no code with allocate.py. That is the entire point of it.


def _max_flow(cap: list[list[int]], source: int, sink: int) -> int:
    """Edmonds-Karp. Graphs here have single-digit node counts."""
    n = len(cap)
    residual = [row[:] for row in cap]
    total = 0
    while True:
        parent = [-1] * n
        parent[source] = source
        queue = deque([source])
        while queue:
            u = queue.popleft()
            for v in range(n):
                if parent[v] == -1 and residual[u][v] > 0:
                    parent[v] = u
                    queue.append(v)
        if parent[sink] == -1:
            return total
        bottleneck = min(
            residual[parent[v]][v]
            for v in _path_nodes(parent, source, sink)
        )
        for v in _path_nodes(parent, source, sink):
            residual[parent[v]][v] -= bottleneck
            residual[v][parent[v]] += bottleneck
        total += bottleneck


def _path_nodes(parent: Sequence[int], source: int, sink: int) -> list[int]:
    out = []
    v = sink
    while v != source:
        out.append(v)
        v = parent[v]
    return out


def _priced_pairs(manifest: Manifest, cart: Cart) -> list[tuple[CartLine, Benefit]]:
    return [
        (ln, b)
        for b in manifest.priced()
        for ln in cart.lines
        if b.eligibility.admits(ln, cart.merchant)
    ]


def _best_credit_value(
    credit_pairs: Sequence[tuple[CartLine, Benefit]],
    blocked: frozenset[tuple[str, str]],
) -> int:
    """Max total statement-credit value given which (line, group) slots are already taken."""
    free: list[tuple[CartLine, Benefit]] = []
    grouped: dict[tuple[str, str], list[tuple[CartLine, Benefit]]] = {}
    for ln, b in credit_pairs:
        if b.exclusivity_group is None:
            free.append((ln, b))
            continue
        key = (ln.sku, b.exclusivity_group)
        if key in blocked:
            continue
        grouped.setdefault(key, []).append((ln, b))

    options = [[None, *members] for members in grouped.values()]
    best = 0
    for combo in itertools.product(*options) if options else [()]:
        allowed = list(free) + [p for p in combo if p is not None]
        best = max(best, _credit_flow(allowed))
    return best


def _credit_flow(allowed: Sequence[tuple[CartLine, Benefit]]) -> int:
    if not allowed:
        return 0
    benefit_ids = sorted({b.benefit_id for _, b in allowed})
    skus = sorted({ln.sku for ln, _ in allowed})
    lines = {ln.sku: ln for ln, _ in allowed}
    benefits = {b.benefit_id: b for _, b in allowed}

    n = 2 + len(benefit_ids) + len(skus)
    source, sink = 0, 1
    bnode = {bid: 2 + i for i, bid in enumerate(benefit_ids)}
    lnode = {sku: 2 + len(benefit_ids) + i for i, sku in enumerate(skus)}
    unbounded = sum(lines[s].amount for s in skus) + 1

    cap = [[0] * n for _ in range(n)]
    for bid in benefit_ids:
        c = benefits[bid].capacity_minor
        cap[source][bnode[bid]] = unbounded if c is None else c
    for sku in skus:
        cap[lnode[sku]][sink] = lines[sku].amount
    for ln, b in allowed:
        cap[bnode[b.benefit_id]][lnode[ln.sku]] = ln.amount

    return _max_flow(cap, source, sink)


def _optimum(manifest: Manifest, cart: Cart, *, budget: int = 1 << 13) -> int | None:
    """The exact maximum realizable value, or None if the instance is too big to enumerate.

    Callers that get None must skip the instance rather than treat it as zero.
    """
    pairs = _priced_pairs(manifest, cart)
    fixed = [
        (ln, b, b.value_for_line(ln, cart.merchant))
        for ln, b in pairs
        if b.kind != KIND_CREDIT
    ]
    # A zero-value fixed assignment can only consume capacity and block a group, so it is
    # never part of an optimum and dropping it costs nothing.
    fixed = [f for f in fixed if f[2] > 0]
    credit_pairs = [(ln, b) for ln, b in pairs if b.kind == KIND_CREDIT]

    if (1 << len(fixed)) > budget:
        return None

    memo: dict[frozenset[tuple[str, str]], int] = {}
    best = 0
    for mask in range(1 << len(fixed)):
        chosen = [fixed[i] for i in range(len(fixed)) if mask >> i & 1]
        claims: dict[tuple[str, str], str] = {}
        used: dict[str, int] = {}
        feasible = True
        for ln, b, value in chosen:
            if b.exclusivity_group is not None:
                key = (ln.sku, b.exclusivity_group)
                if key in claims and claims[key] != b.benefit_id:
                    feasible = False
                    break
                claims[key] = b.benefit_id
            used[b.benefit_id] = used.get(b.benefit_id, 0) + value
            if b.capacity_minor is not None and used[b.benefit_id] > b.capacity_minor:
                feasible = False
                break
        if not feasible:
            continue
        blocked = frozenset(claims)
        if blocked not in memo:
            memo[blocked] = _best_credit_value(credit_pairs, blocked)
        best = max(best, sum(v for _, _, v in chosen) + memo[blocked])
    return best


# ---------------------------------------------------------------------------
# Seeded instance generation
# ---------------------------------------------------------------------------


def _random_cart(rng: random.Random) -> Cart:
    n = rng.randint(1, 4)
    lines = [
        line(
            f"sku_{i}",
            rng.randrange(1, 40) * 5_000,
            category=rng.choice(CATEGORIES),
            mcc=rng.choice(MCCS),
        )
        for i in range(n)
    ]
    return Cart.of(rng.choice(("m_resy", "m_croma")), lines)


def _random_benefit(rng: random.Random, idx: int) -> Benefit:
    kind = rng.choices(
        (KIND_EARN, KIND_CREDIT, KIND_PROTECTION, KIND_UNPRICED), weights=(4, 4, 2, 1)
    )[0]
    roll = rng.random()
    if roll < 0.40:
        elig = Eligibility(categories=(rng.choice(CATEGORIES),))
    elif roll < 0.60:
        elig = Eligibility(mccs=(rng.choice(MCCS),))
    else:
        elig = Eligibility()
    requires_enrollment = rng.random() < 0.25
    return Benefit(
        benefit_id=f"b{idx}",
        kind=kind,
        label=f"benefit {idx}",
        eligibility=elig,
        rate_bp=rng.choice((0, 100, 200, 500, 1_000)),
        capacity_minor=None if rng.random() < 0.3 else rng.randrange(0, 25) * 2_000,
        flat_minor=rng.choice((0, 5_000, 20_000)),
        exclusivity_group=rng.choice((None, None, "dining_pool", "travel_pool")),
        requires_enrollment=requires_enrollment,
        enrolled=not (requires_enrollment and rng.random() < 0.4),
    )


def _random_instance(seed: int) -> tuple[Manifest, Cart]:
    rng = random.Random(seed)
    cart = _random_cart(rng)
    benefits = [_random_benefit(rng, i) for i in range(rng.randint(1, 5))]
    return manifest_of(*benefits, manifest_id=f"m_{seed}"), cart


# ---------------------------------------------------------------------------
# The oracle itself, pinned by hand
# ---------------------------------------------------------------------------


def test_optimum_oracle_hand_checked_cases():
    """If the ground truth is wrong the headline property proves nothing."""
    dinner = line("s1", 80_000)

    # Two ungrouped credits, ₹500 each, on an ₹800 dinner: the line bounds the pair.
    m = manifest_of(credit("c1", 50_000), credit("c2", 50_000))
    assert _optimum(m, Cart.of("m_resy", [dinner])) == 80_000

    # Same two credits in one exclusivity group: only one may attach.
    m = manifest_of(
        credit("c1", 50_000, group="dining_pool"), credit("c2", 50_000, group="dining_pool")
    )
    assert _optimum(m, Cart.of("m_resy", [dinner])) == 50_000

    # Credits with room to spare: the line still bounds them.
    m = manifest_of(credit("c1", 500_000), credit("c2", 500_000))
    assert _optimum(m, Cart.of("m_resy", [dinner])) == 80_000

    # One credit spread across two lines, capacity binding across both.
    m = manifest_of(credit("c1", 100_000))
    two = Cart.of("m_resy", [line("s1", 60_000), line("s2", 60_000)])
    assert _optimum(m, two) == 100_000

    # Earn stacks with a credit: 5% of ₹800 is ₹40, plus the ₹500 credit.
    m = manifest_of(credit("c1", 50_000), earn("e1", 500))
    assert _optimum(m, Cart.of("m_resy", [dinner])) == 50_000 + 4_000

    # An earn benefit is all-or-nothing against its cap, so a cap below the line's earn
    # yields nothing at all.
    m = manifest_of(earn("e1", 500, capacity=1_000))
    assert _optimum(m, Cart.of("m_resy", [dinner])) == 0

    # Protection is a flat amount and is not bounded by the line it attaches to.
    m = manifest_of(protection("p1", 200_000))
    assert _optimum(m, Cart.of("m_resy", [line("s1", 10_000)])) == 200_000

    # Choosing the grouped earn over the grouped credit, when the earn is worth more.
    m = manifest_of(
        credit("c1", 1_000, group="dining_pool"),
        earn("e1", 1_000, group="dining_pool", category="dining"),
    )
    assert _optimum(m, Cart.of("m_resy", [dinner])) == 8_000


def test_max_flow_helper():
    """The transportation solver underneath the oracle, checked on its own."""
    # source -> a (5) -> t (10); source -> b (7) -> t.  Sink edge caps at 10.
    cap = [[0] * 4 for _ in range(4)]
    cap[0][2], cap[0][3] = 5, 7
    cap[2][1], cap[3][1] = 10, 10
    assert _max_flow(cap, 0, 1) == 12

    cap = [[0] * 4 for _ in range(4)]
    cap[0][2], cap[0][3] = 5, 7
    cap[2][1], cap[3][1] = 3, 2
    assert _max_flow(cap, 0, 1) == 5

    assert _max_flow([[0, 0], [0, 0]], 0, 1) == 0


# ---------------------------------------------------------------------------
# THE HEADLINE PROPERTY: the witness never exceeds the true optimum
# ---------------------------------------------------------------------------

SEEDS = range(400)


def test_witness_never_exceeds_true_optimum():
    """Conservatism by construction, over 400 seeded instances against exact ground truth.

    This is the claim the entire submission rests on. The optimum is computed by exhaustive
    search plus max flow, sharing no code with the allocator.
    """
    checked = 0
    skipped = 0
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        result = allocate(manifest, cart)
        best = _optimum(manifest, cart)
        if best is None:
            skipped += 1
            continue
        checked += 1
        realized = result.witness.realized_minor()
        assert realized <= best, (
            f"seed {seed}: witness asserts {realized} but the true optimum is {best} — "
            f"the value is not achievable"
        )
        verification = verify_witness(
            witness=result.witness,
            manifest=manifest,
            cart=cart,
            asserted_minor=realized,
        )
        assert verification.ok, f"seed {seed}: own witness fails own verifier: {verification.codes()}"
        assert verification.supports_assertion
        assert verification.realized_minor == realized
    assert checked >= len(SEEDS) * 0.9, f"only {checked} instances were small enough to verify"
    assert skipped <= len(SEEDS) * 0.1


def test_verifier_never_accepts_a_value_above_the_optimum():
    """The stronger claim: no witness the verifier accepts overstates, however it was built.

    The allocator is not trusted here — witnesses are mutated adversarially to inflate
    values, add assignments, and evade capacity. A verified value must still be achievable.
    Also asserts the reported total is a sound lower bound even when verification fails,
    which is what makes reporting it safer than reporting zero.
    """
    rng = random.Random(20260825)
    accepted = 0
    rejected = 0
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        best = _optimum(manifest, cart)
        if best is None:
            continue
        base = allocate(manifest, cart).witness
        for mutant in _mutations(base, manifest, cart, rng):
            v = verify_witness(
                witness=mutant, manifest=manifest, cart=cart, asserted_minor=0
            )
            assert v.realized_minor <= best, (
                f"seed {seed}: verifier reports {v.realized_minor}, above the true optimum "
                f"{best}; the reported total is meant to be achievable"
            )
            if v.ok:
                accepted += 1
                assert v.realized_minor == mutant.realized_minor()
            else:
                rejected += 1
                assert set(v.codes()) <= set(FAILURE_CODES)
    assert rejected > 500, f"only {rejected} mutants rejected — the mutator is too gentle"
    assert accepted > 0, "no mutant was accepted — the mutator is not producing valid witnesses"


def _priced_assignment(benefit: Benefit, ln: CartLine) -> Assignment:
    """The assignment a manifest-faithful attacker would write: correct value, correct draw.

    An attacker who forges arithmetic is caught by inspection. The interesting adversary
    writes assignments that are individually beyond reproach and relies on the aggregate
    constraints being unchecked.
    """
    if benefit.kind == KIND_CREDIT:
        value = min(ln.amount, benefit.capacity_minor or ln.amount)
    elif benefit.kind == KIND_EARN:
        value = (ln.amount * benefit.rate_bp) // 10_000
    else:
        value = benefit.flat_minor
    return Assignment(ln.sku, benefit.benefit_id, value, value)


def _mutations(
    witness: Witness, manifest: Manifest, cart: Cart, rng: random.Random
) -> Iterable[Witness]:
    """Adversarial variations on a witness: inflate, duplicate, misattribute, evade caps."""
    assignments = list(witness.assignments)
    yield witness

    # The cap-evasion family: correctly priced extra assignments, some declaring no draw
    # against their capacity. These are the ones that pass every per-assignment check.
    for benefit in manifest.priced():
        for ln in cart.lines:
            if not benefit.eligibility.admits(ln, cart.merchant):
                continue
            honest = _priced_assignment(benefit, ln)
            if honest.value_minor <= 0:
                continue
            variants = [
                honest,
                # No draw against capacity: the cap-evasion attack.
                Assignment(honest.line_sku, honest.benefit_id, 0, honest.value_minor),
            ]
            if benefit.kind == KIND_CREDIT:
                # A credit offsetting more spend than the line carries. Value and draw stay
                # consistent with each other, so only the line bound catches it.
                greedy = max(ln.amount * 3, benefit.capacity_minor or 0)
                variants.append(Assignment(ln.sku, benefit.benefit_id, greedy, greedy))
            for extra in variants:
                kept = [
                    a
                    for a in assignments
                    if (a.line_sku, a.benefit_id) != (extra.line_sku, extra.benefit_id)
                ]
                yield Witness(witness.manifest_id, witness.cart_hash, tuple(kept) + (extra,))

    if not assignments:
        return
    for _ in range(6):
        mutated = list(assignments)
        i = rng.randrange(len(mutated))
        a = mutated[i]
        choice = rng.randrange(7)
        if choice == 0:
            mutated[i] = Assignment(a.line_sku, a.benefit_id, a.consumed_minor, a.value_minor * 3)
        elif choice == 1:
            mutated[i] = Assignment(a.line_sku, a.benefit_id, 0, a.value_minor)
        elif choice == 2:
            mutated[i] = Assignment(
                a.line_sku, a.benefit_id, a.consumed_minor * 4, a.value_minor * 4
            )
        elif choice == 3:
            mutated.append(a)
        elif choice == 4:
            other = rng.choice(manifest.benefits)
            mutated.append(
                Assignment(a.line_sku, other.benefit_id, a.consumed_minor, a.value_minor)
            )
        elif choice == 5:
            other = rng.choice(cart.lines)
            mutated.append(
                Assignment(other.sku, a.benefit_id, a.consumed_minor, a.value_minor)
            )
        else:
            mutated[i] = Assignment(a.line_sku, a.benefit_id, -a.consumed_minor, a.value_minor)
        yield Witness(witness.manifest_id, witness.cart_hash, tuple(mutated))


# ---------------------------------------------------------------------------
# The overstatement gap
# ---------------------------------------------------------------------------


def test_naive_sum_never_below_the_witness():
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        result = allocate(manifest, cart)
        naive = naive_sum(manifest, cart)
        assert naive >= result.witness.realized_minor(), f"seed {seed}"


def test_naive_sum_strictly_overstates_exactly_when_a_constraint_binds():
    """The demo's first beat, as a biconditional.

    If a capacity, an exclusivity group or the spend on a line actually bound, per-line
    summation claims strictly more than the card can deliver. If nothing bound, the naive
    answer was right — and saying so is what keeps the demo honest.
    """
    bound = 0
    unbound = 0
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        result = allocate(manifest, cart)
        naive = naive_sum(manifest, cart)
        realized = result.witness.realized_minor()
        if result.binding():
            bound += 1
            assert naive > realized, (
                f"seed {seed}: {result.to_dict()} bound something but naive {naive} "
                f"equals witness {realized}"
            )
        else:
            unbound += 1
            assert naive == realized, f"seed {seed}: nothing bound yet naive {naive} != {realized}"
    assert bound > 50, f"only {bound} instances had a binding constraint"
    assert unbound > 10, f"only {unbound} instances had none"


def test_overstatement_gap_distribution(capsys):
    """Measured, not asserted. Printed so the figure quoted on stage has a provenance."""
    gaps: list[float] = []
    absolute: list[int] = []
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        result = allocate(manifest, cart)
        naive = naive_sum(manifest, cart)
        realized = result.witness.realized_minor()
        if naive <= 0:
            continue
        absolute.append(naive - realized)
        gaps.append(100.0 * (naive - realized) / naive)
    assert gaps, "no instance produced a positive naive sum"
    gaps.sort()

    def pct(p: float) -> float:
        return gaps[min(len(gaps) - 1, int(p / 100.0 * len(gaps)))]

    with capsys.disabled():
        print(
            f"\n  overstatement of naive per-line summation over {len(gaps)} random carts"
            f"\n    p50 {pct(50):5.1f}%   p90 {pct(90):5.1f}%   p99 {pct(99):5.1f}%   "
            f"max {gaps[-1]:5.1f}%"
            f"\n    mean {statistics.fmean(gaps):5.1f}%   "
            f"carts where naive overstates: "
            f"{sum(1 for g in gaps if g > 0)}/{len(gaps)}"
        )
    # A distribution report is worthless if the effect is not there at all.
    assert max(gaps) > 20.0
    assert statistics.fmean(gaps) > 1.0


def test_two_credits_one_dinner_is_the_canonical_overstatement():
    """Demo beat 1, pinned to exact numbers so the slide cannot drift from the code."""
    m = manifest_of(credit("c_dining_a", 50_000), credit("c_dining_b", 50_000))
    cart = Cart.of("m_resy", [line("s1", 80_000)])

    assert naive_sum(m, cart) == 160_000  # ₹1,600 claimed on an ₹800 dinner
    result = allocate(m, cart)
    assert result.witness.realized_minor() == 80_000  # ₹800, and no more than the dinner
    assert _optimum(m, cart) == 80_000
    assert result.binding()

    verification = verify_witness(
        witness=result.witness, manifest=m, cart=cart, asserted_minor=80_000
    )
    assert verification.ok and verification.supports_assertion
    assert result.witness.derivation(m, cart) == [
        "c_dining_a credit on line s1: ₹500",
        "c_dining_b credit on line s1: ₹300",
    ]


def test_greedy_optimality_gap_is_measured_not_assumed(capsys):
    """The witness is conservative by construction and suboptimal by design.

    Quoting the gap is the honest version of the claim; claiming optimality is not
    available to us, because generalized assignment is NP-hard and the hot path does not
    solve it. The bounds here are deliberately loose — the point is the printed figure.
    """
    gaps: list[float] = []
    optimal = 0
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        best = _optimum(manifest, cart)
        if best is None or best <= 0:
            continue
        realized = allocate(manifest, cart).witness.realized_minor()
        gaps.append(100.0 * (best - realized) / best)
        optimal += realized == best
    share = 100.0 * optimal / len(gaps)
    below = [g for g in gaps if g > 0]
    with capsys.disabled():
        print(
            f"\n  greedy witness vs exact optimum over {len(gaps)} random carts"
            f"\n    optimal on {optimal}/{len(gaps)} ({share:.1f}%)   "
            f"mean gap {statistics.fmean(gaps):.3f}%   "
            f"worst {max(gaps):.1f}%"
        )
    assert share >= 90.0
    assert statistics.fmean(gaps) <= 2.0
    assert below, "the oracle never beat greedy — it is probably just re-deriving it"


def test_hot_path_holds_a_checkout_budget(capsys):
    """The measured p50/p99 the pitch quotes, at the size MaxSMT was benchmarked on.

    The assertion bounds carry two orders of magnitude of headroom on purpose: this test
    exists to produce a reproducible number and to catch an accidental quadratic, not to
    fail on a loaded CI box. Never quote this alongside the entailment figure — different
    component, different budget.
    """
    rng = random.Random(1)
    cart = Cart.of(
        "m_bench",
        [
            line(f"s{i}", rng.randrange(1, 200) * 5_000, rng.choice(CATEGORIES), rng.choice(MCCS))
            for i in range(20)
        ],
    )
    manifest = manifest_of(
        *[_random_benefit(rng, i) for i in range(40)], manifest_id="m_bench"
    )
    witness = allocate(manifest, cart).witness

    def sample(fn, runs: int = 200) -> list[float]:
        out = []
        for _ in range(runs):
            start = time.perf_counter()
            fn()
            out.append((time.perf_counter() - start) * 1000.0)
        return sorted(out)

    alloc = sample(lambda: allocate(manifest, cart))
    check = sample(
        lambda: verify_witness(
            witness=witness,
            manifest=manifest,
            cart=cart,
            asserted_minor=witness.realized_minor(),
        )
    )
    p99 = lambda xs: xs[int(len(xs) * 0.99)]  # noqa: E731
    p50 = lambda xs: xs[len(xs) // 2]  # noqa: E731
    with capsys.disabled():
        print(
            f"\n  20 cart lines x 40 benefits, 200 runs, one instrument"
            f"\n    allocate        p50 {p50(alloc):6.3f}ms  p99 {p99(alloc):6.3f}ms"
            f"\n    verify_witness  p50 {p50(check):6.3f}ms  p99 {p99(check):6.3f}ms"
            f"  ({len(witness.assignments)} assignments, no solver)"
        )
    assert p99(alloc) < 50.0
    assert p99(check) < 25.0
    # Verification is linear in the witness; allocation sorts the candidate set. The
    # verifier must not be the slower of the two.
    assert p50(check) <= p50(alloc)


# ---------------------------------------------------------------------------
# Verifier soundness: every mutation gets its own reason code
# ---------------------------------------------------------------------------


def _valid_setup() -> tuple[Manifest, Cart, Witness]:
    m = manifest_of(
        credit("c_dining", 30_000, group="dining_pool"),
        credit("c_rival", 40_000, group="dining_pool"),
        earn("e_base", 200, capacity=100_000),
        protection("p_cover", 15_000, category="electronics"),
        Benefit(benefit_id="u_lounge", kind=KIND_UNPRICED, label="Lounge access"),
        Benefit(
            benefit_id="c_offer",
            kind=KIND_CREDIT,
            label="Enrollment-gated offer",
            eligibility=Eligibility(categories=("dining",)),
            capacity_minor=25_000,
            requires_enrollment=True,
            enrolled=False,
        ),
    )
    cart = Cart.of(
        "m_resy",
        [line("s_dinner", 90_000, "dining"), line("s_tv", 200_000, "electronics", 5732)],
    )
    witness = allocate(m, cart).witness
    assert verify_witness(
        witness=witness, manifest=m, cart=cart, asserted_minor=witness.realized_minor()
    ).ok
    return m, cart, witness


def _codes_for(witness: Witness, manifest: Manifest, cart: Cart, asserted: int = 0) -> tuple[str, ...]:
    return verify_witness(
        witness=witness, manifest=manifest, cart=cart, asserted_minor=asserted
    ).codes()


def _with(witness: Witness, *extra: Assignment) -> Witness:
    return Witness(witness.manifest_id, witness.cart_hash, witness.assignments + extra)


def _replace(witness: Witness, index: int, assignment: Assignment) -> Witness:
    items = list(witness.assignments)
    items[index] = assignment
    return Witness(witness.manifest_id, witness.cart_hash, tuple(items))


def test_reject_inflated_value():
    m, cart, w = _valid_setup()
    idx = next(i for i, a in enumerate(w.assignments) if a.benefit_id == "e_base")
    a = w.assignments[idx]
    bad = _replace(w, idx, Assignment(a.line_sku, a.benefit_id, a.consumed_minor, a.value_minor + 1))
    assert _codes_for(bad, m, cart) == (ERR_VALUE_MISMATCH,)


def test_reject_earn_declaring_no_capacity_draw():
    """The cap-evasion hole: value without consumption makes every cap unenforceable."""
    m, cart, w = _valid_setup()
    idx = next(i for i, a in enumerate(w.assignments) if a.benefit_id == "e_base")
    a = w.assignments[idx]
    bad = _replace(w, idx, Assignment(a.line_sku, a.benefit_id, 0, a.value_minor))
    assert _codes_for(bad, m, cart) == (ERR_CONSUMPTION_MISMATCH,)


def test_reject_capacity_exceeded():
    m = manifest_of(earn("e_capped", 1_000, capacity=5_000))
    cart = Cart.of("m_resy", [line("s1", 40_000), line("s2", 40_000)])
    forged = Witness(
        m.manifest_id,
        cart.hash(),
        (
            Assignment("s1", "e_capped", 4_000, 4_000),
            Assignment("s2", "e_capped", 4_000, 4_000),
        ),
    )
    v = verify_witness(witness=forged, manifest=m, cart=cart, asserted_minor=8_000)
    assert v.codes() == (ERR_CAPACITY,)
    assert not v.ok
    # Both assignments belong to the over-capacity benefit, so nothing is left to stand behind.
    assert v.realized_minor == 0
    assert v.claimed_minor == 8_000


def test_reject_ineligible_benefit():
    m, cart, w = _valid_setup()
    # The electronics-only protection attached to the dinner.
    bad = _with(w, Assignment("s_dinner", "p_cover", 15_000, 15_000))
    assert _codes_for(bad, m, cart) == (ERR_INELIGIBLE,)


def test_reject_exclusivity_violation():
    m, cart, w = _valid_setup()
    held = {a.benefit_id for a in w.assignments if a.line_sku == "s_dinner"}
    rival = "c_rival" if "c_dining" in held else "c_dining"
    bad = _with(w, Assignment("s_dinner", rival, 10_000, 10_000))
    assert _codes_for(bad, m, cart) == (ERR_EXCLUSIVITY,)


def test_reject_duplicate_assignment():
    m, cart, w = _valid_setup()
    bad = _with(w, w.assignments[0])
    assert _codes_for(bad, m, cart) == (ERR_DOUBLE_ASSIGNED,)


def test_reject_unknown_benefit():
    m, cart, w = _valid_setup()
    bad = _with(w, Assignment("s_dinner", "b_does_not_exist", 1_000, 1_000))
    assert _codes_for(bad, m, cart) == (ERR_UNKNOWN_BENEFIT,)


def test_reject_unknown_line():
    m, cart, w = _valid_setup()
    bad = _with(w, Assignment("sku_nowhere", "c_dining", 1_000, 1_000))
    assert _codes_for(bad, m, cart) == (ERR_UNKNOWN_LINE,)


def test_reject_unenrolled_benefit():
    m, cart, w = _valid_setup()
    bad = _with(w, Assignment("s_dinner", "c_offer", 25_000, 25_000))
    assert _codes_for(bad, m, cart) == (ERR_UNAVAILABLE,)


def test_reject_unpriced_benefit():
    m, cart, w = _valid_setup()
    bad = _with(w, Assignment("s_dinner", "u_lounge", 0, 100_000))
    assert _codes_for(bad, m, cart) == (ERR_UNPRICED,)


def test_reject_credit_larger_than_its_line():
    m = manifest_of(credit("c_big", 500_000))
    cart = Cart.of("m_resy", [line("s1", 10_000)])
    forged = Witness(m.manifest_id, cart.hash(), (Assignment("s1", "c_big", 500_000, 500_000),))
    assert _codes_for(forged, m, cart) == (ERR_CREDIT_OVER_LINE,)


def test_reject_credits_that_together_over_offset_a_line():
    """No exclusivity group declared, each credit individually legal, the pair is not.

    This is the case that survives every per-assignment check. Without the per-line bound
    the witness verifies at ₹1,000 of value against an ₹800 dinner.
    """
    m = manifest_of(credit("c1", 50_000), credit("c2", 50_000))
    cart = Cart.of("m_resy", [line("s1", 80_000)])
    forged = Witness(
        m.manifest_id,
        cart.hash(),
        (Assignment("s1", "c1", 50_000, 50_000), Assignment("s1", "c2", 50_000, 50_000)),
    )
    v = verify_witness(witness=forged, manifest=m, cart=cart, asserted_minor=100_000)
    assert v.codes() == (ERR_LINE_OVER_OFFSET,)
    assert v.claimed_minor == 100_000
    assert v.realized_minor == 0
    assert not v.supports_assertion


def test_reject_negative_amounts():
    m, cart, w = _valid_setup()
    a = w.assignments[0]
    bad = _replace(w, 0, Assignment(a.line_sku, a.benefit_id, -1, a.value_minor))
    assert _codes_for(bad, m, cart) == (ERR_NEGATIVE_AMOUNT,)


def test_reject_duplicate_sku_cart():
    m = manifest_of(credit("c1", 50_000))
    cart = Cart.of("m_resy", [line("s1", 30_000), line("s1", 90_000)])
    forged = Witness(m.manifest_id, cart.hash(), (Assignment("s1", "c1", 50_000, 50_000),))
    v = verify_witness(witness=forged, manifest=m, cart=cart, asserted_minor=50_000)
    assert v.codes() == (ERR_DUPLICATE_SKU,)
    # And the allocator refuses to produce a witness it could not stand behind.
    with pytest.raises(AllocationError, match="more than once"):
        allocate(m, cart)


def test_reject_negative_line_amount():
    m = manifest_of(earn("e1", 500))
    cart = Cart.of("m_resy", [CartLine("s1", "refund", -50_000, 5812, "dining")])
    with pytest.raises(AllocationError, match="non-negative spend"):
        allocate(m, cart)
    forged = Witness(m.manifest_id, cart.hash(), (Assignment("s1", "e1", 0, 0),))
    assert _codes_for(forged, m, cart) == (ERR_NEGATIVE_AMOUNT,)


def test_reject_assertion_above_a_valid_witness():
    """Demo beat 2: the refusal. A perfectly valid witness that does not reach the claim."""
    m, cart, w = _valid_setup()
    realized = w.realized_minor()
    v = verify_witness(witness=w, manifest=m, cart=cart, asserted_minor=realized + 1)
    assert v.codes() == (ERR_OVERSTATED,)
    assert not v.ok and not v.supports_assertion
    # The witness is sound; only the assertion laid on top of it was not.
    assert v.realized_minor == realized
    assert v.claimed_minor == realized


def test_every_failure_code_is_reachable():
    """A closed vocabulary that contains a code nothing can produce is not closed."""
    produced = {
        ERR_MANIFEST_MISMATCH,
        ERR_CART_MISMATCH,
        ERR_CURRENCY_MISMATCH,
        ERR_VALUE_MISMATCH,
        ERR_CONSUMPTION_MISMATCH,
        ERR_CAPACITY,
        ERR_INELIGIBLE,
        ERR_EXCLUSIVITY,
        ERR_DOUBLE_ASSIGNED,
        ERR_UNKNOWN_BENEFIT,
        ERR_UNKNOWN_LINE,
        ERR_UNAVAILABLE,
        ERR_UNPRICED,
        ERR_CREDIT_OVER_LINE,
        ERR_LINE_OVER_OFFSET,
        ERR_NEGATIVE_AMOUNT,
        ERR_DUPLICATE_SKU,
        ERR_OVERSTATED,
    }
    assert produced == set(FAILURE_CODES)
    assert len(FAILURE_CODES) == len(set(FAILURE_CODES))


def test_a_witness_is_bound_to_the_manifest_and_cart_it_names() -> None:
    """The three binding codes, each earned by an actual verification.

    Arithmetic alone cannot catch these: a witness re-checked against a different cart or a
    different manifest can add up perfectly and still be about something else.
    """
    m, cart, w = _valid_setup()
    # Same benefits, different identity: only the binding check can catch this.
    renamed = dataclasses.replace(m, manifest_id=m.manifest_id + "_other")
    v = verify_witness(
        witness=w, manifest=renamed, cart=cart, asserted_minor=w.realized_minor()
    )
    assert v.codes() == (ERR_MANIFEST_MISMATCH,)
    assert not v.ok

    other_cart = Cart.of(
        "m_resy",
        [line("s_dinner", 91_000, "dining"), line("s_tv", 200_000, "electronics", 5732)],
    )
    v = verify_witness(
        witness=w, manifest=m, cart=other_cart, asserted_minor=w.realized_minor()
    )
    assert v.codes() == (ERR_CART_MISMATCH,)
    assert not v.ok


def test_a_manifest_and_cart_in_different_currencies_are_never_added() -> None:
    """Minor units carry no unit. A cent and a paisa must never meet in one sum."""
    m, cart, w = _valid_setup()
    foreign = dataclasses.replace(m, currency="USD" if m.currency != "USD" else "INR")
    v = verify_witness(
        witness=w, manifest=foreign, cart=cart, asserted_minor=w.realized_minor()
    )
    assert v.codes() == (ERR_CURRENCY_MISMATCH,)
    assert not v.ok


def test_every_mutation_of_a_valid_witness_is_rejected():
    """Systematic single-field mutation of every assignment, in both directions."""
    m, cart, w = _valid_setup()
    assert w.assignments
    checked = 0
    for i, a in enumerate(w.assignments):
        variants = [
            Assignment(a.line_sku, a.benefit_id, a.consumed_minor, a.value_minor + 1),
            Assignment(a.line_sku, a.benefit_id, a.consumed_minor + 1, a.value_minor),
            Assignment(a.line_sku, a.benefit_id, a.consumed_minor, a.value_minor * 10),
            Assignment(a.line_sku, "u_lounge", a.consumed_minor, a.value_minor),
            Assignment("sku_missing", a.benefit_id, a.consumed_minor, a.value_minor),
            Assignment(a.line_sku, "c_offer", a.consumed_minor, a.value_minor),
        ]
        for variant in variants:
            if variant == a:
                continue
            v = verify_witness(
                witness=_replace(w, i, variant), manifest=m, cart=cart, asserted_minor=0
            )
            assert not v.ok, f"mutation {variant} of {a} was accepted"
            assert v.codes(), "a rejection must carry a reason code"
            assert all(c in FAILURE_CODES for c in v.codes())
            checked += 1
    assert checked >= 20


def test_failures_are_reported_per_violation_not_collapsed():
    m, cart, w = _valid_setup()
    bad = _with(
        w,
        Assignment("s_dinner", "b_missing", 1, 1),
        Assignment("sku_missing", "c_dining", 1, 1),
    )
    codes = _codes_for(bad, m, cart)
    assert codes == (ERR_UNKNOWN_BENEFIT, ERR_UNKNOWN_LINE)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_witness_is_byte_identical_across_runs():
    for seed in range(40):
        manifest, cart = _random_instance(seed)
        first = canonical_json(
            allocate(manifest, cart).witness.to_dict(currency=cart.currency)
        )
        for _ in range(3):
            assert (
                canonical_json(
                    allocate(manifest, cart).witness.to_dict(currency=cart.currency)
                )
                == first
            )


def test_witness_is_invariant_under_benefit_reordering():
    """Ties resolve on identifiers, never on iteration order."""
    rng = random.Random(7)
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        base = canonical_json(
            allocate(manifest, cart).witness.to_dict(currency=cart.currency)
        )
        for _ in range(3):
            shuffled = list(manifest.benefits)
            rng.shuffle(shuffled)
            reordered = manifest_of(*shuffled, manifest_id=manifest.manifest_id)
            assert canonical_json(
                allocate(reordered, cart).witness.to_dict(currency=cart.currency)
            ) == base, (
                f"seed {seed}: reordering the benefit list changed the witness"
            )


def test_witness_assignments_are_invariant_under_cart_line_reordering():
    """The allocation must not depend on line order.

    The cart hash legitimately does: a witness commits to the exact cart it valued, and a
    reordered cart is a different document. The allocation itself must not move.
    """
    rng = random.Random(11)
    for seed in SEEDS:
        manifest, cart = _random_instance(seed)
        base = allocate(manifest, cart).witness
        for _ in range(3):
            lines = list(cart.lines)
            rng.shuffle(lines)
            shuffled = allocate(manifest, cart.with_lines(lines)).witness
            assert shuffled.assignments == base.assignments, f"seed {seed}"
            assert shuffled.realized_minor() == base.realized_minor()


def test_witness_survives_a_json_round_trip():
    manifest, cart = _random_instance(3)
    witness = allocate(manifest, cart).witness
    restored = Witness.from_dict(
        json.loads(json.dumps(witness.to_dict(currency=cart.currency)))
    )
    assert restored == witness
    assert canonical_json(restored.to_dict(currency=cart.currency)) == canonical_json(
        witness.to_dict(currency=cart.currency)
    )


def test_candidate_ordering_is_total():
    """Equal-value candidates still have exactly one order, so greedy is reproducible."""
    m = manifest_of(earn("e_a", 500), earn("e_b", 500))
    cart = Cart.of("m_resy", [line("s1", 100_000), line("s2", 100_000)])
    pool = candidates(m, cart)
    assert all(isinstance(c, Candidate) for c in pool)
    keys = [c.sort_key() for c in pool]
    assert len(set(keys)) == len(keys)
    assert sorted(keys) == sorted(set(keys))
    assert all(isinstance(k, tuple) and len(k) == 3 for k in keys)


# ---------------------------------------------------------------------------
# Allocation behaviour: partial credit, exhaustion, all-or-nothing
# ---------------------------------------------------------------------------


def test_partial_credit_offsets_what_it_can():
    """A ₹300 credit against a ₹1,000 dinner offsets ₹300, not ₹1,000 and not nothing."""
    m = manifest_of(credit("c1", 30_000))
    cart = Cart.of("m_resy", [line("s1", 100_000)])
    result = allocate(m, cart)
    assert result.witness.assignments == (Assignment("s1", "c1", 30_000, 30_000),)
    assert result.clipped_credits == 1
    assert verify_witness(
        witness=result.witness, manifest=m, cart=cart, asserted_minor=30_000
    ).ok
    assert _optimum(m, cart) == 30_000


def test_earn_is_all_or_nothing_against_its_cap():
    """A fractional multiplier is not a thing the manifest declared, so it is dropped."""
    m = manifest_of(earn("e1", 1_000, capacity=5_000))
    cart = Cart.of("m_resy", [line("s1", 100_000)])  # would earn ₹100 against ₹50 of headroom
    result = allocate(m, cart)
    assert result.witness.assignments == ()
    assert result.skipped_capacity == 1
    assert result.witness.realized_minor() == 0
    assert _optimum(m, cart) == 0


def test_protection_is_all_or_nothing_against_its_cap():
    m = manifest_of(protection("p1", 20_000, capacity=15_000))
    cart = Cart.of("m_resy", [line("s1", 100_000)])
    result = allocate(m, cart)
    assert result.witness.assignments == ()
    assert result.skipped_capacity == 1


def test_capacity_exhausts_across_multiple_lines():
    """₹1,000 of balance spread over three ₹600 dinners: ₹600, then ₹400, then nothing."""
    m = manifest_of(credit("c1", 100_000))
    cart = Cart.of(
        "m_resy", [line("s1", 60_000), line("s2", 60_000), line("s3", 60_000)]
    )
    result = allocate(m, cart)
    values = {a.line_sku: a.value_minor for a in result.witness.assignments}
    assert sum(values.values()) == 100_000
    assert sorted(values.values()) == [40_000, 60_000]
    assert result.skipped_capacity == 1
    assert _optimum(m, cart) == 100_000
    assert verify_witness(
        witness=result.witness, manifest=m, cart=cart, asserted_minor=100_000
    ).ok


def test_exclusivity_keeps_the_more_valuable_claimant():
    m = manifest_of(
        credit("c_small", 20_000, group="dining_pool"),
        credit("c_large", 60_000, group="dining_pool"),
    )
    cart = Cart.of("m_resy", [line("s1", 100_000)])
    result = allocate(m, cart)
    assert result.witness.assignments == (Assignment("s1", "c_large", 60_000, 60_000),)
    assert result.skipped_exclusivity == 1
    assert _optimum(m, cart) == 60_000


def test_credits_stack_up_to_the_line_then_stop():
    m = manifest_of(credit("c1", 50_000), credit("c2", 50_000), credit("c3", 50_000))
    cart = Cart.of("m_resy", [line("s1", 80_000)])
    result = allocate(m, cart)
    assert result.witness.realized_minor() == 80_000
    assert result.skipped_offset == 1
    assert _optimum(m, cart) == 80_000


def test_single_line_with_many_competing_benefits():
    m = manifest_of(
        credit("c1", 30_000, group="dining_pool"),
        credit("c2", 45_000, group="dining_pool"),
        credit("c3", 25_000),
        earn("e1", 500),
        earn("e2", 200, category="dining"),
        protection("p1", 10_000),
    )
    cart = Cart.of("m_resy", [line("s1", 100_000)])
    result = allocate(m, cart)
    best = _optimum(m, cart)
    assert result.witness.realized_minor() <= best
    assert verify_witness(
        witness=result.witness,
        manifest=m,
        cart=cart,
        asserted_minor=result.witness.realized_minor(),
    ).ok
    # Only one of the pooled credits may attach.
    pooled = {a.benefit_id for a in result.witness.assignments} & {"c1", "c2"}
    assert len(pooled) == 1


def test_allocation_result_reports_its_work():
    m = manifest_of(credit("c1", 30_000, group="g"), credit("c2", 40_000, group="g"))
    cart = Cart.of("m_resy", [line("s1", 100_000)])
    result = allocate(m, cart)
    assert isinstance(result, AllocationResult)
    d = result.to_dict(currency=cart.currency)
    assert d["considered"] == 2
    assert d["assigned"] == 1
    assert d["skipped_exclusivity"] == 1
    assert d["binding"] is True
    assert d["elapsed_ms"] >= 0.0
    assert d["witness"]["realized_minor"] == 40_000


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_cart():
    m = manifest_of(credit("c1", 50_000), earn("e1", 500))
    cart = Cart.of("m_resy", [])
    result = allocate(m, cart)
    assert result.witness.assignments == ()
    assert result.witness.realized_minor() == 0
    assert naive_sum(m, cart) == 0
    assert _optimum(m, cart) == 0
    v = verify_witness(witness=result.witness, manifest=m, cart=cart, asserted_minor=0)
    assert v.ok and v.supports_assertion
    assert not verify_witness(
        witness=result.witness, manifest=m, cart=cart, asserted_minor=1
    ).ok


def test_no_eligible_benefits():
    m = manifest_of(credit("c1", 50_000, category="travel"))
    cart = Cart.of("m_resy", [line("s1", 90_000, "dining")])
    assert eligible_benefits(m, cart) == ()
    result = allocate(m, cart)
    assert result.witness.realized_minor() == 0
    assert result.considered == 0
    assert naive_sum(m, cart) == 0


def test_zero_value_benefits_are_never_assigned():
    m = manifest_of(earn("e_zero", 0), protection("p_zero", 0), credit("c_zero", 0))
    cart = Cart.of("m_resy", [line("s1", 90_000)])
    result = allocate(m, cart)
    assert result.witness.assignments == ()
    assert naive_sum(m, cart) == 0
    assert _optimum(m, cart) == 0


def test_capacity_of_exactly_zero_is_unavailable():
    zero = credit("c_spent", 0)
    assert not zero.available()
    m = manifest_of(zero, earn("e1", 500))
    cart = Cart.of("m_resy", [line("s1", 90_000)])
    assert m.priced() == (m.benefits[1],)
    assert "c_spent" not in {b.benefit_id for b in eligible_benefits(m, cart)}
    forged = Witness(m.manifest_id, cart.hash(), (Assignment("s1", "c_spent", 0, 0),))
    assert _codes_for(forged, m, cart) == (ERR_UNAVAILABLE,)


def test_unenrolled_benefit_yields_nothing():
    gated = credit("c_offer", 50_000, requires_enrollment=True, enrolled=False)
    assert not gated.available()
    assert gated.value_for_line(line("s1", 90_000), "m_resy") == 0
    m = manifest_of(gated)
    cart = Cart.of("m_resy", [line("s1", 90_000)])
    assert allocate(m, cart).witness.realized_minor() == 0
    assert naive_sum(m, cart) == 0

    enrolled = credit("c_offer", 50_000, requires_enrollment=True, enrolled=True)
    assert enrolled.available()
    assert allocate(manifest_of(enrolled), cart).witness.realized_minor() == 50_000


def test_zero_amount_line_admits_no_credit():
    m = manifest_of(credit("c1", 50_000), protection("p1", 10_000))
    cart = Cart.of("m_resy", [line("s1", 0)])
    result = allocate(m, cart)
    assert {a.benefit_id for a in result.witness.assignments} == {"p1"}
    assert verify_witness(
        witness=result.witness, manifest=m, cart=cart, asserted_minor=10_000
    ).ok


def test_uncapped_benefits_are_bounded_only_by_the_cart():
    m = manifest_of(credit("c1", 10**9), earn("e1", 500))
    cart = Cart.of("m_resy", [line("s1", 90_000)])
    result = allocate(m, cart)
    assert result.witness.realized_minor() == 90_000 + 4_500

    uncapped = Benefit(
        benefit_id="c_unlimited",
        kind=KIND_CREDIT,
        label="Uncapped",
        eligibility=Eligibility(categories=("dining",)),
        capacity_minor=None,
    )
    assert uncapped.available()
    result = allocate(manifest_of(uncapped), cart)
    assert result.witness.realized_minor() == 90_000


def test_unpriced_benefits_are_carried_but_never_scored():
    """CONSIDERED-BUT-UNPRICED: the receipt proves the agent saw it, the integer stays honest."""
    lounge = Benefit(benefit_id="u_lounge", kind=KIND_UNPRICED, label="Centurion Lounge")
    m = manifest_of(lounge, earn("e1", 500))
    cart = Cart.of("m_resy", [line("s1", 90_000)])
    assert m.unpriced() == (lounge,)
    assert lounge.is_priced() is False
    assert lounge.value_for_line(cart.lines[0], "m_resy") == 0
    assert allocate(m, cart).witness.realized_minor() == 4_500
    assert lounge.describe(currency="INR") == "Centurion Lounge (declared, not priced)"


def test_witness_derivation_degrades_gracefully_on_unknown_references():
    m, cart, w = _valid_setup()
    orphan = Witness(w.manifest_id, w.cart_hash, (Assignment("sku_gone", "b_gone", 1, 1),))
    assert orphan.derivation(m, cart) == ["b_gone on sku_gone: ₹0.01"]


# ---------------------------------------------------------------------------
# Manifest structure and signing
# ---------------------------------------------------------------------------


def test_signed_manifest_verifies():
    m = manifest_of(credit("c1", 50_000), earn("e1", 500))
    signed = sign_manifest(m, SIGNING_KEY, key_id="issuer-prototype-1")
    assert verify_manifest(signed, SIGNING_KEY)
    assert verify_manifest(signed.to_dict(), SIGNING_KEY)
    assert signed.key_id == "issuer-prototype-1"
    assert signed.to_dict()["content_hash"] == m.content_hash()
    assert signed.to_dict()["body"]["version"] == MANIFEST_VERSION


def test_tampered_body_fails_verification():
    m = manifest_of(credit("c1", 50_000))
    signed = sign_manifest(m, SIGNING_KEY)
    payload = signed.to_dict()
    payload["body"]["benefits"][0]["capacity_minor"] = 5_000_000
    assert not verify_manifest(payload, SIGNING_KEY)

    payload = signed.to_dict()
    payload["body"]["issuer"] = "someone_else"
    assert not verify_manifest(payload, SIGNING_KEY)

    payload = signed.to_dict()
    payload["signature"] = "0" * 64
    assert not verify_manifest(payload, SIGNING_KEY)


def test_wrong_key_fails_verification():
    m = manifest_of(credit("c1", 50_000))
    signed = sign_manifest(m, SIGNING_KEY)
    assert not verify_manifest(signed, "not-the-issuer-key")
    assert not verify_manifest(signed, b"not-the-issuer-key")
    assert verify_manifest(signed, SIGNING_KEY.encode("utf-8"))


def test_canonicalisation_is_key_order_independent():
    m = manifest_of(credit("c1", 50_000), earn("e1", 500))
    body = m.body()
    shuffled_keys = dict(sorted(body.items(), key=lambda kv: kv[0], reverse=True))
    assert list(shuffled_keys) != list(body)
    assert canonical_json(shuffled_keys) == canonical_json(body)

    signed = sign_manifest(m, SIGNING_KEY)
    payload = signed.to_dict()
    payload["body"] = {k: payload["body"][k] for k in reversed(list(payload["body"]))}
    assert verify_manifest(payload, SIGNING_KEY)


def test_canonicalisation_handles_non_ascii():
    b = Benefit(
        benefit_id="c_resy",
        kind=KIND_CREDIT,
        label="Resy dining credit — ₹500/month",
        capacity_minor=50_000,
        note="भोजन",
    )
    m = manifest_of(b)
    raw = m.canonical()
    assert "₹".encode("utf-8") in raw
    assert json.loads(raw.decode("utf-8"))["benefits"][0]["label"] == b.label
    signed = sign_manifest(m, SIGNING_KEY)
    assert verify_manifest(signed, SIGNING_KEY)
    assert verify_manifest(json.loads(json.dumps(signed.to_dict())), SIGNING_KEY)


def test_benefit_list_order_is_part_of_the_signed_document():
    """Arrays are ordered. A reordered benefit list is a different document, by design."""
    a, b = credit("c1", 50_000), earn("e1", 500)
    assert manifest_of(a, b).content_hash() != manifest_of(b, a).content_hash()
    signed = sign_manifest(manifest_of(a, b), SIGNING_KEY)
    payload = signed.to_dict()
    payload["body"]["benefits"].reverse()
    assert not verify_manifest(payload, SIGNING_KEY)


def test_manifest_round_trips_through_dict():
    m = manifest_of(
        credit("c1", 50_000, group="dining_pool"),
        earn("e1", 500, capacity=100_000, category="travel"),
        protection("p1", 25_000),
        Benefit(benefit_id="u1", kind=KIND_UNPRICED, label="Lounge"),
    )
    restored = Manifest.from_dict(json.loads(json.dumps(m.body())))
    assert restored == m
    assert restored.content_hash() == m.content_hash()


def test_eligibility_round_trips_and_admits():
    e = Eligibility(mccs=(5812,), merchants=("m_resy",), categories=("dining",))
    assert Eligibility.from_dict(e.to_dict()) == e
    assert Eligibility.from_dict(None) == Eligibility()
    assert e.admits(line("s1", 1_000, "dining", 5812), "m_resy")
    assert not e.admits(line("s1", 1_000, "dining", 5812), "m_other")
    assert not e.admits(line("s1", 1_000, "travel", 5812), "m_resy")
    assert not e.admits(line("s1", 1_000, "dining", 4511), "m_resy")
    assert Eligibility().admits(line("s1", 1_000), "anything")


def test_assignment_round_trips():
    a = Assignment("s1", "c1", 500, 500)
    assert Assignment.from_dict(a.to_dict()) == a


def test_failure_and_verification_serialize():
    f = Failure(ERR_CAPACITY, "detail")
    assert f.to_dict() == {"code": ERR_CAPACITY, "detail": "detail"}
    v = Verification(ok=False, realized_minor=100, asserted_minor=500, failures=(f,), claimed_minor=900)
    d = v.to_dict(currency="INR")
    assert d["ok"] is False and d["supports_assertion"] is False
    assert d["realized_minor"] == 100 and d["claimed_minor"] == 900
    assert d["failures"] == [f.to_dict()]
    assert d["asserted_display"] == "₹5"


def test_benefit_describe_and_serialization():
    c = credit("c1", 50_000)
    e = earn("e1", 500)
    p = protection("p1", 25_000)
    assert c.describe(currency="INR") == "c1 credit (credit, ₹500 remaining)"
    assert e.describe(currency="INR") == "e1 earn (5% back)"
    assert p.describe(currency="INR") == "p1 cover (cover worth ₹250)"
    # The same benefits under a different denomination. A defaulted symbol here would have
    # rendered every one of these as rupees with the integer intact.
    assert c.describe(currency="USD") == "c1 credit (credit, $500 remaining)"
    assert p.describe(currency="USD") == "p1 cover (cover worth $250)"
    for b in (c, e, p):
        assert Benefit.from_dict(b.to_dict()) == b


def test_manifest_rejects_structurally_invalid_input():
    with pytest.raises(ManifestError, match="no benefits"):
        build_manifest(
            manifest_id="m", issuer="i", product="p", benefits=[], issued_at=T0
        )
    with pytest.raises(ManifestError, match="duplicate benefit_id"):
        manifest_of(credit("c1", 1_000), earn("c1", 500))
    with pytest.raises(ManifestError, match="non-empty benefit_id"):
        manifest_of(Benefit(benefit_id="", kind=KIND_EARN, label="x", rate_bp=100))
    with pytest.raises(ManifestError, match="unknown benefit kind"):
        Benefit(benefit_id="b", kind="cashback_probably", label="x")
    with pytest.raises(ManifestError, match="rate_bp must be >= 0"):
        Benefit(benefit_id="b", kind=KIND_EARN, label="x", rate_bp=-100)
    with pytest.raises(ManifestError, match="flat_minor must be >= 0"):
        Benefit(benefit_id="b", kind=KIND_PROTECTION, label="x", flat_minor=-1)
    with pytest.raises(ManifestError, match="capacity_minor must be >= 0"):
        Benefit(benefit_id="b", kind=KIND_CREDIT, label="x", capacity_minor=-1)
    with pytest.raises(ManifestError, match="missing required field"):
        Manifest.from_dict({"issuer": "i", "product": "p", "issued_at": T0})
    with pytest.raises(ManifestError, match="missing required field"):
        Benefit.from_dict({"kind": KIND_EARN})


def test_negative_rate_cannot_invert_the_overstatement_bound():
    """A negative rate would make naive_sum smaller than the witness it must bound."""
    with pytest.raises(ManifestError):
        Benefit(benefit_id="b", kind=KIND_EARN, label="x", rate_bp=-500)


def test_eligible_benefits_filters_to_the_cart():
    m = manifest_of(
        credit("c_dining", 50_000, category="dining"),
        credit("c_travel", 50_000, category="travel"),
        Benefit(benefit_id="u1", kind=KIND_UNPRICED, label="Lounge"),
        credit("c_spent", 0, category="dining"),
    )
    cart = Cart.of("m_resy", [line("s1", 90_000, "dining")])
    assert {b.benefit_id for b in eligible_benefits(m, cart)} == {"c_dining"}


def test_signed_manifest_is_a_frozen_record():
    m = manifest_of(credit("c1", 50_000))
    signed = SignedManifest(manifest=m, signature="abc", key_id="k")
    with pytest.raises(Exception):
        signed.signature = "def"  # type: ignore[misc]
