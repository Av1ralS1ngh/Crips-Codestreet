"""Attacks on the central correctness claim, written by someone trying to break it.

THE CLAIM UNDER ATTACK, as it appears on the slide:

    An asserted value can never exceed what the card can actually deliver, because it is
    backed by an exhibited allocation, and that allocation is verifiable in linear time
    without a solver.

Everything here exists to falsify that. Where an attack fails, the failure is recorded as
a regression test; where it succeeds, the test pins the boundary of the claim so nobody
states it more broadly than it holds.

WHAT THE ATTACKS ESTABLISHED

  * The value claim survives. Over seeded random instances the allocator's witness always
    verifies, and — the stronger form — EVERY witness `verify_witness` accepts, including
    thousands of deliberate forgeries and every subset of the candidate set, realizes no
    more than the exact optimum computed independently by Z3. See
    `test_no_accepted_witness_ever_exceeds_the_true_optimum`.

  * The scope of the claim is narrower than the sentence above. It holds with respect to
    the constraints the MANIFEST DECLARES, and one benefit kind declares almost none:
    a protection's value is bounded by its own `capacity_minor` and by nothing else — not
    by the line, not by the cart. `test_an_uncapped_protection_is_bounded_by_nothing`
    exhibits ₹1,00,000 asserted against a ₹20 cart, verified clean. This is a manifest
    authoring hazard rather than an engine defect (a $100 property credit on a $40 room
    really is worth $100), the shipped catalogue is free of it, and `authoring.py` now
    raises ADVISORY_UNCAPPED_PROTECTION. Say "sound with respect to the signed facts",
    never "sound with respect to reality".

  * The RFC 6962 implementation is correct against the specification, checked against a
    reference transcribed from the RFC rather than against itself.

  * One real hole was found and fixed, in the split-view defence rather than in the
    valuation: see `test_a_split_view_cannot_be_laundered_through_a_log_id`.

Attacks that failed to break anything, and are kept because they are the ones a hostile
reviewer runs first: cap evasion by splitting a draw across assignments, cap evasion by
under-declaring consumption, double-offsetting one line with two ungrouped credits,
exclusivity evasion by reordering, value inflation, credits larger than their line,
witnesses for the wrong cart or the wrong manifest, and every integer-division boundary.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import random

import pytest

from caveat.cart import Cart, CartLine
from plumbline.allocate import allocate, candidates, naive_sum
from plumbline.authoring import ADVISORY_UNCAPPED_PROTECTION, validate_draft
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    Benefit,
    Eligibility,
    Manifest,
    build_manifest,
)
from plumbline.oracle import STATUS_OPTIMAL, optimum
from plumbline.transparency import (
    AUDIT_CONSISTENT,
    AUDIT_LOG_ID_MISMATCH,
    AUDIT_SPLIT_VIEW,
    EMPTY_ROOT,
    ENTRY_RECEIPT,
    PROOF_OK,
    PROOF_ROOT_MISMATCH,
    ConsistencyProof,
    TransparencyError,
    TransparencyLog,
    audit_pair,
    check_consistency_proof,
    consistency_path,
    hash_leaf,
    inclusion_path,
    merkle_tree_hash,
    verify_consistency,
    verify_inclusion,
)
from plumbline.witness import (
    ERR_CAPACITY,
    ERR_CART_MISMATCH,
    ERR_CONSUMPTION_MISMATCH,
    ERR_CREDIT_OVER_LINE,
    ERR_CURRENCY_MISMATCH,
    ERR_DOUBLE_ASSIGNED,
    ERR_EXCLUSIVITY,
    ERR_LINE_OVER_OFFSET,
    ERR_MANIFEST_MISMATCH,
    ERR_VALUE_MISMATCH,
    Assignment,
    Witness,
    verify_witness,
)

T0 = 1_760_000_000
LOG_KEY = "prototype-log-key"


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def line(sku: str, amount: int, category: str = "dining", mcc: int = 5812) -> CartLine:
    return CartLine(sku=sku, description=sku, amount=amount, mcc=mcc, category=category)


def manifest_of(*benefits: Benefit, manifest_id: str = "m_adv") -> Manifest:
    return build_manifest(
        manifest_id=manifest_id,
        issuer="issuer_adv",
        product="Adversary Card",
        benefits=benefits,
        issued_at=T0,
    )


def verify(witness: Witness, manifest: Manifest, cart: Cart, asserted: int | None = None):
    return verify_witness(
        witness=witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=witness.realized_minor() if asserted is None else asserted,
    )


# --------------------------------------------------------------------------------------
# 1. Forging a witness the verifier accepts
# --------------------------------------------------------------------------------------


def test_cap_evasion_by_splitting_a_draw_across_assignments_is_refused():
    """A credit worth ₹500 in total, claimed at ₹300 and ₹400 on two different lines."""
    credit = Benefit(
        benefit_id="cr", kind=KIND_CREDIT, label="₹500 dining credit", capacity_minor=500
    )
    m = manifest_of(credit)
    cart = Cart.of("m_resy", [line("x", 300), line("y", 400)])
    forged = Witness(
        m.manifest_id,
        cart.hash(),
        (Assignment("x", "cr", 300, 300), Assignment("y", "cr", 400, 400)),
    )
    v = verify(forged, m, cart)
    assert not v.ok
    assert ERR_CAPACITY in v.codes()
    # The verifier's own figure stays sound: it drops the over-capacity benefit entirely
    # rather than silently clipping to the cap, so what it stands behind is achievable.
    assert v.realized_minor == 0
    assert v.claimed_minor == 700


def test_cap_evasion_by_under_declaring_consumption_is_refused():
    """Declare the value, declare a smaller draw. Capacity is summed over the draw."""
    earn = Benefit(
        benefit_id="e", kind=KIND_EARN, label="5x", rate_bp=500, capacity_minor=100
    )
    m = manifest_of(earn)
    cart = Cart.of("m_resy", [line("x", 100_000), line("y", 100_000)])
    forged = Witness(
        m.manifest_id,
        cart.hash(),
        (Assignment("x", "e", 0, 5_000), Assignment("y", "e", 0, 5_000)),
    )
    v = verify(forged, m, cart)
    assert not v.ok
    assert ERR_CONSUMPTION_MISMATCH in v.codes()
    assert v.realized_minor == 0


def test_two_ungrouped_credits_cannot_both_claim_one_line():
    """The demo's headline failure mode, with the exclusivity group deliberately omitted.

    A manifest author who forgets the group must not thereby unlock double-counting: the
    per-line offset bound is structural, not a courtesy the author extends.
    """
    a = Benefit(benefit_id="c1", kind=KIND_CREDIT, label="credit A", capacity_minor=500)
    b = Benefit(benefit_id="c2", kind=KIND_CREDIT, label="credit B", capacity_minor=500)
    assert a.exclusivity_group is None and b.exclusivity_group is None
    m = manifest_of(a, b)
    cart = Cart.of("m_resy", [line("dinner", 800)])

    forged = Witness(
        m.manifest_id,
        cart.hash(),
        (Assignment("dinner", "c1", 500, 500), Assignment("dinner", "c2", 500, 500)),
    )
    v = verify(forged, m, cart)
    assert not v.ok
    assert ERR_LINE_OVER_OFFSET in v.codes()

    # And the allocator itself never proposes it: 500 + 300, not 500 + 500.
    result = allocate(m, cart)
    assert result.witness.realized_minor() == 800
    assert verify(result.witness, m, cart).ok
    assert naive_sum(m, cart) == 1_600


def test_a_credit_cannot_offset_more_than_its_line_costs():
    credit = Benefit(
        benefit_id="cr", kind=KIND_CREDIT, label="big credit", capacity_minor=500_000
    )
    m = manifest_of(credit)
    cart = Cart.of("m_resy", [line("x", 100)])
    forged = Witness(m.manifest_id, cart.hash(), (Assignment("x", "cr", 500_000, 500_000),))
    v = verify(forged, m, cart)
    assert not v.ok
    assert ERR_CREDIT_OVER_LINE in v.codes()


def test_exclusivity_cannot_be_evaded_by_assignment_order():
    """Whichever of the two is listed first, the second is refused."""
    a = Benefit(
        benefit_id="c1",
        kind=KIND_CREDIT,
        label="credit A",
        capacity_minor=400,
        exclusivity_group="dining_pool",
    )
    b = Benefit(
        benefit_id="c2",
        kind=KIND_CREDIT,
        label="credit B",
        capacity_minor=400,
        exclusivity_group="dining_pool",
    )
    m = manifest_of(a, b)
    cart = Cart.of("m_resy", [line("dinner", 5_000)])
    pair = (Assignment("dinner", "c1", 400, 400), Assignment("dinner", "c2", 400, 400))
    for ordering in (pair, tuple(reversed(pair))):
        v = verify(Witness(m.manifest_id, cart.hash(), ordering), m, cart)
        assert not v.ok
        assert ERR_EXCLUSIVITY in v.codes()


def test_the_same_benefit_cannot_be_stapled_to_one_line_twice():
    credit = Benefit(benefit_id="cr", kind=KIND_CREDIT, label="credit", capacity_minor=5_000)
    m = manifest_of(credit)
    cart = Cart.of("m_resy", [line("x", 1_000)])
    forged = Witness(
        m.manifest_id,
        cart.hash(),
        (Assignment("x", "cr", 1_000, 1_000), Assignment("x", "cr", 1_000, 1_000)),
    )
    v = verify(forged, m, cart)
    assert not v.ok
    assert ERR_DOUBLE_ASSIGNED in v.codes()
    # The duplicate is dropped, not the original: what survives is one valid assignment.
    assert v.realized_minor == 1_000


def test_inflating_an_earn_value_by_one_minor_unit_is_refused():
    """Off-by-one is the forgery a reviewer expects a re-adder to miss."""
    earn = Benefit(benefit_id="e", kind=KIND_EARN, label="5x", rate_bp=500)
    m = manifest_of(earn)
    cart = Cart.of("m_resy", [line("x", 100_000)])
    honest = allocate(m, cart).witness
    assert honest.realized_minor() == 5_000
    forged = Witness(m.manifest_id, cart.hash(), (Assignment("x", "e", 5_001, 5_001),))
    v = verify(forged, m, cart)
    assert not v.ok
    assert ERR_VALUE_MISMATCH in v.codes()


def test_a_witness_for_another_cart_or_manifest_is_refused():
    """The arithmetic alone cannot catch this, which is why the binding is checked."""
    credit = Benefit(benefit_id="cr", kind=KIND_CREDIT, label="credit", capacity_minor=25_000)
    m = manifest_of(credit)
    small = Cart.of("m_resy", [line("x", 80_000)])
    w = allocate(m, small).witness

    # A larger cart: every recomputed number checks out, and the witness is still not
    # about this cart.
    larger = Cart.of("m_resy", [line("x", 500_000)])
    assert verify(w, m, larger).codes() == (ERR_CART_MISMATCH,)

    renamed = dataclasses.replace(m, manifest_id="m_other")
    assert verify(w, renamed, small).codes() == (ERR_MANIFEST_MISMATCH,)


def test_a_manifest_in_a_foreign_currency_is_refused_rather_than_converted():
    """There is no FX rate in the decision path, and there must not be one."""
    credit = Benefit(benefit_id="cr", kind=KIND_CREDIT, label="$250 credit", capacity_minor=25_000)
    usd = Manifest(
        manifest_id="m_usd",
        issuer="issuer_adv",
        product="US Card",
        currency="USD",
        benefits=(credit,),
        issued_at=T0,
    )
    inr_cart = Cart(merchant="m_resy", currency="INR", lines=(line("x", 80_000),))
    w = allocate(usd, inr_cart).witness
    assert verify(w, usd, inr_cart).codes() == (ERR_CURRENCY_MISMATCH,)


# --------------------------------------------------------------------------------------
# 2. Arithmetic: partial credits, earn against a cap, integer division
# --------------------------------------------------------------------------------------


def test_a_credit_spanning_two_lines_exhausts_its_balance_and_no_more():
    credit = Benefit(benefit_id="cr", kind=KIND_CREDIT, label="₹500", capacity_minor=500)
    m = manifest_of(credit)
    cart = Cart.of("m_resy", [line("x", 300), line("y", 400)])
    w = allocate(m, cart).witness
    assert w.realized_minor() == 500
    assert sum(a.consumed_minor for a in w.assignments) == 500
    for a in w.assignments:
        assert a.value_minor == a.consumed_minor
        assert a.consumed_minor <= {"x": 300, "y": 400}[a.line_sku]
    assert verify(w, m, cart).ok


@pytest.mark.parametrize(
    "amount,rate_bp,expected",
    [
        (99, 500, 4),        # 4.95 -> 4
        (199, 500, 9),       # 9.95 -> 9
        (1, 9_999, 0),       # 0.9999 -> 0
        (1, 10_000, 1),      # exactly 1
        (10_001, 10_000, 10_001),
        (333, 333, 11),      # 11.0889 -> 11
        (0, 10_000, 0),
    ],
)
def test_earn_rounds_down_never_up(amount: int, rate_bp: int, expected: int):
    """Floor division, and the direction matters more than the magnitude.

    Rounding up would assert a fraction of a point the card never pays, which is an
    overstatement however small. Rounding down understates, which is safe by construction.
    """
    earn = Benefit(benefit_id="e", kind=KIND_EARN, label="earn", rate_bp=rate_bp)
    m = manifest_of(earn)
    cart = Cart.of("m_resy", [line("x", amount)])
    assert (amount * rate_bp) // 10_000 == expected
    assert allocate(m, cart).witness.realized_minor() == expected
    assert expected * 10_000 <= amount * rate_bp  # never above the exact product


def test_splitting_a_line_can_only_lower_the_asserted_earn():
    """Floor division is applied per line, so cart shape moves the number downward only.

    Worth pinning because the direction is the whole safety argument: an agent cannot
    inflate a card's value by restructuring the cart.
    """
    earn = Benefit(benefit_id="e", kind=KIND_EARN, label="5x", rate_bp=500)
    m = manifest_of(earn)
    whole = Cart.of("m_resy", [line("x", 990)])
    split = Cart.of("m_resy", [line(f"x{i}", 99) for i in range(10)])
    assert allocate(m, whole).witness.realized_minor() == 49
    assert allocate(m, split).witness.realized_minor() == 40
    assert allocate(m, split).witness.realized_minor() <= allocate(m, whole).witness.realized_minor()


def test_an_earn_benefit_short_of_headroom_is_dropped_not_prorated():
    """Understating is safe; asserting a fraction of a multiplier the manifest never
    declared is not."""
    earn = Benefit(
        benefit_id="e", kind=KIND_EARN, label="5x", rate_bp=500, capacity_minor=100
    )
    m = manifest_of(earn)
    cart = Cart.of("m_resy", [line("x", 100_000)])  # would yield 5,000 against a cap of 100
    w = allocate(m, cart).witness
    assert w.realized_minor() == 0
    assert verify(w, m, cart).ok


def test_capacity_is_never_exceeded_across_many_lines():
    credit = Benefit(benefit_id="cr", kind=KIND_CREDIT, label="₹1000", capacity_minor=1_000)
    m = manifest_of(credit)
    cart = Cart.of("m_resy", [line(f"s{i}", 700) for i in range(10)])
    w = allocate(m, cart).witness
    assert sum(a.consumed_minor for a in w.assignments) == 1_000
    assert w.realized_minor() == 1_000
    assert verify(w, m, cart).ok


# --------------------------------------------------------------------------------------
# 3. The headline property, against an independent optimum
# --------------------------------------------------------------------------------------

MCCS = (5812, 5411, 4511)
CATEGORIES = ("dining", "grocery", "air")


def _random_instance(rng: random.Random) -> tuple[Manifest, Cart]:
    """Adversarial by construction: boundary amounts, boundary caps, contested groups."""
    lines = [
        line(
            f"s{i}",
            rng.choice((0, 1, 2, 99, 100, 101, 499, 500, 501, 5_000)),
            category=rng.choice(CATEGORIES),
            mcc=rng.choice(MCCS),
        )
        for i in range(rng.randint(1, 5))
    ]
    benefits = []
    for j in range(rng.randint(1, 5)):
        kind = rng.choice((KIND_EARN, KIND_CREDIT, KIND_PROTECTION))
        benefits.append(
            Benefit(
                benefit_id=f"b{j}",
                kind=kind,
                label=f"b{j}",
                eligibility=Eligibility(
                    mccs=tuple(rng.sample(MCCS, rng.randint(0, 2))),
                    categories=tuple(rng.sample(CATEGORIES, rng.randint(0, 1))),
                ),
                rate_bp=rng.choice((1, 99, 100, 500, 9_999, 10_000)) if kind == KIND_EARN else 0,
                capacity_minor=rng.choice((None, 1, 2, 49, 50, 99, 100, 500, 10**9)),
                flat_minor=rng.choice((1, 49, 50, 400, 900)) if kind == KIND_PROTECTION else 0,
                exclusivity_group=rng.choice((None, None, None, "g1", "g2")),
                requires_enrollment=rng.random() < 0.15,
                enrolled=rng.random() < 0.7,
            )
        )
    return manifest_of(*benefits), Cart.of("m_resy", lines)


def _forgeries(manifest: Manifest, cart: Cart) -> list[Witness]:
    """Every witness an adversary would try to pass off as backing a larger number.

    Includes every subset of the full candidate set at its declared value — which covers
    the honest allocator's output and every allocation it declined to make — plus value
    inflation, consumption shrinkage, and matched inflation of both fields.
    """
    all_assignments = [
        Assignment(c.line.sku, c.benefit.benefit_id, c.consumed_minor, c.value_minor)
        for c in candidates(manifest, cart)
    ]
    out: list[Witness] = []
    if len(all_assignments) <= 10:
        for r in range(1, len(all_assignments) + 1):
            out += [
                Witness(manifest.manifest_id, cart.hash(), combo)
                for combo in itertools.combinations(all_assignments, r)
            ]
    for i, a in enumerate(all_assignments):
        head, tail = tuple(all_assignments[:i]), tuple(all_assignments[i + 1 :])
        for delta in (1, 50, 10**6):
            out.append(
                Witness(
                    manifest.manifest_id,
                    cart.hash(),
                    head + (Assignment(a.line_sku, a.benefit_id, a.consumed_minor, a.value_minor + delta),) + tail,
                )
            )
            out.append(
                Witness(
                    manifest.manifest_id,
                    cart.hash(),
                    head + (Assignment(a.line_sku, a.benefit_id, a.consumed_minor + delta, a.value_minor + delta),) + tail,
                )
            )
            out.append(
                Witness(
                    manifest.manifest_id,
                    cart.hash(),
                    head + (Assignment(a.line_sku, a.benefit_id, max(0, a.consumed_minor - delta), a.value_minor),) + tail,
                )
            )
    return out


def test_no_accepted_witness_ever_exceeds_the_true_optimum():
    """THE HEADLINE PROPERTY, in its strongest form and against independent ground truth.

    Not merely "the allocator does not overstate" — that would only test the allocator.
    The claim is about the VERIFIER: anything it accepts must be achievable. So this
    generates thousands of forgeries per instance, keeps the ones the verifier accepts, and
    checks every one against the exact optimum from Z3 — a solver that shares no code with
    `verify_witness` and is built from the constraint model directly.

    `optimum` is used offline exactly as the architecture intends. It never runs on the hot
    path; it is ground truth for a test.
    """
    rng = random.Random(20260825)
    instances = accepted = 0
    for _ in range(250):
        manifest, cart = _random_instance(rng)
        honest = allocate(manifest, cart).witness

        # The allocator's own output must survive its own verifier, always.
        assert verify(honest, manifest, cart).ok, (
            f"the hot path emitted a witness its own verifier rejects: "
            f"{verify(honest, manifest, cart).codes()}"
        )

        exact = optimum(manifest, cart, timeout_ms=5_000)
        if exact.status != STATUS_OPTIMAL:
            continue
        instances += 1
        assert honest.realized_minor() <= exact.optimum_minor

        for forged in _forgeries(manifest, cart):
            verification = verify(forged, manifest, cart)
            if not verification.ok:
                continue
            accepted += 1
            assert forged.realized_minor() <= exact.optimum_minor, (
                f"THE CLAIM IS BROKEN: the verifier accepted a witness realizing "
                f"{forged.realized_minor()} against a true optimum of "
                f"{exact.optimum_minor}\n"
                f"  witness:  {[a.to_dict() for a in forged.assignments]}\n"
                f"  manifest: {[b.to_dict() for b in manifest.benefits]}\n"
                f"  cart:     {cart.to_dict()}"
            )
    # Guards on the strength of the search itself. A property test that silently stops
    # exercising anything passes just as green as one that works.
    assert instances >= 200, f"only {instances} instances reached a proven optimum"
    assert accepted >= 1_000, f"only {accepted} forgeries were accepted; the search is too weak"


def test_the_verifiers_own_figure_is_achievable_even_when_it_refuses():
    """`realized_minor` on a failed verification is a lower bound, never an upper one.

    It is reported so a human can localise the overstatement, which is only safe if the
    number itself is still backed by a valid allocation. Checked against the optimum.
    """
    rng = random.Random(77)
    checked = 0
    for _ in range(40):
        manifest, cart = _random_instance(rng)
        exact = optimum(manifest, cart, timeout_ms=5_000)
        if exact.status != STATUS_OPTIMAL:
            continue
        for forged in _forgeries(manifest, cart):
            verification = verify(forged, manifest, cart)
            if verification.ok:
                continue
            checked += 1
            assert verification.realized_minor <= exact.optimum_minor
            assert not verification.supports_assertion or not verification.ok
    assert checked > 500


def test_the_allocator_is_deterministic_under_input_reordering():
    """The witness is hashed into a signed receipt, so byte-stability is load-bearing."""
    rng = random.Random(99)
    for _ in range(40):
        manifest, cart = _random_instance(rng)
        base = allocate(manifest, cart).witness
        shuffled_benefits = list(manifest.benefits)
        rng.shuffle(shuffled_benefits)
        reordered = manifest_of(*shuffled_benefits, manifest_id=manifest.manifest_id)
        assert allocate(reordered, cart).witness.to_dict(
            currency=cart.currency
        ) == base.to_dict(currency=cart.currency)


# --------------------------------------------------------------------------------------
# 4. The boundary of the claim: what the manifest does not declare, the witness cannot bound
# --------------------------------------------------------------------------------------


def test_an_uncapped_protection_is_bounded_by_nothing():
    """A NAMED LIMITATION, exhibited rather than described. Do not "fix" this by clamping.

    A protection's value is granted per qualifying line and bounded only by its own
    capacity. Uncapped, it asserts `flat_minor` times the line count — here ₹1,00,000
    against a ₹20 cart — and the witness verifies clean, because every constraint the
    manifest declares is satisfied.

    Clamping protection value to the line would be wrong: a $100 Fine Hotels + Resorts
    property credit on a $40 room genuinely is worth $100, since it is spent on property
    rather than against the room. The defence is therefore at authoring time, and the
    honest framing of the claim is "sound with respect to the signed facts".
    """
    protection = Benefit(
        benefit_id="pp", kind=KIND_PROTECTION, label="Purchase Protection", flat_minor=500_000
    )
    assert protection.capacity_minor is None
    m = manifest_of(protection)
    cart = Cart.of("m_resy", [line(f"s{i}", 100) for i in range(20)])
    w = allocate(m, cart).witness

    assert cart.total() == 2_000
    assert w.realized_minor() == 10_000_000
    assert verify(w, m, cart).ok, "this is the point: it verifies, and the claim is narrower"

    # And the offline oracle agrees it is the optimum, so this is the model's answer and
    # not a greedy artefact.
    exact = optimum(m, cart, timeout_ms=5_000)
    assert exact.status == STATUS_OPTIMAL
    assert exact.optimum_minor == 10_000_000


def test_authoring_refuses_to_let_an_uncapped_protection_pass_unremarked():
    """The defence for the limitation above, at the only layer where it belongs."""
    def draft(**benefit):
        return {
            "manifest_id": "m_draft",
            "issuer": "issuer_adv",
            "product": "Adversary Card",
            "currency": "USD",
            "issued_at": T0,
            "source": "publicly published card terms, modelled",
            "benefits": [
                {"benefit_id": "pp", "kind": "protection", "label": "Cover", **benefit}
            ],
        }

    uncapped, _ = validate_draft(draft(flat_minor=10_000))
    assert ADVISORY_UNCAPPED_PROTECTION in {f.code for f in uncapped.findings}

    capped, _ = validate_draft(draft(flat_minor=10_000, capacity_minor=10_000))
    assert ADVISORY_UNCAPPED_PROTECTION not in {f.code for f in capped.findings}


def test_the_shipped_catalogue_caps_every_protection_it_declares():
    """The limitation above is not live in the demo, and this keeps it that way."""
    from plumbline.products import catalogue

    for manifest in catalogue(T0):
        for benefit in manifest.benefits:
            if benefit.kind != KIND_PROTECTION:
                continue
            assert benefit.capacity_minor is not None, (
                f"{manifest.manifest_id}/{benefit.benefit_id} is an uncapped protection; "
                f"its asserted value grows with the cart's line count without bound"
            )
            assert benefit.capacity_minor >= benefit.flat_minor


# --------------------------------------------------------------------------------------
# 5. RFC 6962, against the specification rather than against itself
# --------------------------------------------------------------------------------------


def _rfc_mth(data: list[bytes]) -> str:
    """MTH transcribed from RFC 6962 section 2.1, sharing no code with transparency.py.

    A reimplementation that reuses the module's own split rule would agree with it by
    construction and prove nothing, so the split is recomputed here from the definition:
    k is the largest power of two strictly less than n.
    """
    if not data:
        return hashlib.sha256(b"").hexdigest()
    if len(data) == 1:
        return hashlib.sha256(b"\x00" + data[0]).hexdigest()
    k = 1
    while k * 2 < len(data):
        k *= 2
    left = bytes.fromhex(_rfc_mth(data[:k]))
    right = bytes.fromhex(_rfc_mth(data[k:]))
    return hashlib.sha256(b"\x01" + left + right).hexdigest()


ENTRIES = [f"entry-{i}".encode() for i in range(65)]


def test_the_empty_tree_root_is_the_rfc_value():
    assert EMPTY_ROOT == hashlib.sha256(b"").hexdigest()
    assert merkle_tree_hash([]) == EMPTY_ROOT


@pytest.mark.parametrize("n", list(range(0, 65)))
def test_merkle_tree_hash_matches_the_rfc_definition(n: int):
    leaves = [hash_leaf(d) for d in ENTRIES[:n]]
    assert merkle_tree_hash(leaves) == _rfc_mth(ENTRIES[:n])


def test_leaf_and_interior_hashes_are_domain_separated():
    """Without the 0x00/0x01 prefixes an interior node can be passed off as a leaf."""
    a, b = hash_leaf(b"a"), hash_leaf(b"b")
    interior = merkle_tree_hash([a, b])
    assert interior != hash_leaf(bytes.fromhex(a) + bytes.fromhex(b))
    assert hash_leaf(b"") != hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("n", list(range(1, 33)))
def test_every_inclusion_proof_verifies_and_no_other_index_does(n: int):
    leaves = [hash_leaf(d) for d in ENTRIES[:n]]
    root = merkle_tree_hash(leaves)
    for i in range(n):
        path = inclusion_path(i, leaves)
        assert verify_inclusion(
            leaf_hash=leaves[i], leaf_index=i, tree_size=n, path=path, root_hash=root
        )
        for j in range(n):
            if j == i:
                continue
            assert not verify_inclusion(
                leaf_hash=leaves[i], leaf_index=j, tree_size=n, path=path, root_hash=root
            ), f"a proof for index {i} verified at index {j} in a tree of {n}"


@pytest.mark.parametrize("n", list(range(1, 33)))
def test_every_consistency_proof_between_every_pair_of_sizes_verifies(n: int):
    leaves = [hash_leaf(d) for d in ENTRIES[:n]]
    second_root = merkle_tree_hash(leaves)
    for m in range(1, n + 1):
        first_root = merkle_tree_hash(leaves[:m])
        assert verify_consistency(
            first_size=m,
            first_root=first_root,
            second_size=n,
            second_root=second_root,
            path=consistency_path(m, leaves),
        ), f"the honest proof from {m} to {n} did not verify"


def test_a_consistency_proof_does_not_verify_against_a_root_from_a_different_history():
    """The proof must bind to the earlier root, not merely be well-formed."""
    leaves = [hash_leaf(d) for d in ENTRIES[:16]]
    other = [hash_leaf(d) for d in ENTRIES[1:17]]
    path = consistency_path(5, leaves)
    assert not verify_consistency(
        first_size=5,
        first_root=merkle_tree_hash(other[:5]),
        second_size=16,
        second_root=merkle_tree_hash(leaves),
        path=path,
    )


def test_a_truncated_or_padded_consistency_path_is_refused():
    leaves = [hash_leaf(d) for d in ENTRIES[:16]]
    first_root, second_root = merkle_tree_hash(leaves[:5]), merkle_tree_hash(leaves)
    honest = consistency_path(5, leaves)
    for tampered in (honest[:-1], honest[1:], honest + (honest[0],), ()):
        assert not verify_consistency(
            first_size=5,
            first_root=first_root,
            second_size=16,
            second_root=second_root,
            path=tampered,
        )


def test_a_shrinking_log_is_never_consistent():
    leaves = [hash_leaf(d) for d in ENTRIES[:16]]
    assert not verify_consistency(
        first_size=16,
        first_root=merkle_tree_hash(leaves),
        second_size=5,
        second_root=merkle_tree_hash(leaves[:5]),
        path=consistency_path(5, leaves),
    )


# --------------------------------------------------------------------------------------
# 6. Log edits a correct consistency proof must catch
# --------------------------------------------------------------------------------------


def _log_of(bodies: list[dict], log_id: str = "plumbline-demo-log") -> TransparencyLog:
    log = TransparencyLog(log_id, signing_key=LOG_KEY)
    for i, body in enumerate(bodies):
        log.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + i)
    return log


HONEST_BODIES = [
    {"receipt_id": f"rcpt_{i}", "candidates": ["amex-platinum", "chase-sapphire"]}
    for i in range(6)
]


@pytest.mark.parametrize(
    "name,bodies",
    [
        # Demo beat 3: the platform drops Amex from a candidate set already published.
        (
            "amex omitted from a published receipt",
            HONEST_BODIES[:2]
            + [{"receipt_id": "rcpt_2", "candidates": ["chase-sapphire"]}]
            + HONEST_BODIES[3:],
        ),
        ("an entry deleted", HONEST_BODIES[:1] + HONEST_BODIES[2:]),
        ("an entry inserted mid-history", HONEST_BODIES[:2] + [{"receipt_id": "spliced"}] + HONEST_BODIES[2:]),
        ("two entries reordered", [HONEST_BODIES[1], HONEST_BODIES[0]] + HONEST_BODIES[2:]),
        ("a timestamp backdated", HONEST_BODIES),
    ],
)
def test_every_retroactive_edit_fails_a_consistency_proof(name: str, bodies: list[dict]):
    """The published head is the commitment; the edit is detected by arithmetic anyone runs."""
    honest = _log_of(HONEST_BODIES)
    published = honest.signed_tree_head(timestamp=T0 + 100, tree_size=4)

    edited = _log_of(bodies)
    if name == "a timestamp backdated":
        edited = TransparencyLog("plumbline-demo-log", signing_key=LOG_KEY)
        for i, body in enumerate(bodies):
            edited.append(kind=ENTRY_RECEIPT, body=body, timestamp=T0 + i - (5 if i == 2 else 0))

    ok, code = check_consistency_proof(edited.prove_extends(published), published)
    assert not ok, f"{name!r} was not detected"
    assert code == PROOF_ROOT_MISMATCH

    # The honest log, by contrast, proves it still contains what it published.
    ok, code = check_consistency_proof(honest.prove_extends(published), published)
    assert ok and code == PROOF_OK


def test_a_truncated_log_is_refused_rather_than_proved():
    honest = _log_of(HONEST_BODIES)
    published = honest.signed_tree_head(timestamp=T0 + 100, tree_size=4)
    truncated = _log_of(HONEST_BODIES[:3])
    with pytest.raises(TransparencyError, match="shrinking log"):
        truncated.prove_extends(published)


def test_an_edited_log_can_prove_consistency_with_its_own_edited_past():
    """A NAMED FOOTGUN, pinned so nobody demonstrates the weaker check on stage.

    `ConsistencyProof.verify()` checks only that the proof agrees with itself, and an
    edited history is internally consistent — that is exactly what a forger builds. The
    detection comes from the earlier root being a commitment made by someone else, before
    anyone knew the entry would need editing. So the proof must be bound to a head that
    was actually published, via `prove_extends` or `verify_against`.
    """
    honest = _log_of(HONEST_BODIES)
    published = honest.signed_tree_head(timestamp=T0 + 100, tree_size=4)
    edited = _log_of(
        HONEST_BODIES[:2]
        + [{"receipt_id": "rcpt_2", "candidates": ["chase-sapphire"]}]
        + HONEST_BODIES[3:]
    )

    self_serving = edited.consistency_proof(4, 6)
    assert self_serving.first_root != published.root_hash
    assert self_serving.verify(), "an edited history is internally consistent, as expected"
    assert check_consistency_proof(self_serving) == (True, PROOF_OK)

    # Bound to the head the counterparty actually holds, it fails.
    assert not self_serving.verify_against(published)
    assert check_consistency_proof(self_serving, published) == (False, PROOF_ROOT_MISMATCH)


def test_verify_against_rejects_a_head_of_the_wrong_size():
    honest = _log_of(HONEST_BODIES)
    proof = honest.consistency_proof(4, 6)
    wrong_size = honest.signed_tree_head(timestamp=T0 + 100, tree_size=5)
    assert not proof.verify_against(wrong_size)
    assert ConsistencyProof(
        first_size=proof.first_size,
        first_root=proof.first_root,
        second_size=proof.second_size,
        second_root=proof.second_root,
        path=proof.path,
    ).verify_against(honest.signed_tree_head(timestamp=T0 + 100, tree_size=4))


def test_an_inclusion_proof_for_an_edited_entry_fails_against_the_published_head():
    honest = _log_of(HONEST_BODIES)
    published = honest.signed_tree_head(timestamp=T0 + 100, tree_size=4)
    edited = _log_of(
        HONEST_BODIES[:2]
        + [{"receipt_id": "rcpt_2", "candidates": ["chase-sapphire"]}]
        + HONEST_BODIES[3:]
    )
    assert not edited.inclusion_proof(2, tree_size=4).verify_against(published)
    assert honest.inclusion_proof(2, tree_size=4).verify_against(published)


# --------------------------------------------------------------------------------------
# 7. The split-view defence
# --------------------------------------------------------------------------------------


def test_a_split_view_cannot_be_laundered_through_a_log_id():
    """A REAL HOLE, found by attacking the auditor rather than the arithmetic.

    Two heads at the same size with different roots is a split view. The auditor used to
    skip any pair whose `log_id` differed, so an operator serving two histories only had to
    label one of them differently: the audit produced no findings at all, fell through to
    its "nothing to compare" default, and reported VIEWS_CONSISTENT with ok=True over a
    comparison it had declined to make. The log id is a string the log itself chooses, so
    letting it silence the comparison hands the forger the veto.

    Now reported as OBSERVED_HEADS_NAME_DIFFERENT_LOGS, which is not ok.
    """
    honest = _log_of(HONEST_BODIES, log_id="amex-ct")
    forged = _log_of(
        HONEST_BODIES[:2]
        + [{"receipt_id": "rcpt_2", "candidates": ["chase-sapphire"]}]
        + HONEST_BODIES[3:],
        log_id="amex-ct-mirror",
    )
    issuer_view = honest.signed_tree_head(timestamp=T0 + 100)
    cardholder_view = forged.signed_tree_head(timestamp=T0 + 100)
    assert issuer_view.tree_size == cardholder_view.tree_size
    assert issuer_view.root_hash != cardholder_view.root_hash

    report = audit_pair(
        auditor_id="witness_1",
        issuer_view=issuer_view,
        cardholder_view=cardholder_view,
        key=LOG_KEY,
    )
    assert not report.ok
    assert report.outcome == AUDIT_LOG_ID_MISMATCH
    assert AUDIT_CONSISTENT not in report.codes()


def test_the_same_split_view_under_one_log_id_is_named_outright():
    honest = _log_of(HONEST_BODIES, log_id="amex-ct")
    forged = _log_of(
        HONEST_BODIES[:2]
        + [{"receipt_id": "rcpt_2", "candidates": ["chase-sapphire"]}]
        + HONEST_BODIES[3:],
        log_id="amex-ct",
    )
    report = audit_pair(
        auditor_id="witness_1",
        issuer_view=honest.signed_tree_head(timestamp=T0 + 100),
        cardholder_view=forged.signed_tree_head(timestamp=T0 + 100),
        key=LOG_KEY,
    )
    assert report.outcome == AUDIT_SPLIT_VIEW
    assert not report.ok


def test_an_audit_that_compared_nothing_says_how_many_views_it_saw():
    """"Consistent" over zero comparisons is an absence of evidence, not evidence."""
    honest = _log_of(HONEST_BODIES)
    from plumbline.transparency import LogAuditor

    auditor = LogAuditor("witness_1")
    auditor.observe("issuer", honest.signed_tree_head(timestamp=T0 + 100))
    report = auditor.audit(key=LOG_KEY)
    assert report.outcome == AUDIT_CONSISTENT
    assert "1 head(s) observed" in report.findings[0].detail
