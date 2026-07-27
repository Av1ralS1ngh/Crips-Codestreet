"""Named, reproducible demo scenarios over the modelled card products.

Each scenario returns a plain JSON-serialisable dict, so the same call drives a terminal
table, a FastAPI response and a React panel with no translation layer. Every scenario runs
on `DEMO_CLOCK`, a chosen constant rather than a sampled one, and every measured field is
dropped on the way out, so two runs are byte-identical. That is what makes the demo safe to
rehearse and safe to diff.

This module is an ORCHESTRATOR, not an implementation. Valuation is `evaluate.evaluate`,
the signed record is `receipt.build_receipt_from_evaluation`, the log is `transparency`,
and the manifests are `products`. Nothing here re-derives a number those modules already
derive. What is added here is the staging: fixed inputs, an adversary, and the narration a
judge hears.

The five beats:

  overstatement     a cart where naive per-line summation claims value the card cannot
                    deliver, then the witness-backed number: lower, achievable, exhibited
                    as an allocation. The gap is attributed to individual (line, benefit)
                    pairings and the attribution reconciles exactly to the difference.
  refusal           two assertions no witness supports — a proposed value above what any
                    allocation realizes, and a forged witness whose assignments are each
                    correct and jointly over capacity. The evaluator declines to sign both,
                    with reason codes, anchored in the log.
  omission          a platform silently drops an instrument from the candidate set. The
                    receipt's own attestation against the mandate catches it; the inclusion
                    proof, the consistency proof and a split-view audit make the retroactive
                    version detectable and attributable.
  graceful_degrade  no counterpart receipt: the transaction PROCEEDS and the gap is recorded
                    as an unattested selection. Then the Card Member elects enforcement and
                    the same flow denies. Four passes, the whole matrix.
  cross_instrument  several instruments on one cart, ranked, each with its derivation, then
                    re-ranked with the annual credits drawn down.

Where the enforcement point sits, because it is the thing most easily misread: the receipt
obligation is a caveat on the mandate the Card Member issues to THEIR OWN AGENT. It is not
a condition an issuer imposes on a platform. The platform is asked for nothing and never
sees the check. An agent that cannot produce a receipt fails to discharge the cardholder's
own delegated authority — architecturally identical to a spend limit denying.

`oracle.py` is never imported here. This module is reachable from the API, and the oracle
refuses to sit behind a checkout budget.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from caveat.cart import Cart, CartLine

from . import transparency as tlog
from .evaluate import (
    AUTHOR_CARDHOLDER,
    CRITERION_MAX_ASSERTED,
    REFUSE_CLAIM_UNSUPPORTED,
    Evaluation,
    InstrumentValuation,
    ValuationPolicy,
    evaluate,
)
from .manifest import KIND_CREDIT, Manifest, SignedManifest, build_manifest, canonical_json, sign_manifest
from .products import (
    AMEX_GOLD_ID,
    AMEX_PLATINUM_ID,
    CAT_AIRFARE,
    CAT_AIRLINE_INCIDENTAL,
    CAT_DIGITAL_ENTERTAINMENT,
    CAT_DINING,
    CAT_HOTEL_DIRECT,
    CAT_INDIA_RETAIL,
    CAT_LULULEMON,
    CAT_PREPAID_HOTEL_AMEX,
    CAT_RESY_DINING,
    CAT_SMARTBUY_FLIGHT,
    CAT_SMARTBUY_HOTEL,
    CAT_US_SUPERMARKET,
    CHASE_SAPPHIRE_RESERVE_ID,
    DEFAULT_VALUATION,
    HDFC_INFINIA_ID,
    INR,
    USD,
    PointValuation,
    catalogue_by_id,
    fmt_currency,
)
from .receipt import (
    POSTURE_ENFORCE,
    POSTURE_OBSERVE_ONLY,
    REASON_SELECTION_ATTESTED,
    CheckoutSession,
    Identity,
    MandateBinding,
    SignedReceipt,
    anchor_receipt,
    assess_counterpart,
    build_receipt_from_evaluation,
    record_unattested_selection,
    sign_receipt,
    stable_evaluation_body,
    verify_receipt,
)
from .witness import Assignment, Witness, verify_witness

# --------------------------------------------------------------------------------------
# Fixed inputs. Chosen, not sampled.
# --------------------------------------------------------------------------------------

# 2026-07-27T00:00:00Z. Every manifest, receipt, log entry and signed head in this module is
# stamped from this constant, so a rehearsal and the live run produce identical bytes.
DEMO_CLOCK = 1_785_110_400

# Prototype keys. Production signs manifests with each issuer's HSM key, receipts with the
# agent's or the Card Member's, and tree heads with the log operator's. Canonicalisation
# and every verification flow are unchanged by the substitution.
DEMO_ISSUER_KEY = "plumbline-demo-issuer-key"
DEMO_ISSUER_KEY_ID = "plumbline-demo-issuer"
DEMO_RECEIPT_KEY = "plumbline-demo-agent-signing-key"
DEMO_LOG_KEY = "plumbline-demo-transparency-key"
DEMO_LOG_ID = "plumbline-demo-log"

ISSUER_KEYS: Mapping[str, str] = {DEMO_ISSUER_KEY_ID: DEMO_ISSUER_KEY}

AGENT = Identity(
    kind="agent",
    identifier="agent_plumbline_demo",
    name="the Card Member's shopping agent",
)
PLATFORM = Identity(
    kind="platform",
    identifier="platform_third_party_checkout",
    name="a third-party agentic checkout",
)

# The ranking rule. Its hash travels in the receipt because the criterion belongs to the
# Card Member, never to an issuer. No issuer signs "we beat a competitor on this cart",
# which is why the corpus contains no such signed assertion.
CARDHOLDER_POLICY = ValuationPolicy(
    policy_id="plumbline/demo/cardholder-policy/1",
    criterion=CRITERION_MAX_ASSERTED,
    author=AUTHOR_CARDHOLDER,
)

# --------------------------------------------------------------------------------------
# Reason codes local to the staging. Everything else is imported from the module that owns
# it, so a code that appears here appears nowhere else.
# --------------------------------------------------------------------------------------

PROBE_CLAIM_ABOVE_WITNESS = "PROBE_PROPOSED_VALUE_ABOVE_ANY_ALLOCATION"
PROBE_FORGED_WITNESS = "PROBE_FORGED_WITNESS_OVERDRAWS_SHARED_CAPACITY"

# Why a naive per-line figure exceeds what an allocation can realize.
GAP_CREDIT_BALANCE = "CREDIT_BALANCE_BELOW_LINE_AMOUNT"
GAP_EXCLUSIVITY = "EXCLUSIVITY_GROUP_ALREADY_CLAIMED"
GAP_CAPACITY = "BENEFIT_CAPACITY_EXHAUSTED"
GAP_LINE_OFFSET = "LINE_ALREADY_FULLY_OFFSET"


class ScenarioError(ValueError):
    """A staging mistake: an unknown scenario, a cart in the wrong currency, a bad id."""


# --------------------------------------------------------------------------------------
# Carts
# --------------------------------------------------------------------------------------

# A trip booked through Amex Travel. Every line is one an Amex Platinum benefit admits, so
# the naive figure is at its most seductive here — which is the point of demo beat one.
PLATINUM_TRIP_CART = Cart.of(
    "amextravel.com",
    [
        CartLine("plat_hotel", "Fine Hotels + Resorts, 3 nights, prepaid", 124_000, 7011, CAT_PREPAID_HOTEL_AMEX),
        CartLine("plat_resy", "Resy dinner, party of four", 45_200, 5812, CAT_RESY_DINING),
        CartLine("plat_air", "Delta round trip, booked direct", 68_400, 3000, CAT_AIRFARE),
        CartLine("plat_bags", "Delta checked bags and seat selection", 14_500, 3000, CAT_AIRLINE_INCIDENTAL),
        CartLine("plat_stream", "Streaming bundle, monthly", 3_200, 5815, CAT_DIGITAL_ENTERTAINMENT),
        CartLine("plat_lulu", "lululemon order", 12_800, 5651, CAT_LULULEMON),
    ],
    currency=USD,
)

# Channel-neutral: everything booked direct with the airline, the property or the
# restaurant. No card's own travel portal is favoured, so the comparison turns on published
# rates and broad credits rather than on which portal the cart happened to name.
USD_TRIP_CART = Cart.of(
    "m_travel_desk",
    [
        CartLine("usd_air", "United round trip SFO-BOS, booked direct", 74_200, 3000, CAT_AIRFARE),
        CartLine("usd_hotel", "Hotel, 3 nights, booked direct with the property", 91_800, 7011, CAT_HOTEL_DIRECT),
        CartLine("usd_dining", "Dinner for four", 28_400, 5812, CAT_DINING),
        CartLine("usd_grocery", "Weekly groceries", 19_600, 5411, CAT_US_SUPERMARKET),
        CartLine("usd_bags", "United checked bags and seat selection", 14_800, 3000, CAT_AIRLINE_INCIDENTAL),
    ],
    currency=USD,
)

# An India cart, booked through SmartBuy. Two accelerated hotel lines against one shared
# monthly point pool, which is what puts both a capacity bind and an exclusivity bind on
# the same cart.
INR_TRIP_CART = Cart.of(
    "smartbuy.hdfcbank.com",
    [
        CartLine("inr_hotel_a", "Mumbai hotel, 2 nights, via SmartBuy", 2_650_000, 7011, CAT_SMARTBUY_HOTEL),
        CartLine("inr_hotel_b", "Chennai hotel, 1 night, via SmartBuy", 1_420_000, 7011, CAT_SMARTBUY_HOTEL),
        CartLine("inr_flight", "BLR-DEL return, via SmartBuy", 1_840_000, 4722, CAT_SMARTBUY_FLIGHT),
        CartLine("inr_dinner", "Dinner for four", 680_000, 5812, CAT_DINING),
        CartLine("inr_retail", "Noise-cancelling headphones", 2_499_000, 5732, CAT_INDIA_RETAIL),
    ],
    currency=INR,
)

USD_CANDIDATE_SET = (AMEX_PLATINUM_ID, AMEX_GOLD_ID, CHASE_SAPPHIRE_RESERVE_ID)

# Annual credits, drawn to zero for the second pass of `cross_instrument`. A one-time
# annual credit can decide a single cart's ranking when nothing recurring would, so the
# same cart is valued again with them spent.
ANNUAL_CREDIT_IDS: Mapping[str, tuple[str, ...]] = {
    AMEX_PLATINUM_ID: ("amex_plat_credit_airline_fee",),
    AMEX_GOLD_ID: (),
    CHASE_SAPPHIRE_RESERVE_ID: (
        "csr_credit_travel",
        "csr_credit_the_edit_1",
        "csr_credit_the_edit_2",
        "csr_credit_dining_h2",
    ),
}


# --------------------------------------------------------------------------------------
# Staging helpers
# --------------------------------------------------------------------------------------


def signed_manifests(
    manifest_ids: Sequence[str],
    *,
    clock: int = DEMO_CLOCK,
    points: PointValuation = DEFAULT_VALUATION,
    drawn_down: bool = False,
) -> dict[str, SignedManifest]:
    """Build and issuer-sign the named products, in the order given."""
    catalogue = catalogue_by_id(clock, points)
    out: dict[str, SignedManifest] = {}
    for manifest_id in manifest_ids:
        if manifest_id not in catalogue:
            raise ScenarioError(
                f"no modelled product {manifest_id!r}; known: {', '.join(sorted(catalogue))}"
            )
        manifest = catalogue[manifest_id]
        if drawn_down:
            manifest = draw_down(manifest, ANNUAL_CREDIT_IDS.get(manifest_id, ()))
        out[manifest_id] = sign_manifest(manifest, DEMO_ISSUER_KEY, key_id=DEMO_ISSUER_KEY_ID)
    return out


def draw_down(manifest: Manifest, benefit_ids: Sequence[str]) -> Manifest:
    """The same product with the named credits spent to zero.

    Rebuilt through `build_manifest` so the result is validated exactly like a fresh
    manifest. The manifest_id is unchanged because it is still the same product; the
    content hash differs, which is correct — a manifest is a point-in-time snapshot of
    member state and two snapshots of one product should not collide.
    """
    declared = {b.benefit_id for b in manifest.benefits}
    unknown = [b for b in benefit_ids if b not in declared]
    if unknown:
        raise ScenarioError(
            f"manifest {manifest.manifest_id!r} declares no benefit(s) "
            f"{', '.join(unknown)}; check ANNUAL_CREDIT_IDS against the product builder"
        )
    targets = set(benefit_ids)
    return build_manifest(
        manifest_id=manifest.manifest_id,
        issuer=manifest.issuer,
        product=manifest.product,
        benefits=[
            replace(b, capacity_minor=0, note=f"{b.note}; drawn to zero for this pass")
            if b.benefit_id in targets
            else b
            for b in manifest.benefits
        ],
        issued_at=manifest.issued_at,
        currency=manifest.currency,
        source=manifest.source,
    )


def evaluate_cart(
    cart: Cart,
    manifests: Mapping[str, SignedManifest],
    *,
    clock: int = DEMO_CLOCK,
    claims: Mapping[str, int] | None = None,
) -> Evaluation:
    """Value a cart on every supplied instrument. Single-currency by construction.

    Ranking across currencies would need an FX rate, which is a market price this system
    does not carry and would be pretending to know, so a mismatch is refused here rather
    than silently converted downstream.
    """
    for signed in manifests.values():
        if signed.manifest.currency != cart.currency:
            raise ScenarioError(
                f"manifest {signed.manifest.manifest_id!r} is denominated in "
                f"{signed.manifest.currency} but the cart is in {cart.currency}; ranking "
                f"across currencies would need an FX rate this system does not carry. "
                f"Build a single-currency candidate set."
            )
    return evaluate(
        cart=cart,
        manifests=list(manifests.values()),
        now=clock,
        policy=CARDHOLDER_POLICY,
        keys=ISSUER_KEYS,
        claims=claims,
    )


def mandate_for(manifest_ids: Sequence[str], mandate_id: str) -> MandateBinding:
    """What the Card Member authorised. This is the omission detector's reference set.

    Its authority comes from the mandate the Card Member issued, not from the agent's
    say-so: an agent that shortens the candidate set is falsifying the mandate, not merely
    writing a thin receipt.
    """
    return MandateBinding(
        mandate_id=mandate_id,
        authorized_instrument_ids=tuple(manifest_ids),
        disclosure_caveat=True,
    )


def issue_receipt(
    evaluation: Evaluation,
    *,
    receipt_id: str,
    cart: Cart,
    manifests: Mapping[str, SignedManifest],
    mandate: MandateBinding,
    clock: int = DEMO_CLOCK,
    posture: str = POSTURE_OBSERVE_ONLY,
    session_id: str = "sess_plumbline_demo",
) -> SignedReceipt:
    """Build and sign the Decision Receipt for one evaluation.

    Signed by the agent. `sign_receipt` refuses the issuer role outright, because the body
    carries a ranking and no issuer signature may cover a comparison between instruments.
    """
    receipt = build_receipt_from_evaluation(
        receipt_id=receipt_id,
        issued_at=clock,
        evaluation=evaluation,
        session=CheckoutSession.of(session_id, cart, clock),
        mandate=mandate,
        agent=AGENT,
        platform=PLATFORM,
        signed_manifests=dict(manifests),
        chosen_instrument_id=(
            evaluation.ranking.chosen_manifest_id if evaluation.ranking else None
        ),
        posture=posture,
    )
    return sign_receipt(receipt, key=DEMO_RECEIPT_KEY)


def new_log() -> tlog.TransparencyLog:
    return tlog.TransparencyLog(DEMO_LOG_ID, signing_key=DEMO_LOG_KEY)


def publish_manifests(
    log: tlog.TransparencyLog, manifests: Iterable[SignedManifest], clock: int
) -> None:
    for signed in manifests:
        m = signed.manifest
        log.append(
            kind=tlog.ENTRY_MANIFEST_PUBLISHED,
            body={
                "manifest_id": m.manifest_id,
                "issuer": m.issuer,
                "product": m.product,
                "content_hash": m.content_hash(),
                "issuer_key_id": signed.key_id,
                "source": m.source,
            },
            timestamp=clock,
        )


# --------------------------------------------------------------------------------------
# Gap attribution — additive to `InstrumentValuation.overstatement_avoided_minor()`, which
# reports the scalar. This reports where each unit of it went.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GapItem:
    """One (line, benefit) pairing the naive sum counted and the allocation could not."""

    line_sku: str
    benefit_id: str
    label: str
    naive_minor: int
    witness_minor: int
    delta_minor: int
    reason: str

    def to_dict(self, currency: str) -> dict[str, Any]:
        return {
            "line_sku": self.line_sku,
            "benefit_id": self.benefit_id,
            "label": self.label,
            "naive_minor": self.naive_minor,
            "naive_display": fmt_currency(self.naive_minor, currency),
            "witness_minor": self.witness_minor,
            "witness_display": fmt_currency(self.witness_minor, currency),
            "delta_minor": self.delta_minor,
            "delta_display": fmt_currency(self.delta_minor, currency),
            "reason": self.reason,
        }


def attribute_gap(manifest: Manifest, cart: Cart, witness: Witness) -> tuple[GapItem, ...]:
    """Attribute every unit of (naive sum - realized value) to a (line, benefit) pairing.

    The deltas sum to the difference exactly, and `test_scenarios.py` asserts the identity
    on randomised carts. A decomposition that only roughly explained the gap would be a
    story about the number rather than a derivation of it, and the console renders it line
    by line.
    """
    by_sku = {line.sku: line for line in cart.lines}
    by_id = {b.benefit_id: b for b in manifest.benefits}
    assigned = {(a.line_sku, a.benefit_id): a.value_minor for a in witness.assignments}

    claimed_group: dict[tuple[str, str], str] = {}
    offset_used: dict[str, int] = {}
    for a in witness.assignments:
        benefit = by_id.get(a.benefit_id)
        if benefit is None:
            continue
        if benefit.exclusivity_group:
            claimed_group[(a.line_sku, benefit.exclusivity_group)] = a.benefit_id
        if benefit.kind == KIND_CREDIT:
            offset_used[a.line_sku] = offset_used.get(a.line_sku, 0) + a.value_minor

    items: list[GapItem] = []
    for benefit in manifest.priced():
        for line in cart.lines:
            naive_v = benefit.value_for_line(line, cart.merchant)
            if naive_v <= 0:
                continue
            witness_v = assigned.get((line.sku, benefit.benefit_id), 0)
            delta = naive_v - witness_v
            if delta == 0:
                continue
            group = benefit.exclusivity_group
            if witness_v > 0 and benefit.kind == KIND_CREDIT:
                reason = GAP_CREDIT_BALANCE
            elif group and claimed_group.get((line.sku, group), benefit.benefit_id) != benefit.benefit_id:
                reason = GAP_EXCLUSIVITY
            elif benefit.kind == KIND_CREDIT and offset_used.get(line.sku, 0) >= by_sku[line.sku].amount:
                reason = GAP_LINE_OFFSET
            else:
                reason = GAP_CAPACITY
            items.append(
                GapItem(
                    line_sku=line.sku,
                    benefit_id=benefit.benefit_id,
                    label=benefit.label,
                    naive_minor=naive_v,
                    witness_minor=witness_v,
                    delta_minor=delta,
                    reason=reason,
                )
            )
    items.sort(key=lambda g: (-g.delta_minor, g.line_sku, g.benefit_id))
    return tuple(items)


def relabel(obj: Any, currency: str) -> Any:
    """Rewrite `*_display` fields with the cart's own currency symbol.

    Every serializer in the valuation path now takes the currency explicitly, so on those
    objects this is a no-op. It still earns its place over the objects that come from the
    kernel — `Cart.to_dict`, the evidence and ledger bodies — which are INR by design and
    render through the defaulting `fmt_money`. Recomputing any `<name>_display` whose
    sibling `<name>_minor` is an integer covers them without reaching into the kernel's own
    serialisation, which the whole rest of the repository depends on being INR.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key.endswith("_display"):
                sibling = obj.get(f"{key[: -len('_display')]}_minor")
                if isinstance(sibling, int):
                    out[key] = fmt_currency(sibling, currency)
                    continue
            out[key] = relabel(value, currency)
        return out
    if isinstance(obj, list):
        return [relabel(v, currency) for v in obj]
    return obj


def candidate_view(valuation: InstrumentValuation, cart: Cart, manifest: Manifest) -> dict[str, Any]:
    """One instrument's result, shaped for the console.

    `elapsed_ms` is deliberately dropped: it measures the machine rather than the decision
    and is the only field of an evaluation that does not replay.
    """
    body = {k: v for k, v in valuation.to_dict().items() if k != "elapsed_ms"}
    gap_items = (
        attribute_gap(manifest, cart, valuation.witness)
        if valuation.witness is not None
        else ()
    )
    body["gap_items"] = [g.to_dict(cart.currency) for g in gap_items]
    body["gap_reconciles"] = sum(g.delta_minor for g in gap_items) == (
        valuation.naive_sum_minor - (valuation.asserted_minor or 0)
    )
    body["derivation_lines"] = (
        valuation.witness.derivation(manifest, cart)
        if valuation.witness is not None
        else []
    )
    body["considered_but_unpriced"] = [
        {"benefit_id": b.benefit_id, "label": b.label, "rationale": b.note}
        for b in manifest.unpriced()
    ]
    body["declared_but_unavailable"] = [
        {"benefit_id": b.benefit_id, "label": b.label, "note": b.note}
        for b in manifest.benefits
        if b.is_priced() and not b.available()
    ]
    return relabel(body, cart.currency)


# --------------------------------------------------------------------------------------
# Result envelope
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    title: str
    headline: str
    clock: int
    data: dict[str, Any]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "headline": self.headline,
            "clock": self.clock,
            "notes": list(self.notes),
            "data": self.data,
        }


def _cart_block(cart: Cart) -> dict[str, Any]:
    return {
        "cart": cart.to_dict(),
        "cart_total_minor": cart.total(),
        "cart_total_display": fmt_currency(cart.total(), cart.currency),
    }


def _receipt_block(
    signed: SignedReceipt,
    *,
    cart: Cart,
    manifests: Mapping[str, SignedManifest],
    log: tlog.TransparencyLog,
    clock: int,
) -> dict[str, Any]:
    anchored = anchor_receipt(log, signed, timestamp=clock)
    verification = verify_receipt(
        signed,
        DEMO_RECEIPT_KEY,
        cart=cart,
        manifests={mid: sm for mid, sm in manifests.items()},
        issuer_keys=ISSUER_KEYS,
    )
    ok, code = tlog.check_inclusion_proof(anchored.inclusion_proof, anchored.sth)
    return {
        "receipt": relabel(signed.to_dict(), cart.currency),
        "receipt_verification": verification.to_dict(),
        "receipt_verified": verification.ok,
        "anchor": {
            "seq": anchored.seq,
            "signed_tree_head": anchored.sth.to_dict(),
            "inclusion_proof": anchored.inclusion_proof.to_dict(),
            "inclusion_ok": ok,
            "inclusion_code": code,
        },
    }


# --------------------------------------------------------------------------------------
# 1. overstatement — demo beat one
# --------------------------------------------------------------------------------------


def overstatement(
    clock: int = DEMO_CLOCK, points: PointValuation = DEFAULT_VALUATION
) -> ScenarioResult:
    """Naive per-line summation claims value the card cannot deliver.

    On this cart the naive figure overstates for two reasons, both read off published terms
    rather than invented:

      * every statement credit is counted at the full line amount instead of at its
        remaining balance — a $300 hotel credit against a $1,240 booking;
      * the 1x base rate is added to the 5x category rate on the same line, when the
        published terms say 5x REPLACES 1x.

    A third effect is visible and contributes nothing to the arithmetic: the lululemon
    credit is enrollment-gated and this member is not enrolled, so it scores zero on both
    figures and appears only in the declared-but-unavailable list. The receipt proves the
    agent saw it.
    """
    manifests = signed_manifests([AMEX_PLATINUM_ID], clock=clock, points=points)
    manifest = manifests[AMEX_PLATINUM_ID].manifest
    cart = PLATINUM_TRIP_CART

    evaluation = evaluate_cart(cart, manifests, clock=clock)
    valuation = evaluation.candidates[0]
    mandate = mandate_for([AMEX_PLATINUM_ID], "mnd_plumbline_demo_single")
    signed = issue_receipt(
        evaluation,
        receipt_id="rcpt_overstatement",
        cart=cart,
        manifests=manifests,
        mandate=mandate,
        clock=clock,
    )

    log = new_log()
    publish_manifests(log, manifests.values(), clock)
    receipt_block = _receipt_block(
        signed, cart=cart, manifests=manifests, log=log, clock=clock
    )

    naive = valuation.naive_sum_minor
    asserted = valuation.asserted_minor or 0
    gap = valuation.overstatement_avoided_minor()

    return ScenarioResult(
        name="overstatement",
        title="The naive sum overstates",
        headline=(
            f"Per-line summation claims {fmt_currency(naive, cart.currency)}. The exhibited "
            f"allocation realizes {fmt_currency(asserted, cart.currency)} — lower, and "
            f"achievable."
        ),
        clock=clock,
        data={
            **_cart_block(cart),
            "instrument": candidate_view(valuation, cart, manifest),
            "naive_minor": naive,
            "naive_display": fmt_currency(naive, cart.currency),
            "asserted_minor": asserted,
            "asserted_display": fmt_currency(asserted, cart.currency),
            "overstatement_avoided_minor": gap,
            "overstatement_avoided_display": fmt_currency(gap, cart.currency),
            # Basis points of the achievable figure that naive summation would have claimed.
            "naive_over_witness_bp": (naive * 10_000) // max(asserted, 1),
            "evaluation": relabel(stable_evaluation_body(evaluation), cart.currency),
            **receipt_block,
        },
        notes=(
            "Every figure is in integer minor units; the displayed strings are presentation "
            "only and never enter the arithmetic.",
            "The witness is the derivation. Checking it is linear-time and needs no solver: "
            "re-add the values, then check each benefit against its declared capacity.",
            "Because the exhibited allocation is achievable, the asserted value cannot exceed "
            "the true optimum. Conservatism is proved by producing an allocation, not by an "
            "unsat proof.",
            "The allocator is conservative but not optimal. The offline oracle reports the "
            "measured gap to optimal; no claim of optimality is made here.",
        ),
    )


# --------------------------------------------------------------------------------------
# 2. refusal — demo beat two
# --------------------------------------------------------------------------------------


def refusal(
    clock: int = DEMO_CLOCK, points: PointValuation = DEFAULT_VALUATION
) -> ScenarioResult:
    """Two assertions no witness supports. The evaluator declines to sign, and says why.

    Probe one runs through the evaluator's own claim channel: a value is PROPOSED — by a
    model, a merchant, a cached figure — and used only as a hypothesis to reject. The best
    allocation the evaluator can exhibit realizes less, so the instrument is refused
    outright. There is deliberately no silent downgrade to the witness-backed figure.

    Probe two is a forged witness. It draws the SmartBuy accelerator on both hotel lines,
    with each individual assignment arithmetically correct, so nothing is wrong line by
    line. The two draws together exceed the shared monthly point pool, and only the capacity
    check catches it. The verifier then reports what survives — an achievable total, so a
    sound lower bound — rather than the forged figure or nothing at all.
    """
    manifests = signed_manifests([HDFC_INFINIA_ID], clock=clock, points=points)
    manifest = manifests[HDFC_INFINIA_ID].manifest
    cart = INR_TRIP_CART

    honest = evaluate_cart(cart, manifests, clock=clock)
    honest_valuation = honest.candidates[0]
    naive = honest_valuation.naive_sum_minor

    claimed = evaluate_cart(cart, manifests, clock=clock, claims={HDFC_INFINIA_ID: naive})
    claimed_valuation = claimed.candidates[0]

    forged_witness = forge_over_capacity(manifest, cart, honest_valuation.witness)
    forged_verification = verify_witness(
        witness=forged_witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=forged_witness.realized_minor(),
    )

    log = new_log()
    publish_manifests(log, manifests.values(), clock)

    probes = [
        {
            "probe": PROBE_CLAIM_ABOVE_WITNESS,
            "detail": (
                "a value equal to the naive per-line sum was proposed for this instrument"
            ),
            "signed": False,
            "status": claimed_valuation.status,
            "proposed_minor": naive,
            "proposed_display": fmt_currency(naive, cart.currency),
            "asserted_minor": claimed_valuation.asserted_minor,
            "refusal_codes": [r.code for r in claimed_valuation.refusals],
            "refusals": [r.to_dict() for r in claimed_valuation.refusals],
            "evaluation_refusal_codes": [r.code for r in claimed.refusals],
            "ranking": (
                None
                if claimed.ranking is None
                else claimed.ranking.to_dict(currency=cart.currency)
            ),
        },
        {
            "probe": PROBE_FORGED_WITNESS,
            "detail": (
                "every assignment is arithmetically correct; the draws jointly exceed the "
                "shared monthly accelerated-points pool"
            ),
            "signed": False,
            "status": None,
            "proposed_minor": forged_witness.realized_minor(),
            "proposed_display": fmt_currency(forged_witness.realized_minor(), cart.currency),
            "asserted_minor": None,
            "refusal_codes": list(forged_verification.codes()),
            "verifier_lower_bound_minor": forged_verification.realized_minor,
            "verifier_lower_bound_display": fmt_currency(
                forged_verification.realized_minor, cart.currency
            ),
            "verification": forged_verification.to_dict(currency=cart.currency),
            "witness": forged_witness.to_dict(currency=cart.currency),
        },
    ]

    for probe in probes:
        log.append(
            kind=tlog.ENTRY_REFUSAL,
            body={
                "manifest_id": manifest.manifest_id,
                "manifest_content_hash": manifest.content_hash(),
                "cart_hash": cart.hash(),
                "probe": probe["probe"],
                "refusal_codes": probe["refusal_codes"],
                "proposed_minor": probe["proposed_minor"],
            },
            timestamp=clock,
        )

    sth = log.signed_tree_head(timestamp=clock)
    return ScenarioResult(
        name="refusal",
        title="Refusal is a typed, first-class output",
        headline=(
            f"Two assertions on the same cart, both declined: "
            f"{', '.join(sorted({c for p in probes for c in p['refusal_codes']}))}."
        ),
        clock=clock,
        data={
            **_cart_block(cart),
            "supported_assertion": candidate_view(honest_valuation, cart, manifest),
            "probes": probes,
            "signed_tree_head": sth.to_dict(),
            "log_entries": [e.to_dict() for e in log.entries],
        },
        notes=(
            "The LLM proposes; the deterministic evaluator disposes. A proposed value is only "
            "ever a hypothesis to reject — it never becomes a number in a signed artifact.",
            "A refusal is anchored in the log exactly like a signature. A system that visibly "
            "declines to make a claim is the strongest available answer to a demand that "
            "vaporware be punished.",
            "On the forged witness every individual assignment is arithmetically correct. Only "
            "the shared capacity catches it, which is why capacity is a resource the verifier "
            "tracks rather than a rule the producer is trusted to have followed.",
            "The verifier reports what survives every check. That total is itself achievable, "
            "so a failed verification still yields a sound lower bound rather than a number "
            "that could be mistaken for a supported assertion.",
        ),
    )


def forge_over_capacity(manifest: Manifest, cart: Cart, honest: Witness | None) -> Witness:
    """Build a witness whose assignments are each correct and jointly over capacity.

    Attaches the capped non-credit benefit with the most headroom to every line it admits,
    dropping anything its exclusivity group would then conflict with. This is the adversary
    a per-assignment verifier misses.
    """
    capped = [
        b for b in manifest.priced() if b.capacity_minor is not None and b.kind != KIND_CREDIT
    ]
    if not capped:
        raise ScenarioError(
            f"manifest {manifest.manifest_id!r} has no capped non-credit benefit to "
            f"overdraw; the forged-witness probe needs one"
        )
    target = max(capped, key=lambda b: (b.capacity_minor or 0, b.benefit_id))
    group = target.exclusivity_group

    forged: list[Assignment] = []
    claimed: set[str] = set()
    for line in cart.lines:
        if not target.eligibility.admits(line, cart.merchant):
            continue
        value = target.value_for_line(line, cart.merchant)
        if value <= 0:
            continue
        forged.append(
            Assignment(
                line_sku=line.sku,
                benefit_id=target.benefit_id,
                consumed_minor=value,
                value_minor=value,
            )
        )
        claimed.add(line.sku)

    by_id = {b.benefit_id: b for b in manifest.benefits}
    for a in honest.assignments if honest is not None else ():
        if a.benefit_id == target.benefit_id:
            continue
        other = by_id.get(a.benefit_id)
        if a.line_sku in claimed and other is not None and other.exclusivity_group == group:
            continue
        forged.append(a)

    forged.sort(key=lambda a: (a.line_sku, a.benefit_id))
    return Witness(
        manifest_id=manifest.manifest_id, cart_hash=cart.hash(), assignments=tuple(forged)
    )


# --------------------------------------------------------------------------------------
# 3. omission — demo beat three
# --------------------------------------------------------------------------------------


def omission(
    clock: int = DEMO_CLOCK, points: PointValuation = DEFAULT_VALUATION
) -> ScenarioResult:
    """A platform silently drops an instrument from the candidate set.

    Omission is the attack. An agent that never evaluates an instrument leaves no trace in
    any log that records only what was chosen, which is why the receipt records the full
    candidate set and attests it against the mandate.

    Four independent detections here, escalating from cheap to non-repudiable:

      1. the receipt attests its own candidate set against the mandate and reports
         CANDIDATE_SET_INCOMPLETE, naming the instrument that went missing;
      2. the honest receipt's inclusion proof fails against the edited head;
      3. the edited log cannot produce a consistency proof from an already-published head,
         so the retroactive version is detectable — the RFC 6962 mechanism that has
         underpinned public-web TLS trust for a decade, with no chain, token or consensus;
      4. two signed heads at the same size with different roots is a split view, which
         makes it attributable rather than merely visible.
    """
    manifests = signed_manifests(USD_CANDIDATE_SET, clock=clock, points=points)
    cart = USD_TRIP_CART
    dropped = AMEX_PLATINUM_ID
    mandate = mandate_for(USD_CANDIDATE_SET, "mnd_plumbline_demo_wallet")

    honest_eval = evaluate_cart(cart, manifests, clock=clock)
    thin_manifests = {k: v for k, v in manifests.items() if k != dropped}
    edited_eval = evaluate_cart(cart, thin_manifests, clock=clock)

    honest_receipt = issue_receipt(
        honest_eval,
        receipt_id="rcpt_omission",
        cart=cart,
        manifests=manifests,
        mandate=mandate,
        clock=clock,
    )
    # The mandate is unchanged: the Card Member authorised three instruments either way.
    # That is what makes the shortened candidate set an attestable fault rather than a
    # difference of opinion about scope.
    edited_receipt = issue_receipt(
        edited_eval,
        receipt_id="rcpt_omission",
        cart=cart,
        manifests=thin_manifests,
        mandate=mandate,
        clock=clock,
    )

    honest_log = new_log()
    edited_log = new_log()
    publish_manifests(honest_log, manifests.values(), clock)
    publish_manifests(edited_log, manifests.values(), clock)

    receipt_seq = len(honest_log)
    honest_log.append(kind=tlog.ENTRY_RECEIPT, body=honest_receipt.to_dict(), timestamp=clock)
    edited_log.append(kind=tlog.ENTRY_RECEIPT, body=edited_receipt.to_dict(), timestamp=clock)

    honest_head = honest_log.signed_tree_head(timestamp=clock)
    edited_head = edited_log.signed_tree_head(timestamp=clock)

    # Both logs then extend identically, so nothing after the edit distinguishes them.
    for log in (honest_log, edited_log):
        log.append(
            kind=tlog.ENTRY_UNATTESTED_SELECTION,
            body={"cart_hash": cart.hash(), "reason": "a later checkout produced no receipt"},
            timestamp=clock + 60,
        )

    honest_inclusion = honest_log.inclusion_proof(receipt_seq, tree_size=honest_head.tree_size)
    against_honest = tlog.check_inclusion_proof(honest_inclusion, honest_head)
    against_edited = tlog.check_inclusion_proof(honest_inclusion, edited_head)

    # `honest_head` is passed explicitly on both sides. `prove_extends` already takes its
    # first root from the published head, so these would pass without it; naming the head
    # anyway is what makes the demo show the check a relying party actually runs. A proof
    # verified against a root the prover supplied is a claim about itself — an edited log's
    # proof about its own edited past verifies perfectly well.
    honest_consistency = tlog.check_consistency_proof(
        honest_log.prove_extends(honest_head), honest_head
    )
    edited_consistency = tlog.check_consistency_proof(
        edited_log.prove_extends(honest_head), honest_head
    )

    audit = tlog.audit_pair(
        auditor_id="plumbline-witness",
        issuer_view=honest_head,
        cardholder_view=edited_head,
        key=DEMO_LOG_KEY,
    )

    missing = sorted(
        set(mandate.authorized_instrument_ids)
        - {c.instrument_id for c in edited_receipt.receipt.candidates}
    )

    return ScenarioResult(
        name="omission",
        title="Omission leaves a signature",
        headline=(
            f"The edited receipt names {len(edited_receipt.receipt.candidates)} candidates "
            f"where the mandate authorised {len(mandate.authorized_instrument_ids)}. Its own "
            f"attestation reports {edited_receipt.receipt.attestation.outcome}, and the log "
            f"audit reports {audit.outcome}."
        ),
        clock=clock,
        data={
            **_cart_block(cart),
            "mandate": mandate.to_dict(),
            "dropped_manifest_id": dropped,
            "missing_from_edited_receipt": missing,
            "attestation": {
                "honest": honest_receipt.receipt.attestation.to_dict(),
                "edited": edited_receipt.receipt.attestation.to_dict(),
            },
            "heads": {"honest": honest_head.to_dict(), "edited": edited_head.to_dict()},
            "inclusion": {
                "proof": honest_inclusion.to_dict(),
                "against_honest_head": {"ok": against_honest[0], "code": against_honest[1]},
                "against_edited_head": {"ok": against_edited[0], "code": against_edited[1]},
            },
            "consistency": {
                "honest_extends_published_head": {
                    "ok": honest_consistency[0],
                    "code": honest_consistency[1],
                },
                "edited_extends_published_head": {
                    "ok": edited_consistency[0],
                    "code": edited_consistency[1],
                },
            },
            "audit": audit.to_dict(),
            "audit_text": audit.render_text(),
            "honest_receipt": relabel(honest_receipt.to_dict(), cart.currency),
            "edited_receipt": relabel(edited_receipt.to_dict(), cart.currency),
        },
        notes=(
            "The receipt records the full candidate set and attests it against the mandate, so "
            "an agent that dropped an instrument must either say so in a signed record or "
            "produce no receipt at all.",
            "The attestation certifies compliance as readily as violation. A receipt that could "
            "only catch cheating is something a platform routes around; one that can prove a "
            "faithful ranking is something a platform wants to display.",
            "A consistency proof makes retroactive editing detectable; a witness comparing two "
            "published heads kills the split view in which a platform shows one party one log "
            "and another party a different one.",
            "This is the mechanism underpinning public-web TLS trust for a decade. No chain, no "
            "token, no consensus.",
        ),
    )


# --------------------------------------------------------------------------------------
# 4. graceful_degrade — demo beat four
# --------------------------------------------------------------------------------------


def graceful_degrade(
    clock: int = DEMO_CLOCK, points: PointValuation = DEFAULT_VALUATION
) -> ScenarioResult:
    """No counterpart receipt: the transaction proceeds. Then enforcement is elected.

    Default posture is observe-only. The evaluator computes and signs, the receipt is
    emitted, and the absence of a counterpart receipt is recorded as an unattested
    selection — never a decline. A credential that hard-failed inside a third-party checkout
    would be routed around, and a platform routing around it is the risk this exists to
    reduce, self-administered.

    What is withheld instead is coverage: no receipt, no Agent Purchase Protection. Coverage
    is conditioned on evidence; authorization is not.

    Enforcement is a mode the Card Member elects, and the enforcement point is a caveat on
    the mandate the Card Member issued to their own agent. The platform is asked for nothing
    and never sees the check. An agent that cannot produce a receipt fails to discharge the
    cardholder's own delegated authority, which is architecturally identical to a spend
    limit denying — and that is exactly what a governance layer is asked to do.
    """
    manifests = signed_manifests(USD_CANDIDATE_SET, clock=clock, points=points)
    cart = USD_TRIP_CART
    mandate = mandate_for(USD_CANDIDATE_SET, "mnd_plumbline_demo_wallet")
    evaluation = evaluate_cart(cart, manifests, clock=clock)
    signed = issue_receipt(
        evaluation,
        receipt_id="rcpt_graceful_degrade",
        cart=cart,
        manifests=manifests,
        mandate=mandate,
        clock=clock,
    )
    session = CheckoutSession.of("sess_plumbline_demo", cart, clock)

    log = new_log()
    publish_manifests(log, manifests.values(), clock)

    passes = []
    ts = clock
    for posture in (POSTURE_OBSERVE_ONLY, POSTURE_ENFORCE):
        for counterpart in (True, False):
            ts += 30
            assessment = assess_counterpart(
                receipt=signed if counterpart else None,
                posture=posture,
                session=session,
                mandate=mandate,
                assessed_at=ts,
            )
            if assessment.reason_code == REASON_SELECTION_ATTESTED:
                seq = anchor_receipt(log, signed, timestamp=ts).seq
            else:
                seq = record_unattested_selection(log, assessment, timestamp=ts)
            passes.append(
                {
                    "posture": posture,
                    "counterpart_receipt": counterpart,
                    "proceeds": assessment.proceeds,
                    "reason_code": assessment.reason_code,
                    "detail": assessment.detail,
                    "coverage_eligible": assessment.coverage_eligible,
                    "log_seq": seq,
                    "assessment": assessment.to_dict(),
                }
            )

    sth = log.signed_tree_head(timestamp=ts)
    denied = [p for p in passes if not p["proceeds"]]
    return ScenarioResult(
        name="graceful_degrade",
        title="Observe by default, enforce by election",
        headline=(
            f"Four passes: {len(passes) - len(denied)} proceed, {len(denied)} deny. The only "
            f"denial is the one the Card Member elected, and it denies against their own "
            f"mandate, not against the platform."
        ),
        clock=clock,
        data={
            **_cart_block(cart),
            "mandate": mandate.to_dict(),
            "chosen_instrument_id": signed.receipt.chosen_instrument_id,
            "passes": passes,
            "log_entry_kinds": dict(sorted(Counter(e.kind for e in log.entries).items())),
            "reason_code_counts": dict(
                sorted(Counter(p["reason_code"] for p in passes).items())
            ),
            "signed_tree_head": sth.to_dict(),
            "receipt": relabel(signed.to_dict(), cart.currency),
        },
        notes=(
            "Absence of a counterpart receipt is logged as an unattested selection, never as a "
            "decline. A corpus with no record of its own holes cannot be used to reason about "
            "coverage.",
            "No receipt, no Agent Purchase Protection coverage. Coverage is conditioned on "
            "evidence; authorization is not.",
            "The receipt obligation is a caveat on the mandate the Card Member issues to their "
            "own agent, not a condition imposed on a platform.",
            "Three of these four passes are a signed record that the ranking was performed "
            "faithfully, which is what makes the receipt something a platform would display.",
        ),
    )


# --------------------------------------------------------------------------------------
# 5. cross_instrument — the one to hand a judge
# --------------------------------------------------------------------------------------


def cross_instrument(
    clock: int = DEMO_CLOCK, points: PointValuation = DEFAULT_VALUATION
) -> ScenarioResult:
    """Three instruments, one channel-neutral cart, ranked, each with its derivation.

    Read the second pass before quoting the first. A one-time annual credit drawn to zero on
    a single cart can decide a ranking that nothing recurring would decide, so the same cart
    is valued again with the annual credits spent. Both rankings are in the result, because
    a record that showed only the flattering one would be the thing this system exists to
    make impossible.

    No issuer signs either ranking. The manifests carry issuer-signed facts; the criterion
    and the valuation policy belong to the Card Member and are recorded by hash. That is why
    a competitor leading a cart produces no issuer-signed assertion that it did.
    """
    cart = USD_TRIP_CART
    mandate = mandate_for(USD_CANDIDATE_SET, "mnd_plumbline_demo_wallet")

    fresh = signed_manifests(USD_CANDIDATE_SET, clock=clock, points=points)
    spent = signed_manifests(USD_CANDIDATE_SET, clock=clock, points=points, drawn_down=True)

    first = evaluate_cart(cart, fresh, clock=clock)
    second = evaluate_cart(cart, spent, clock=clock)

    signed = issue_receipt(
        first,
        receipt_id="rcpt_cross_instrument",
        cart=cart,
        manifests=fresh,
        mandate=mandate,
        clock=clock,
    )
    log = new_log()
    publish_manifests(log, fresh.values(), clock)
    receipt_block = _receipt_block(signed, cart=cart, manifests=fresh, log=log, clock=clock)

    return ScenarioResult(
        name="cross_instrument",
        title="Several instruments, one cart, every derivation shown",
        headline=(
            f"With annual credits available: {_ranking_line(first, cart)}. "
            f"With them spent: {_ranking_line(second, cart)}."
        ),
        clock=clock,
        data={
            **_cart_block(cart),
            "mandate": mandate.to_dict(),
            "valuation_policy": CARDHOLDER_POLICY.to_dict(),
            "ranking_with_annual_credits": (
                first.ranking.to_dict(currency=cart.currency) if first.ranking else None
            ),
            "ranking_annual_credits_spent": (
                second.ranking.to_dict(currency=cart.currency) if second.ranking else None
            ),
            "candidates_with_annual_credits": [
                candidate_view(c, cart, fresh[c.manifest_id].manifest) for c in first.candidates
            ],
            "candidates_annual_credits_spent": [
                candidate_view(c, cart, spent[c.manifest_id].manifest) for c in second.candidates
            ],
            "order_changed": _order(first) != _order(second),
            "attestation": signed.receipt.attestation.to_dict(),
            "evaluation": relabel(stable_evaluation_body(first), cart.currency),
            **receipt_block,
        },
        notes=(
            "The ranking and the valuation policy belong to the Card Member or the agent. Their "
            "hashes are recorded; no issuer endorses either, and no issuer signs a comparison "
            "against a competitor.",
            "The first ranking is driven partly by annual credits that a single cart draws to "
            "zero. The second pass is the steady-state answer, and both are in the record.",
            "Annual fees are carried on the product profile and never netted against a cart. A "
            "per-cart valuation says nothing about whether a card earns its fee.",
            "Non-numeric value — lounge access, status, service, contingent cover — is declared "
            "as considered-but-unpriced on every candidate, so the integer never claims to be "
            "the whole worth of the card.",
        ),
    )


def _order(evaluation: Evaluation) -> tuple[str, ...]:
    if evaluation.ranking is None:
        return ()
    return tuple(e.manifest_id for e in evaluation.ranking.entries)


def _ranking_line(evaluation: Evaluation, cart: Cart) -> str:
    if evaluation.ranking is None:
        return "no instrument produced a verified witness"
    by_id = {c.manifest_id: c for c in evaluation.candidates}
    return " > ".join(
        f"{by_id[e.manifest_id].product} {fmt_currency(e.asserted_minor, cart.currency)}"
        for e in evaluation.ranking.entries
    )


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------

SCENARIOS: dict[str, Callable[..., ScenarioResult]] = {
    "overstatement": overstatement,
    "refusal": refusal,
    "omission": omission,
    "graceful_degrade": graceful_degrade,
    "cross_instrument": cross_instrument,
}


def run_scenario(name: str, **kwargs: Any) -> ScenarioResult:
    if name not in SCENARIOS:
        raise ScenarioError(
            f"unknown plumbline scenario {name!r}; known: {', '.join(sorted(SCENARIOS))}"
        )
    return SCENARIOS[name](**kwargs)


def run(name: str, **kwargs: Any) -> dict[str, Any]:
    """The API and console entry point. Returns a plain JSON-serialisable dict."""
    return run_scenario(name, **kwargs).to_dict()


def run_all(**kwargs: Any) -> dict[str, dict[str, Any]]:
    """Every scenario, in registry order. Byte-identical across runs at a fixed clock."""
    return {name: run(name, **kwargs) for name in SCENARIOS}


def fingerprint(**kwargs: Any) -> str:
    """A hash over every scenario's output. Two runs that differ here are not reproducible."""
    return hashlib.sha256(canonical_json(run_all(**kwargs))).hexdigest()
