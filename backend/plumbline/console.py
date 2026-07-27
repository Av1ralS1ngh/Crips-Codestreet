"""The console corpus: one envelope, built once here, served two ways.

`GET /api/plumbline/state` returns `build_state()`. `frontend/scripts/gen_plumbline_fixtures.py`
writes `build_state()` to `src/mock/plumblineFixtures.json`. There is one builder and both
transports call it, so the mock the console renders by default and the payload a live
backend serves cannot disagree about a card, a rate, a witness or a root — they are the
same bytes from the same function at the same fixed clock. That property is asserted by
test, not assumed: `test_plumbline_console_state.py` diffs the checked-in fixture against a
live response.

The manifests come from `products.py` and nowhere else. That module's numbers were read off
issuer fact sheets and each one carries a comment naming what it was read off. Any manifest
built here instead of there would be a card term with no provenance, rendered next to a real
issuer's name — which is the single failure this file exists to prevent.

One instrument is NOT from `products.py`, and it is labelled as invented everywhere it
appears:

  * `illustrative_reserve()` is a hypothetical. No such product exists, no issuer signed it,
    and its `source` says so in the first four words. It is here because the published
    Indian catalogue contains no statement credits, so the case CLAUDE.md's first demo beat
    turns on — two credits competing for one line — cannot be shown from real Indian terms.
    The alternative was to attach invented credits to a real issuer's product name, and that
    is the one thing this system may never do.

  * It is also the only instrument on which the perturbation screen may move a RATE. A
    balance is member state and two Card Members hold the same product with different
    balances; a rate is a product term, and a rate printed beside a real product's name is
    a claim about that product.

Money is integer minor units throughout. The clock is a constant, so two runs are
byte-identical apart from `valuation.latency`, which is measured on the host that served it
and is the only field in the envelope that does not replay.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import statistics
import time
from dataclasses import dataclass, replace
from typing import Any, Sequence

from caveat.cart import Cart, CartLine
from caveat.ledger import MerkleLedger, _node_hash

from . import products as P
from .allocate import allocate, naive_sum
from .manifest import (
    KIND_CREDIT,
    KIND_EARN,
    WINDOW_ANNUAL,
    WINDOW_MONTHLY,
    Benefit,
    Eligibility,
    Manifest,
    build_manifest,
    canonical_json,
    sign_manifest,
)
from .witness import Assignment, Witness, verify_witness

# --------------------------------------------------------------------------------------
# Fixed inputs. Chosen, not sampled — a rehearsal and the live run must produce identical
# bytes, and a wall-clock read anywhere below would end that.
# --------------------------------------------------------------------------------------

# 2026-07-27T00:00:00Z, the same constant `scenarios.DEMO_CLOCK` uses, so the five scenario
# routes and this envelope describe the same day's member state.
CONSOLE_CLOCK = 1_785_110_400

CURRENCY = P.INR

# Prototype keys. Production signs manifests with each issuer's HSM key and tree heads with
# the log operator's; canonicalisation and every verification flow are unchanged by the
# substitution, which is the part the argument rests on.
ISSUER_KEY = "plumbline-console-issuer-key-not-an-hsm"
ISSUER_KEY_ID = "plumbline-console-issuer"
LOG_KEY = "plumbline-console-log-key-not-an-hsm"
LOG_KEY_ID = "plumbline-console-log"

# Reason codes. Closed vocabulary, mirrored into src/lib/plumbline.ts.
REFUSED_NO_WITNESS = "VALUATION_REFUSED_NO_SUPPORTING_WITNESS"

CAUSE_ASSIGNED = "ASSIGNED"
CAUSE_EXCLUSIVITY = "STRUCK_EXCLUSIVITY_GROUP"
CAUSE_BALANCE = "OVER_REMAINING_BALANCE"
CAUSE_EXHAUSTED = "BALANCE_ALREADY_CONSUMED"

QUADRANT_LOAD_BEARING = "LOAD_BEARING"
QUADRANT_DEAD_WEIGHT = "DEAD_WEIGHT"
QUADRANT_OPTION = "OPTION"
QUADRANT_NOISE = "NOISE"

MCC_HOTEL = 7011
MCC_AIRLINE = 3000
MCC_DINING = 5812
MCC_FUEL = 5541
MCC_ELECTRONICS = 5732


def money(minor: int) -> str:
    return P.fmt_currency(minor, CURRENCY)


# --------------------------------------------------------------------------------------
# The cart. Channel-neutral by construction: everything is booked direct with the airline,
# the property or the merchant, so no card's own travel portal is favoured and the
# comparison turns on published rates rather than on which portal the cart happened to
# name. A cart routed through one issuer's booking site is also a cart the other issuers
# cannot pay at all, which would make the candidate set a fiction.
# --------------------------------------------------------------------------------------

CART = Cart(
    merchant="m_travel_desk_in",
    currency=CURRENCY,
    lines=(
        CartLine(
            sku="in_hotel",
            description="Marina Bay hotel, 3 nights, booked direct",
            amount=2_840_000,
            mcc=MCC_HOTEL,
            category=P.CAT_OVERSEAS,
            qty=3,
        ),
        # A second stay, so a benefit whose whole balance the first stay consumed can be seen
        # yielding nothing on the second. That is the one overstatement cause no per-line
        # implementation can detect, because it is not a property of either line alone.
        CartLine(
            sku="in_hotel_transit",
            description="Airport transit hotel, 1 night, booked direct",
            amount=620_000,
            mcc=MCC_HOTEL,
            category=P.CAT_HOTEL_DIRECT,
            qty=1,
        ),
        CartLine(
            sku="in_flight",
            description="BLR–SIN return, booked direct with the airline",
            amount=1_420_000,
            mcc=MCC_AIRLINE,
            category=P.CAT_AIRFARE,
            qty=2,
        ),
        CartLine(
            sku="in_dining",
            description="Dinner for four, Marina Bay",
            amount=168_400,
            mcc=MCC_DINING,
            category=P.CAT_OVERSEAS,
            qty=1,
        ),
        # Fuel is the one line on this cart that only one instrument's base earn admits.
        # Every other candidate excludes it, which is what makes it the line the attribution
        # screen ends up calling decisive.
        CartLine(
            sku="in_fuel",
            description="Fuel, full tank before the airport drive",
            amount=480_000,
            mcc=MCC_FUEL,
            category=P.CAT_FUEL,
            qty=1,
        ),
        CartLine(
            sku="in_retail",
            description="Noise-cancelling headphones",
            amount=249_900,
            mcc=MCC_ELECTRONICS,
            category=P.CAT_INDIA_RETAIL,
            qty=1,
        ),
    ),
)


# --------------------------------------------------------------------------------------
# The hypothetical. Every number below is invented and none of it is modelled on any real
# product. The disclaimer leads the `source` string because that string is what the console
# renders under the card's name.
# --------------------------------------------------------------------------------------

HYPOTHETICAL_ID = "hypothetical-illustrative-reserve"

HYPOTHETICAL_SOURCE = (
    "HYPOTHETICAL INSTRUMENT — invented for this demonstration. Not a real product, not "
    "modelled from any issuer's published terms, and signed by no one. It is in the "
    "candidate set because the published Indian catalogue carries no statement credits, so "
    "the case where two credits compete for one line cannot be shown from real Indian "
    "terms; attaching invented credits to a real issuer's product name is the one thing "
    "this system may never do."
)

_HYPO_DINING = "hypo:dining_credit"
_HYPO_STAY = "hypo:stay_credit"
_HYPO_EARN = "hypo:earn"

_INVENTED = "invented figure on a hypothetical instrument; no issuer publishes this"


def illustrative_reserve(issued_at: int) -> Manifest:
    """A hypothetical instrument carrying the statement-credit case.

    Two dining credits share an exclusivity group so one dinner can never draw both; a stay
    credit is smaller than the room it attaches to; and a capped travel multiplier is drawn
    down across two lines. Those are the three ways a per-line sum overstates, and this
    manifest is the only place in the console corpus where all three are available at once.
    """
    return build_manifest(
        manifest_id=HYPOTHETICAL_ID,
        issuer="Hypothetical Bank",
        product="Illustrative Reserve",
        currency=CURRENCY,
        issued_at=issued_at,
        source=HYPOTHETICAL_SOURCE,
        benefits=[
            Benefit(
                benefit_id="hypo_dining_credit_monthly",
                kind=KIND_CREDIT,
                label="Monthly dining credit",
                eligibility=Eligibility(mccs=(MCC_DINING,)),
                capacity_minor=30_000,
                exclusivity_group=_HYPO_DINING,
                window=WINDOW_MONTHLY,
                note=f"{_INVENTED}; ₹300 of balance against a larger dinner",
            ),
            Benefit(
                benefit_id="hypo_dining_credit_partner",
                kind=KIND_CREDIT,
                label="Partner restaurant credit",
                eligibility=Eligibility(mccs=(MCC_DINING,)),
                capacity_minor=20_000,
                exclusivity_group=_HYPO_DINING,
                window=WINDOW_MONTHLY,
                requires_enrollment=True,
                enrolled=True,
                note=(
                    f"{_INVENTED}; shares an exclusivity group with the monthly dining "
                    f"credit, so one dinner draws at most one of them"
                ),
            ),
            Benefit(
                benefit_id="hypo_stay_credit",
                kind=KIND_CREDIT,
                label="Annual stay credit",
                eligibility=Eligibility(mccs=(MCC_HOTEL,)),
                capacity_minor=50_000,
                exclusivity_group=_HYPO_STAY,
                window=WINDOW_ANNUAL,
                note=f"{_INVENTED}; ₹500 of balance against a much larger room charge",
            ),
            Benefit(
                benefit_id="hypo_earn_travel",
                kind=KIND_EARN,
                label="Travel multiplier",
                eligibility=Eligibility(mccs=(MCC_HOTEL, MCC_AIRLINE)),
                rate_bp=250,
                capacity_minor=40_000,
                exclusivity_group=_HYPO_EARN,
                window=WINDOW_ANNUAL,
                note=f"{_INVENTED}; the shared annual headroom is drawn down across lines",
            ),
            Benefit(
                benefit_id="hypo_earn_base",
                kind=KIND_EARN,
                label="Base earn",
                # Fuel is off the allow-list, as it is on both real cards' base rates. It is
                # not a favour to anybody: it is what makes the fuel line the one place on
                # this cart where the published Amex rate is the only rate standing.
                eligibility=Eligibility(
                    mccs=(MCC_HOTEL, MCC_AIRLINE, MCC_DINING, MCC_ELECTRONICS)
                ),
                rate_bp=75,
                exclusivity_group=_HYPO_EARN,
                note=(
                    f"{_INVENTED}; shares an exclusivity group with the travel multiplier, "
                    f"because a bonus rate replaces a base rate and never adds to it"
                ),
            ),
            Benefit(
                benefit_id="hypo_lounge",
                kind=P.KIND_UNPRICED,
                label="Lounge access",
                note=(
                    "declared and considered, deliberately not scored — and invented, like "
                    "everything else on this instrument"
                ),
            ),
        ],
    )


# --------------------------------------------------------------------------------------
# The candidate set.
#
# `issuer_signed` is the signing boundary and it is not decoration. American Express signs
# its own facts. The HDFC manifest is the AGENT's model of a competitor's published terms
# and carries no issuer signature, because an issuer signs its own facts and nothing else.
# The hypothetical is signed by no one for the more basic reason that there is no one to
# sign it.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentSpec:
    manifest: Manifest
    issuer_signed: bool


def instrument_specs(clock: int = CONSOLE_CLOCK) -> tuple[InstrumentSpec, ...]:
    """The console's candidate set, in display order. Single-currency by construction.

    Ranking across currencies would need an FX rate, which is a market price this system
    does not carry and would be pretending to know, so the set is built from the rupee
    catalogue and the rupee hypothetical alone.
    """
    catalogue = P.catalogue_by_id(clock)
    specs = [
        InstrumentSpec(catalogue[P.AMEX_PLATINUM_INDIA_ID], issuer_signed=True),
        InstrumentSpec(catalogue[P.AMEX_PLATINUM_TRAVEL_INDIA_ID], issuer_signed=True),
        InstrumentSpec(catalogue[P.HDFC_INFINIA_ID], issuer_signed=False),
        InstrumentSpec(illustrative_reserve(clock), issuer_signed=False),
    ]
    off_currency = [s.manifest.manifest_id for s in specs if s.manifest.currency != CURRENCY]
    if off_currency:
        raise ValueError(
            f"the console candidate set must be single-currency ({CURRENCY}); "
            f"{', '.join(off_currency)} is not. Ranking across currencies would need an FX "
            f"rate this system does not carry."
        )
    return tuple(specs)


HEADLINE_INSTRUMENT = P.AMEX_PLATINUM_INDIA_ID


# --------------------------------------------------------------------------------------
# Derivations. Everything below reads the emitted witness; nothing re-runs the allocator.
# --------------------------------------------------------------------------------------


def witness_hash(w: Witness) -> str:
    return hashlib.sha256(canonical_json(w.to_dict(currency=CURRENCY))).hexdigest()


def derivation_rows(manifest: Manifest, cart: Cart, w: Witness) -> list[dict[str, Any]]:
    """The allocation table. One row per assignment, with the balance drawn down.

    `capacity_before`/`capacity_after` are a running total over the witness's own output
    order, so a reader can re-add the column and land on the same remaining balance.
    """
    by_id = {b.benefit_id: b for b in manifest.benefits}
    by_sku = {line.sku: line for line in cart.lines}
    running: dict[str, int] = {}
    rows = []
    for a in w.assignments:
        benefit = by_id[a.benefit_id]
        line = by_sku[a.line_sku]
        used = running.get(a.benefit_id, 0)
        before = None if benefit.capacity_minor is None else benefit.capacity_minor - used
        running[a.benefit_id] = used + a.consumed_minor
        rows.append(
            {
                "line_sku": line.sku,
                "line_description": line.description,
                "line_amount": line.amount,
                "line_mcc": line.mcc,
                "benefit_id": benefit.benefit_id,
                "benefit_label": benefit.label,
                "benefit_kind": benefit.kind,
                "rate_bp": benefit.rate_bp,
                "window": benefit.window,
                "exclusivity_group": benefit.exclusivity_group,
                "consumed_minor": a.consumed_minor,
                "value_minor": a.value_minor,
                "value_display": money(a.value_minor),
                "capacity_before": before,
                "capacity_after": None if before is None else before - a.consumed_minor,
            }
        )
    return rows


def capped_sum(manifest: Manifest, cart: Cart) -> int:
    """The steelmanned per-line valuation: caps each pairing at the benefit's balance.

    `naive_sum` is the literal per-line sum and it is easy to beat. This is the strongest
    valuation a per-line implementation can produce without solving an assignment problem:
    it reads the remaining balance and clamps each pairing to it. What it still cannot do
    is notice that one balance is being spent twice across two lines, or that two benefits
    in an exclusivity group both claimed the same line — because neither fact is a property
    of any single line.
    """
    total = 0
    for benefit in manifest.priced():
        for line in cart.lines:
            value = benefit.value_for_line(line, cart.merchant)
            if value <= 0:
                continue
            if benefit.capacity_minor is not None:
                value = min(value, benefit.capacity_minor)
            total += value
    return total


def reconcile(manifest: Manifest, cart: Cart, w: Witness) -> dict[str, Any]:
    """Account for every unit between each per-line baseline and the witness.

    Derived from the witness and the manifest only — the same two objects a counterparty
    receives — so the reconciliation is reproducible without re-running the allocator. Both
    steps are asserted to close exactly before this function returns.
    """
    assigned = {(a.line_sku, a.benefit_id): a for a in w.assignments}
    group_holder: dict[tuple[str, str], str] = {}
    for a in w.assignments:
        benefit = next(b for b in manifest.benefits if b.benefit_id == a.benefit_id)
        if benefit.exclusivity_group:
            group_holder[(a.line_sku, benefit.exclusivity_group)] = a.benefit_id

    rows: list[dict[str, Any]] = []
    by_cause: dict[str, int] = {CAUSE_BALANCE: 0, CAUSE_EXCLUSIVITY: 0, CAUSE_EXHAUSTED: 0}

    for benefit in manifest.priced():
        for line in cart.lines:
            naive_value = benefit.value_for_line(line, cart.merchant)
            if naive_value <= 0:
                continue
            capped_value = (
                naive_value
                if benefit.capacity_minor is None
                else min(naive_value, benefit.capacity_minor)
            )
            hit = assigned.get((line.sku, benefit.benefit_id))
            realized = hit.value_minor if hit else 0

            # A pairing loses value in at most two steps: the balance is smaller than the
            # line (visible per line), then the balance is already spent elsewhere or the
            # line is held by another benefit in the same group (visible only across lines).
            by_cause[CAUSE_BALANCE] += naive_value - capped_value
            struck_by = None
            if capped_value > realized:
                holder = group_holder.get((line.sku, benefit.exclusivity_group or ""))
                if benefit.exclusivity_group and holder not in (None, benefit.benefit_id):
                    by_cause[CAUSE_EXCLUSIVITY] += capped_value - realized
                    struck_by = holder
                else:
                    by_cause[CAUSE_EXHAUSTED] += capped_value - realized

            rows.append(
                {
                    "line_sku": line.sku,
                    "line_description": line.description,
                    "benefit_id": benefit.benefit_id,
                    "benefit_label": benefit.label,
                    "benefit_kind": benefit.kind,
                    "exclusivity_group": benefit.exclusivity_group,
                    "naive_minor": naive_value,
                    "capped_minor": capped_value,
                    "realized_minor": realized,
                    "shortfall_minor": naive_value - realized,
                    "cause": (
                        CAUSE_ASSIGNED
                        if naive_value == realized
                        else CAUSE_EXCLUSIVITY
                        if struck_by
                        else CAUSE_BALANCE
                        if capped_value == realized
                        else CAUSE_EXHAUSTED
                    ),
                    "struck_by": struck_by,
                }
            )

    naive_total = naive_sum(manifest, cart)
    capped_total = capped_sum(manifest, cart)
    realized_total = w.realized_minor()

    if naive_total - capped_total != by_cause[CAUSE_BALANCE]:
        raise AssertionError(
            f"{manifest.manifest_id}: the per-line reconciliation step does not close"
        )
    cross_line = by_cause[CAUSE_EXCLUSIVITY] + by_cause[CAUSE_EXHAUSTED]
    if capped_total - realized_total != cross_line:
        raise AssertionError(
            f"{manifest.manifest_id}: the cross-line reconciliation step does not close"
        )

    return {
        "naive_minor": naive_total,
        "naive_display": money(naive_total),
        "capped_minor": capped_total,
        "capped_display": money(capped_total),
        "witness_minor": realized_total,
        "witness_display": money(realized_total),
        "overstatement_minor": naive_total - realized_total,
        "overstatement_display": money(naive_total - realized_total),
        "capped_overstatement_minor": capped_total - realized_total,
        "capped_overstatement_display": money(capped_total - realized_total),
        "by_cause": [
            {
                "cause": cause,
                "minor": by_cause[cause],
                "display": money(by_cause[cause]),
                "visible_per_line": cause == CAUSE_BALANCE,
            }
            for cause in (CAUSE_BALANCE, CAUSE_EXCLUSIVITY, CAUSE_EXHAUSTED)
        ],
        "rows": rows,
    }


def collisions(manifest: Manifest, cart: Cart, w: Witness) -> list[dict[str, Any]]:
    """Lines where two benefits in one exclusivity group both wanted the same spend."""
    by_id = {b.benefit_id: b for b in manifest.benefits}
    winners = {
        (a.line_sku, by_id[a.benefit_id].exclusivity_group): a
        for a in w.assignments
        if by_id[a.benefit_id].exclusivity_group
    }
    out = []
    for (sku, group), winner in sorted(winners.items()):
        line = next(line for line in cart.lines if line.sku == sku)
        struck = []
        for benefit in manifest.priced():
            if benefit.exclusivity_group != group or benefit.benefit_id == winner.benefit_id:
                continue
            value = benefit.value_for_line(line, cart.merchant)
            if value <= 0:
                continue
            struck.append(
                {
                    "benefit_id": benefit.benefit_id,
                    "label": benefit.label,
                    "value_minor": value,
                    "value_display": money(value),
                    "capacity_minor": benefit.capacity_minor,
                }
            )
        if not struck:
            continue
        win_benefit = by_id[winner.benefit_id]
        out.append(
            {
                "line_sku": sku,
                "line_description": line.description,
                "line_amount": line.amount,
                "exclusivity_group": group,
                "winner": {
                    "benefit_id": winner.benefit_id,
                    "label": win_benefit.label,
                    "value_minor": winner.value_minor,
                    "value_display": money(winner.value_minor),
                    "capacity_minor": win_benefit.capacity_minor,
                },
                "struck": struck,
            }
        )
    return out


def valuation(spec: InstrumentSpec, cart: Cart) -> dict[str, Any]:
    manifest = spec.manifest
    result = allocate(manifest, cart)
    w = result.witness
    asserted = w.realized_minor()
    verification = verify_witness(
        witness=w, manifest=manifest, cart=cart, asserted_minor=asserted
    )
    if not verification.ok:
        raise AssertionError(
            f"{manifest.manifest_id}: the allocator's own witness does not verify: "
            f"{[f.to_dict() for f in verification.failures]}"
        )

    signed = (
        sign_manifest(manifest, ISSUER_KEY, key_id=ISSUER_KEY_ID) if spec.issuer_signed else None
    )

    return {
        "instrument_id": manifest.manifest_id,
        "issuer": manifest.issuer,
        "product": manifest.product,
        "manifest_id": manifest.manifest_id,
        "manifest_hash": manifest.content_hash(),
        "manifest_source": manifest.source,
        "issuer_signed": spec.issuer_signed,
        "signature": signed.signature if signed else None,
        "key_id": signed.key_id if signed else None,
        "asserted_minor": asserted,
        "asserted_display": money(asserted),
        "witness": w.to_dict(currency=CURRENCY),
        "witness_hash": witness_hash(w),
        "verification": verification.to_dict(currency=CURRENCY),
        "derivation": derivation_rows(manifest, cart, w),
        "reconciliation": reconcile(manifest, cart, w),
        "collisions": collisions(manifest, cart, w),
        "unpriced": [
            {"benefit_id": b.benefit_id, "label": b.label, "note": b.note}
            for b in manifest.unpriced()
        ],
        "allocator_stats": {
            "considered": result.considered,
            "assigned": result.assigned,
            "skipped_capacity": result.skipped_capacity,
            "skipped_exclusivity": result.skipped_exclusivity,
        },
    }


# --------------------------------------------------------------------------------------
# Latency. Measured on the host that serves it, at a stated problem size, and never
# extrapolated. The only field of the envelope that does not replay.
# --------------------------------------------------------------------------------------

# Full-fidelity sample counts, used by the fixture generator. The route measures with fewer
# because a checkout budget is not a benchmark budget, and it says so in `method`.
FULL_REPS = (3_000, 800)
ROUTE_REPS = (400, 120)


def percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)

    def at(q: float) -> float:
        idx = min(len(ordered) - 1, int(q * len(ordered)))
        return ordered[idx]

    return {
        "p50_ms": round(statistics.median(ordered), 4),
        "p99_ms": round(at(0.99), 4),
        "min_ms": round(ordered[0], 4),
        "max_ms": round(ordered[-1], 4),
        "runs": len(ordered),
    }


def synthetic_problem(instruments: int, lines: int, benefits: int) -> tuple[list[Manifest], Cart]:
    """The exact shape the MaxSMT approach was benchmarked at, so the two are comparable."""
    rng = random.Random(20260825)
    mccs = (MCC_HOTEL, MCC_DINING, MCC_AIRLINE, MCC_FUEL, MCC_ELECTRONICS, 5411)
    cart = Cart(
        merchant="m_bench",
        currency=CURRENCY,
        lines=tuple(
            CartLine(
                sku=f"sku_{i:02d}",
                description=f"line {i}",
                amount=rng.randrange(50_000, 5_000_000),
                mcc=rng.choice(mccs),
                category="bench",
                qty=1,
            )
            for i in range(lines)
        ),
    )
    manifests = []
    for m in range(instruments):
        bens = []
        for b in range(benefits):
            kind = (KIND_EARN, KIND_CREDIT, P.KIND_PROTECTION)[b % 3]
            bens.append(
                Benefit(
                    benefit_id=f"ben_{m}_{b}",
                    kind=kind,
                    label=f"benefit {m}.{b}",
                    eligibility=Eligibility(mccs=(rng.choice(mccs),)),
                    rate_bp=rng.randrange(100, 1_000) if kind == KIND_EARN else 0,
                    capacity_minor=rng.randrange(50_000, 800_000),
                    flat_minor=rng.randrange(20_000, 200_000)
                    if kind == P.KIND_PROTECTION
                    else 0,
                    exclusivity_group=rng.choice((None, "g0", "g1", "g2")),
                )
            )
        manifests.append(
            build_manifest(
                manifest_id=f"mf_bench_{m}",
                issuer="Bench",
                product=f"bench {m}",
                benefits=bens,
                issued_at=CONSOLE_CLOCK,
                currency=CURRENCY,
            )
        )
    return manifests, cart


def measure(
    specs: Sequence[InstrumentSpec], cart: Cart, reps: tuple[int, int] = FULL_REPS
) -> dict[str, Any]:
    demo_manifests = [s.manifest for s in specs]
    demo_reps, bench_reps = reps

    def time_allocate(manifests: list[Manifest], subject: Cart, runs: int) -> list[float]:
        out = []
        for _ in range(runs):
            started = time.perf_counter()
            for m in manifests:
                allocate(m, subject)
            out.append((time.perf_counter() - started) * 1000.0)
        return out

    def time_verify(manifests: list[Manifest], subject: Cart, runs: int) -> list[float]:
        prepared = [(m, allocate(m, subject).witness) for m in manifests]
        out = []
        for _ in range(runs):
            started = time.perf_counter()
            for m, w in prepared:
                verify_witness(
                    witness=w, manifest=m, cart=subject, asserted_minor=w.realized_minor()
                )
            out.append((time.perf_counter() - started) * 1000.0)
        return out

    # Warm up so the first-call import cost does not land in the sample.
    time_allocate(demo_manifests, cart, 50)
    demo_alloc = percentiles(time_allocate(demo_manifests, cart, demo_reps))
    demo_verify = percentiles(time_verify(demo_manifests, cart, demo_reps))

    bench_manifests, bench_cart = synthetic_problem(instruments=8, lines=20, benefits=40)
    time_allocate(bench_manifests, bench_cart, 20)
    bench_alloc = percentiles(time_allocate(bench_manifests, bench_cart, bench_reps))
    bench_verify = percentiles(time_verify(bench_manifests, bench_cart, bench_reps))

    benefit_count = sum(len(s.manifest.benefits) for s in specs)
    demo_size = (
        f"{len(specs)} instruments × {len(cart.lines)} lines × {benefit_count} benefits "
        f"(the cart on screen)"
    )
    return {
        "demo": {
            **demo_alloc,
            "problem_size": demo_size,
            "method": "wall clock around plumbline.allocate for every instrument, warmed",
        },
        "demo_verify": {
            **demo_verify,
            "problem_size": f"{len(specs)} witnesses over the cart on screen",
            "method": "wall clock around plumbline.witness.verify_witness, no solver",
        },
        "bench": {
            **bench_alloc,
            "problem_size": "8 instruments × 20 lines × 40 benefits",
            "method": "wall clock around plumbline.allocate, warmed, same host as the row above",
        },
        "bench_verify": {
            **bench_verify,
            "problem_size": "8 witnesses × 20 lines × 40 benefits",
            "method": "wall clock around plumbline.witness.verify_witness, no solver",
        },
        "solver": {
            "lo_ms": 451,
            "hi_ms": 2695,
            "variance_x": 6,
            "timeout_ms": 2000,
            "problem_size": "8 instruments × 20 lines × 40 benefits",
            "source": "adversarial panel benchmark of the MaxSMT formulation",
            "measured_here": False,
            "failure_mode": (
                "on timeout the solver can report a lower bound above its upper bound, so an "
                "implementation that reads the bound signs an incoherent number"
            ),
        },
        "host": {
            "python": _python_version(),
            "note": "figures are from one host and are reported, not extrapolated",
        },
    }


def _python_version() -> str:
    import sys

    return sys.version.split()[0]


# --------------------------------------------------------------------------------------
# RFC 6962 transparency: signed tree heads, consistency proofs.
#
# caveat.ledger builds its tree level by level, promoting the odd tail. That produces
# exactly RFC 6962's MTH — the equality is asserted below rather than assumed — so the
# published consistency-proof algorithm applies unchanged.
# --------------------------------------------------------------------------------------


def mth(leaves: list[str]) -> str:
    if not leaves:
        return "0" * 64
    if len(leaves) == 1:
        return leaves[0]
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    return _node_hash(mth(leaves[:k]), mth(leaves[k:]))


def subproof(m: int, leaves: list[str], at_root: bool) -> list[str]:
    n = len(leaves)
    if m == n:
        return [] if at_root else [mth(leaves)]
    k = 1
    while k * 2 < n:
        k *= 2
    if m <= k:
        return subproof(m, leaves[:k], at_root) + [mth(leaves[k:])]
    return subproof(m - k, leaves[k:], False) + [mth(leaves[:k])]


def consistency_proof(m: int, leaves: list[str]) -> list[str]:
    if m == 0 or m > len(leaves):
        return []
    if m == len(leaves):
        return []
    return subproof(m, leaves, True)


def verify_consistency(
    m: int, n: int, first_root: str, second_root: str, proof: list[str]
) -> bool:
    """Reference verifier, kept here so the TypeScript port has something to agree with."""
    if m > n:
        return False
    if m == n:
        return not proof and first_root == second_root
    if m == 0:
        return not proof
    node, last = m - 1, n - 1
    while node % 2 == 1:
        node //= 2
        last //= 2
    p = list(proof)
    if node > 0:
        if not p:
            return False
        fr = sr = p.pop(0)
    else:
        fr = sr = first_root
    while node > 0:
        if node % 2 == 1:
            if not p:
                return False
            sibling = p.pop(0)
            fr = _node_hash(sibling, fr)
            sr = _node_hash(sibling, sr)
        elif node < last:
            if not p:
                return False
            sr = _node_hash(sr, p.pop(0))
        node //= 2
        last //= 2
    while last > 0:
        if not p:
            return False
        sr = _node_hash(sr, p.pop(0))
        last //= 2
    return not p and fr == first_root and sr == second_root


def sign_head(root: str, size: int, at: int) -> dict[str, Any]:
    body = {"tree_size": size, "root_hash": root, "signed_at": at}
    signature = hmac.new(LOG_KEY.encode(), canonical_json(body), hashlib.sha256).hexdigest()
    return {**body, "key_id": LOG_KEY_ID, "signature": signature}


def consistency_vectors(clock: int) -> list[dict[str, Any]]:
    """Cross-checked (m, n) vectors so the browser's verifier is tested, not just exercised."""
    ledger = MerkleLedger()
    for i in range(24):
        ledger.append("vector", {"i": i}, clock + i)
    leaves = [e.leaf_hash for e in ledger.entries]

    if mth(leaves) != ledger.root():
        raise AssertionError("the ledger tree is not RFC 6962 MTH")

    out: list[dict[str, Any]] = []
    for n in range(1, len(leaves) + 1):
        for m in range(1, n + 1):
            sub = leaves[:n]
            proof = consistency_proof(m, sub)
            first_root, second_root = mth(leaves[:m]), mth(sub)
            if not verify_consistency(m, n, first_root, second_root, proof):
                raise AssertionError(f"reference consistency proof failed at ({m}, {n})")
            if (m * 7 + n * 3) % 11 == 0:
                out.append(
                    {
                        "first_size": m,
                        "second_size": n,
                        "first_root": first_root,
                        "second_root": second_root,
                        "path": proof,
                        "expect_ok": True,
                    }
                )
    # Negatives: a tampered second root must fail, and so must a truncated path.
    m, n = 5, 17
    proof = consistency_proof(m, leaves[:n])
    good_first, good_second = mth(leaves[:m]), mth(leaves[:n])
    tampered = "f" + good_second[1:] if good_second[0] != "f" else "0" + good_second[1:]
    if verify_consistency(m, n, good_first, tampered, proof):
        raise AssertionError("a tampered root verified")
    out.append(
        {
            "first_size": m,
            "second_size": n,
            "first_root": good_first,
            "second_root": tampered,
            "path": proof,
            "expect_ok": False,
        }
    )
    if verify_consistency(m, n, good_first, good_second, proof[:-1]):
        raise AssertionError("a truncated proof verified")
    out.append(
        {
            "first_size": m,
            "second_size": n,
            "first_root": good_first,
            "second_root": good_second,
            "path": proof[:-1],
            "expect_ok": False,
        }
    )
    return out


# --------------------------------------------------------------------------------------
# Corpus and attribution.
# --------------------------------------------------------------------------------------

CORPUS_MERCHANTS = (
    "m_travel_desk_in",
    "m_oberoi_direct",
    "m_indigo_direct",
    "m_croma",
    "m_fuel_station",
)

# (category, mcc, low, high) per line. Ranges are in paise.
CORPUS_TEMPLATES: tuple[tuple[str, tuple[tuple[str, int, int, int], ...]], ...] = (
    (
        "overseas_stay",
        (
            (P.CAT_OVERSEAS, MCC_HOTEL, 900_000, 4_000_000),
            (P.CAT_OVERSEAS, MCC_DINING, 120_000, 600_000),
        ),
    ),
    (
        "domestic_stay",
        (
            (P.CAT_HOTEL_DIRECT, MCC_HOTEL, 600_000, 3_000_000),
            (P.CAT_DINING, MCC_DINING, 100_000, 500_000),
        ),
    ),
    ("fuel_run", ((P.CAT_FUEL, MCC_FUEL, 200_000, 900_000),)),
    (
        "fuel_and_dining",
        (
            (P.CAT_FUEL, MCC_FUEL, 150_000, 600_000),
            (P.CAT_DINING, MCC_DINING, 60_000, 300_000),
        ),
    ),
    ("electronics", ((P.CAT_INDIA_RETAIL, MCC_ELECTRONICS, 200_000, 2_500_000),)),
    ("flight", ((P.CAT_AIRFARE, MCC_AIRLINE, 500_000, 2_500_000),)),
    # A drive to the airport and duty-free on the other side. This is the shape where the
    # overseas rate is decisive rather than merely present: the fuel line is the only reason
    # the Amex card is ahead at all, and the margin it is ahead by is smaller than the
    # difference between the overseas rate and the base rate — so deleting either one flips
    # the selection, and deleting the base rate does not.
    (
        "fuel_and_duty_free",
        (
            (P.CAT_FUEL, MCC_FUEL, 400_000, 800_000),
            (P.CAT_OVERSEAS, MCC_ELECTRONICS, 200_000, 600_000),
        ),
    ),
)

# Modelled annual programme cost per benefit, in minor units. An INPUT to the 2x2 and not an
# output of it, labelled as modelled wherever it is displayed, and carried only for the
# instruments whose issuer signs — an issuer's own cost line is the only one it can cut.
#
# Each figure is point liability per member per year under the same published cap and the
# same point policy the manifests use, so the arithmetic is visible rather than asserted:
#
#   plat base       ₹20,00,000 of annual spend at 1 MR per ₹40  = 50,000 pts × ₹0.25 = ₹12,500
#   plat fuel       the published 5,000 pts monthly cap, taken 12 times = 60,000 pts = ₹15,000
#   plat overseas   ₹2,00,000 of overseas spend at 3 MR per ₹40 = 15,000 pts × ₹0.25 = ₹3,750
#   travel base     ₹5,00,000 of annual spend at 1 MR per ₹50   = 10,000 pts × ₹0.25 = ₹2,500
#
# The spend assumptions are modelled and are the only invented part; the caps and the rates
# are the published ones. ₹20 lakh is the card's own published renewal-benefit threshold, so
# it is at least a number the issuer itself uses to describe this member.
ANNUAL_COST = {
    "amex_in_plat_earn_base": 1_250_000,
    "amex_in_plat_earn_fuel": 1_500_000,
    "amex_in_plat_earn_overseas_3x": 375_000,
    "amex_in_travel_earn_base": 250_000,
}

HIGH_COST_MINOR = 1_000_000
DECISIVE_BP_THRESHOLD = 1_000  # 10% of the receipts a benefit appeared in


def strip_benefit(manifest: Manifest, benefit_id: str) -> Manifest:
    return build_manifest(
        manifest_id=manifest.manifest_id,
        issuer=manifest.issuer,
        product=manifest.product,
        currency=manifest.currency,
        benefits=[b for b in manifest.benefits if b.benefit_id != benefit_id],
        issued_at=manifest.issued_at,
        source=manifest.source,
    )


def overlay(manifest: Manifest, rng: random.Random) -> Manifest:
    """A member-scoped state overlay: what is actually left on this account today.

    A corpus in which every account carries identical balances would make every receipt the
    same receipt. Balances deplete through the year and enrollment-gated benefits are often
    not enrolled, so the corpus varies both — which is also the only reason a benefit can be
    decisive on one cart and irrelevant on the next.
    """
    quarters = (0, 1, 2, 3, 4, 5, 6, 8)
    benefits = []
    for b in manifest.benefits:
        if not b.is_priced():
            benefits.append(b)
            continue
        cap = b.capacity_minor
        if cap is not None:
            cap = (cap * rng.choice(quarters)) // 4
        enrolled = rng.random() > 0.35 if b.requires_enrollment else b.enrolled
        benefits.append(replace(b, capacity_minor=cap, enrolled=enrolled))
    return build_manifest(
        manifest_id=manifest.manifest_id,
        issuer=manifest.issuer,
        product=manifest.product,
        currency=manifest.currency,
        benefits=benefits,
        issued_at=manifest.issued_at,
        source=manifest.source,
    )


def build_corpus(
    specs: Sequence[InstrumentSpec], size: int, seed: int = 90210
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    manifests = [s.manifest for s in specs]
    corpus = []
    for i in range(size):
        merchant = rng.choice(CORPUS_MERCHANTS)
        _, shape = rng.choice(CORPUS_TEMPLATES)
        lines = tuple(
            CartLine(
                sku=f"sku_{i}_{j}",
                description=f"line {j}",
                amount=rng.randrange(lo, hi),
                mcc=mcc,
                category=category,
                qty=1,
            )
            for j, (category, mcc, lo, hi) in enumerate(shape)
        )
        cart = Cart(merchant=merchant, currency=CURRENCY, lines=lines)
        state = {m.manifest_id: overlay(m, rng) for m in manifests}
        values = {mid: allocate(m, cart).witness.realized_minor() for mid, m in state.items()}
        winner = max(values.items(), key=lambda kv: (kv[1], kv[0]))[0]
        corpus.append({"cart": cart, "state": state, "values": values, "winner": winner})
    return corpus


def attribution(
    specs: Sequence[InstrumentSpec], corpus: list[dict[str, Any]], clock: int
) -> dict[str, Any]:
    """Benefit-level selection influence, measured by removal against the same corpus.

    `decisive` counts receipts where deleting the benefit from its manifest changes which
    instrument the stated criterion selects. That is selection influence at the moment of
    choice. It is not retention, not incremental spend, and not a renewal signal; the caveat
    travels with the number.

    Only the issuer-signed instruments are scored, because the question this answers is
    "which of MY benefit lines are load-bearing", and an issuer can only cut its own.
    """
    scored = [s for s in specs if s.issuer_signed]
    stats: dict[str, dict[str, Any]] = {}

    for spec in scored:
        manifest = spec.manifest
        for benefit in manifest.priced():
            stats[benefit.benefit_id] = {
                "benefit_id": benefit.benefit_id,
                "label": benefit.label,
                "kind": benefit.kind,
                "issuer": manifest.issuer,
                "product": manifest.product,
                "manifest_id": manifest.manifest_id,
                "in_candidate_set": 0,
                "in_winning_derivation": 0,
                "decisive": 0,
                "value_delivered_minor": 0,
                "annual_cost_minor": ANNUAL_COST.get(benefit.benefit_id, 0),
            }

    for entry in corpus:
        cart = entry["cart"]
        winner_id = entry["winner"]
        for spec in scored:
            manifest = spec.manifest
            today = entry["state"][manifest.manifest_id]
            w = allocate(today, cart).witness
            used = {a.benefit_id: a.value_minor for a in w.assignments}
            for benefit in today.priced():
                row = stats[benefit.benefit_id]
                if any(benefit.eligibility.admits(line, cart.merchant) for line in cart.lines):
                    row["in_candidate_set"] += 1
                if benefit.benefit_id not in used:
                    continue
                if manifest.manifest_id == winner_id:
                    row["in_winning_derivation"] += 1
                    row["value_delivered_minor"] += used[benefit.benefit_id]
                counterfactual = dict(entry["values"])
                counterfactual[manifest.manifest_id] = (
                    allocate(strip_benefit(today, benefit.benefit_id), cart)
                    .witness.realized_minor()
                )
                new_winner = max(counterfactual.items(), key=lambda kv: (kv[1], kv[0]))[0]
                if new_winner != winner_id:
                    row["decisive"] += 1

    rows = []
    for row in stats.values():
        appearances = row["in_candidate_set"]
        decisive_bp = 0 if not appearances else (row["decisive"] * 10_000) // appearances
        high_cost = row["annual_cost_minor"] >= HIGH_COST_MINOR
        often = decisive_bp >= DECISIVE_BP_THRESHOLD
        quadrant = (
            QUADRANT_LOAD_BEARING
            if high_cost and often
            else QUADRANT_DEAD_WEIGHT
            if high_cost
            else QUADRANT_OPTION
            if often
            else QUADRANT_NOISE
        )
        rows.append(
            {
                **row,
                "decisive_bp": decisive_bp,
                "high_cost": high_cost,
                "often_decisive": often,
                "quadrant": quadrant,
                "value_delivered_display": money(row["value_delivered_minor"]),
                "annual_cost_display": money(row["annual_cost_minor"]),
            }
        )
    rows.sort(key=lambda r: (-r["decisive_bp"], -r["annual_cost_minor"], r["benefit_id"]))

    wins = {s.manifest.manifest_id: 0 for s in specs}
    for entry in corpus:
        wins[entry["winner"]] += 1

    return {
        "as_of": clock,
        "corpus_size": len(corpus),
        "method": (
            "per receipt, delete one benefit from its manifest, re-run the allocator, and "
            "re-apply the stated criterion; decisive means the selected instrument changes"
        ),
        "cost_source": "modelled annual programme cost per benefit — an input, not a measurement",
        "thresholds": {
            "high_cost_minor": HIGH_COST_MINOR,
            "high_cost_display": money(HIGH_COST_MINOR),
            "decisive_bp": DECISIVE_BP_THRESHOLD,
        },
        "caveat": (
            "This measures selection influence at the moment of choice. It does not measure "
            "retention, incremental spend, or renewal. A benefit that never appears in a "
            "winning derivation may still be why the card was taken out — most of what this "
            "card is held for is declared as considered-but-unpriced and is not on this "
            "chart at all."
        ),
        "wins": [{"manifest_id": k, "wins": v} for k, v in sorted(wins.items())],
        "benefits": rows,
    }


# --------------------------------------------------------------------------------------
# The receipt and the refusals.
# --------------------------------------------------------------------------------------

CRITERION = (
    "maximise witness-backed value on this cart; break ties by lower asserted value, then "
    "by instrument id"
)

POLICY = {
    "policy_id": "pol_cardholder_max_value_v3",
    "owner": "cardholder",
    "criterion": CRITERION,
    "endorsed_by_issuer": False,
    "note": (
        "The valuation policy and the ranking belong to the cardholder. The issuer signs "
        "facts and never signs a comparison between instruments. "
        + P.POINT_VALUATION_ASYMMETRY
    ),
}


def rank(valuations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(valuations, key=lambda v: (-v["asserted_minor"], v["instrument_id"]))
    for i, v in enumerate(ordered):
        v["rank"] = i + 1
    return ordered


def build_receipt(
    valuations: list[dict[str, Any]], cart: Cart, ledger: MerkleLedger, at: int
) -> dict[str, Any]:
    ranked = rank(valuations)
    policy_hash = hashlib.sha256(canonical_json(POLICY)).hexdigest()
    body = {
        "cart_hash": cart.hash(),
        "candidates": [
            {
                "instrument_id": v["instrument_id"],
                "asserted_minor": v["asserted_minor"],
                "witness_hash": v["witness_hash"],
                "manifest_hash": v["manifest_hash"],
                "issuer_signed": v["issuer_signed"],
            }
            for v in ranked
        ],
        "policy_hash": policy_hash,
        "selected": ranked[0]["instrument_id"],
        "issued_at": at,
    }
    entry = ledger.append("plumbline_receipt", body, at)
    return {
        "receipt_id": f"rcp_{entry.entry_hash[:12]}",
        "issued_at": at,
        "cart": cart.to_dict(),
        "cart_hash": cart.hash(),
        "policy": {**POLICY, "policy_hash": policy_hash},
        "criterion": CRITERION,
        "candidates": ranked,
        "selected": ranked[0]["instrument_id"],
        "ledger_seq": entry.seq,
        "entry_hash": entry.entry_hash,
        "leaf_hash": entry.leaf_hash,
    }


def build_refusals(manifest: Manifest, cart: Cart) -> list[dict[str, Any]]:
    """Two assertions no witness supports. Both verifications are real verifier output.

    Both are built on the headline instrument, and both are failures of a term the manifest
    actually declares — a published monthly point cap and a published bonus-replaces-base
    rule. A refusal demonstrated against an invented term would prove nothing about the
    card on screen.
    """
    by_id = {b.benefit_id: b for b in manifest.benefits}
    honest = allocate(manifest, cart).witness
    supported = honest.realized_minor()
    out: list[dict[str, Any]] = []

    # 1. Claim more from a capped benefit than its published rate yields on that line. The
    #    verifier does not take the claimed number on trust: it recomputes rate × amount off
    #    the manifest, and the claim is four times the monthly headroom the manifest declares,
    #    so it fails on the arithmetic before capacity is ever reached.
    fuel_benefit = by_id["amex_in_plat_earn_fuel"]
    fuel_line = next(line for line in cart.lines if line.category == P.CAT_FUEL)
    headroom = fuel_benefit.capacity_minor or 0
    overdrawn = headroom * 4
    inflated = Witness(
        manifest_id=manifest.manifest_id,
        cart_hash=cart.hash(),
        assignments=tuple(
            a
            for a in honest.assignments
            if not (a.line_sku == fuel_line.sku and a.benefit_id == fuel_benefit.benefit_id)
        )
        + (
            Assignment(
                line_sku=fuel_line.sku,
                benefit_id=fuel_benefit.benefit_id,
                consumed_minor=overdrawn,
                value_minor=overdrawn,
            ),
        ),
    )
    asserted = inflated.realized_minor()
    verification = verify_witness(
        witness=inflated, manifest=manifest, cart=cart, asserted_minor=asserted
    )
    if verification.ok:
        raise AssertionError("the capacity forgery verified; the probe proves nothing")
    out.append(
        {
            "case_id": "refusal_rate",
            "label": "The published rate does not yield that much",
            "narrative": (
                f"The agent claims {money(overdrawn)} of fuel earn on a "
                f"{money(fuel_line.amount)} fill. The published rate is "
                f"{P.AMEX_IN_FUEL_POINTS_PER_BLOCK} Membership Rewards Points per "
                f"₹{P.AMEX_IN_FUEL_BLOCK_MAJOR}, and only {money(headroom)} of the monthly "
                f"cap is left on this account. The witness names the benefit, so the verifier "
                f"recomputes the figure off the manifest instead of accepting it, and the "
                f"arithmetic does not close."
            ),
            "asserted_minor": asserted,
            "asserted_display": money(asserted),
            "supported_minor": supported,
            "supported_display": money(supported),
            "witness": inflated.to_dict(currency=CURRENCY),
            "witness_hash": witness_hash(inflated),
            "verification": verification.to_dict(currency=CURRENCY),
            "reason_code": REFUSED_NO_WITNESS,
        }
    )

    # 2. Add the base rate to the bonus rate on one line. This is the single most common way
    #    a per-line valuation overstates a premium card, and the published terms say the 3X
    #    replaces the 1X rather than adding to it.
    base = by_id["amex_in_plat_earn_base"]
    overseas = by_id["amex_in_plat_earn_overseas_3x"]
    overseas_line = next(line for line in cart.lines if line.category == P.CAT_OVERSEAS)
    base_value = base.value_for_line(overseas_line, cart.merchant)
    stacked = Witness(
        manifest_id=manifest.manifest_id,
        cart_hash=cart.hash(),
        assignments=honest.assignments
        + (
            Assignment(
                line_sku=overseas_line.sku,
                benefit_id=base.benefit_id,
                consumed_minor=base_value,
                value_minor=base_value,
            ),
        ),
    )
    asserted2 = stacked.realized_minor()
    verification2 = verify_witness(
        witness=stacked, manifest=manifest, cart=cart, asserted_minor=asserted2
    )
    if verification2.ok:
        raise AssertionError("the exclusivity forgery verified; the probe proves nothing")
    out.append(
        {
            "case_id": "refusal_exclusivity",
            "label": "The 3X replaces the 1X; it is not added to it",
            "narrative": (
                f"The agent adds the base rate to the overseas rate on the same line. Both "
                f"sit in the exclusivity group {overseas.exclusivity_group!r} that the "
                f"manifest declares, so the verifier rejects the pair by name rather than by "
                f"judgement."
            ),
            "asserted_minor": asserted2,
            "asserted_display": money(asserted2),
            "supported_minor": supported,
            "supported_display": money(supported),
            "witness": stacked.to_dict(currency=CURRENCY),
            "witness_hash": witness_hash(stacked),
            "verification": verification2.to_dict(currency=CURRENCY),
            "reason_code": REFUSED_NO_WITNESS,
        }
    )
    return out


# --------------------------------------------------------------------------------------
# Assembly. One GET, because the five beats are five views of one corpus and a judge
# switching screens must never see two screens disagree.
# --------------------------------------------------------------------------------------

DISCLOSURE = (
    "Recorded output of backend/plumbline and backend/caveat at a fixed clock. Card terms are "
    "modelled from publicly published terms — there is no live Offers feed — and every "
    "remaining balance is synthetic member state, not live issuer data. One instrument, "
    "Hypothetical Bank Illustrative Reserve, is invented outright and says so on its own "
    "provenance line; it is not modelled on any real product. Signatures are HMAC under "
    "prototype keys; production signs with the issuer's HSM key."
)

NARRATIVE = (
    "The same basket, valued two ways on the same instrument. The left column adds every "
    "eligible benefit to every line it admits. The right column exhibits an allocation that "
    "actually satisfies the balances and the exclusivity groups, and asserts only what that "
    "allocation realises."
)

CORPUS_SIZE = 180


def build_state(
    clock: int = CONSOLE_CLOCK,
    *,
    reps: tuple[int, int] = FULL_REPS,
    corpus_size: int = CORPUS_SIZE,
) -> dict[str, Any]:
    """The whole `PlumblineState` envelope. Deterministic apart from `valuation.latency`."""
    specs = instrument_specs(clock)
    cart = CART
    valuations = [valuation(spec, cart) for spec in specs]
    by_id = {v["instrument_id"]: v for v in valuations}

    ledger = MerkleLedger()
    for i in range(6):
        ledger.append("plumbline_prior", {"i": i, "note": "earlier receipt"}, clock + i)

    receipt = build_receipt([dict(v) for v in valuations], cart, ledger, clock + 100)

    headline = by_id[HEADLINE_INSTRUMENT]
    refusals = build_refusals(
        next(s.manifest for s in specs if s.manifest.manifest_id == HEADLINE_INSTRUMENT), cart
    )
    refusal_entries = []
    for case in refusals:
        entry = ledger.append(
            "plumbline_refusal",
            {
                "reason_code": case["reason_code"],
                "asserted_minor": case["asserted_minor"],
                "witness_hash": case["witness_hash"],
                "failures": [f["code"] for f in case["verification"]["failures"]],
                "cart_hash": cart.hash(),
            },
            clock + 200,
        )
        refusal_entries.append(entry)
        case["ledger_seq"] = entry.seq
        case["entry_hash"] = entry.entry_hash
        case["leaf_hash"] = entry.leaf_hash

    head_a = sign_head(ledger.root(), len(ledger), clock + 300)
    leaves_a = [e.leaf_hash for e in ledger.entries]
    receipt_proof = ledger.inclusion_proof(receipt["ledger_seq"])
    if receipt_proof is None or not receipt_proof.verify():
        raise AssertionError("the receipt's own inclusion proof does not verify")
    receipt["inclusion_proof"] = receipt_proof.to_dict()
    receipt["ledger_root"] = ledger.root()
    receipt["ledger_size"] = len(ledger)
    for case, entry in zip(refusals, refusal_entries):
        proof = ledger.inclusion_proof(entry.seq)
        if proof is None or not proof.verify():
            raise AssertionError("a refusal's inclusion proof does not verify")
        case["inclusion_proof"] = proof.to_dict()
        case["ledger_root"] = ledger.root()
        case["ledger_size"] = len(ledger)

    # -- honest extension ----------------------------------------------------------------
    honest_ledger = MerkleLedger(list(ledger.entries))
    for i in range(5):
        honest_ledger.append("plumbline_receipt", {"i": i, "note": "later receipt"}, clock + 400 + i)
    head_b = sign_head(honest_ledger.root(), len(honest_ledger), clock + 500)
    leaves_b = [e.leaf_hash for e in honest_ledger.entries]
    proof_ok = consistency_proof(len(leaves_a), leaves_b)
    if not verify_consistency(
        len(leaves_a), len(leaves_b), head_a["root_hash"], head_b["root_hash"], proof_ok
    ):
        raise AssertionError("the honest extension is not consistent with its own head")

    # -- the split view ------------------------------------------------------------------
    # A platform serving one log to the cardholder and another to the issuer has to rewrite
    # the receipt entry to drop an instrument from the candidate set. Rewriting the entry
    # changes its leaf, so the older head it already published is no longer an ancestor.
    omitted = headline
    edited_body = {
        "cart_hash": cart.hash(),
        "candidates": [
            {
                "instrument_id": v["instrument_id"],
                "asserted_minor": v["asserted_minor"],
                "witness_hash": v["witness_hash"],
                "manifest_hash": v["manifest_hash"],
                "issuer_signed": v["issuer_signed"],
            }
            for v in receipt["candidates"]
            if v["instrument_id"] != omitted["instrument_id"]
        ],
        "policy_hash": receipt["policy"]["policy_hash"],
        "selected": next(
            v["instrument_id"]
            for v in receipt["candidates"]
            if v["instrument_id"] != omitted["instrument_id"]
        ),
        "issued_at": clock + 100,
    }
    rebuilt = MerkleLedger()
    for e in ledger.entries:
        if e.seq == receipt["ledger_seq"]:
            rebuilt.append("plumbline_receipt", edited_body, e.ts)
        else:
            rebuilt.append(e.kind, e.payload, e.ts)
    for i in range(5):
        rebuilt.append("plumbline_receipt", {"i": i, "note": "later receipt"}, clock + 400 + i)
    head_b_edited = sign_head(rebuilt.root(), len(rebuilt), clock + 500)
    leaves_edited = [e.leaf_hash for e in rebuilt.entries]
    proof_bad = consistency_proof(len(leaves_a), leaves_edited)
    if verify_consistency(
        len(leaves_a),
        len(leaves_edited),
        head_a["root_hash"],
        head_b_edited["root_hash"],
        proof_bad,
    ):
        raise AssertionError(
            "the edited log must not be consistent with the head already published"
        )

    # -- latency, corpus, attribution ----------------------------------------------------
    latency = measure(specs, cart, reps=reps)
    corpus = build_corpus(specs, size=corpus_size)
    attribution_book = attribution(specs, corpus, clock)

    return {
        "generated_at": clock,
        "disclosure": DISCLOSURE,
        "cart": cart.to_dict(),
        "cart_hash": cart.hash(),
        "instruments": [
            {
                "instrument_id": s.manifest.manifest_id,
                "issuer": s.manifest.issuer,
                "product": s.manifest.product,
                "issuer_signed": s.issuer_signed,
                "source": s.manifest.source,
                "manifest": s.manifest.body(),
            }
            for s in specs
        ],
        "valuation": {
            "scenario_id": "india_trip_basket",
            "label": "Three nights abroad, one dinner, one flight, one fuel stop",
            "narrative": NARRATIVE,
            "headline_instrument": HEADLINE_INSTRUMENT,
            "instruments": valuations,
            "latency": latency,
        },
        "receipt": receipt,
        "refusals": refusals,
        "omission": {
            "narrative": (
                "A platform shows the issuer one log and the cardholder another. To drop an "
                "instrument from a candidate set it has to rewrite an entry that is already "
                "under a published head — and a rewritten entry is a different leaf."
            ),
            "omitted_instrument": {
                "instrument_id": omitted["instrument_id"],
                "issuer": omitted["issuer"],
                "product": omitted["product"],
                "asserted_minor": omitted["asserted_minor"],
                "asserted_display": omitted["asserted_display"],
            },
            "head_a": head_a,
            "head_b": head_b,
            "head_b_edited": head_b_edited,
            "consistency_honest": {
                "first_size": len(leaves_a),
                "second_size": len(leaves_b),
                "first_root": head_a["root_hash"],
                "second_root": head_b["root_hash"],
                "path": proof_ok,
            },
            "consistency_edited": {
                "first_size": len(leaves_a),
                "second_size": len(leaves_edited),
                "first_root": head_a["root_hash"],
                "second_root": head_b_edited["root_hash"],
                "path": proof_bad,
            },
            "receipt_seq": receipt["ledger_seq"],
            "candidates_published": [c["instrument_id"] for c in receipt["candidates"]],
            "candidates_served": [c["instrument_id"] for c in edited_body["candidates"]],
            "vectors": consistency_vectors(clock),
        },
        "attribution": attribution_book,
    }
