"""Tests for plumbline.evaluate — the witness-backed valuation and its refusals.

Three properties carry the whole argument and each has adversarial cover here:

  * the asserted value is below a naive per-line sum and is supported by its own witness;
  * an unsupported claim is REFUSED, never quietly downgraded;
  * an issuer-attestable body of one instrument is byte-identical whether or not a
    competitor was evaluated alongside it.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from caveat.adapters import AdapterError, normalize
from caveat.cart import Cart, CartLine
from plumbline import evaluate as evaluate_module
from plumbline.allocate import naive_sum
from plumbline.evaluate import (
    BENEFIT_APPLIED,
    BENEFIT_ELIGIBLE_UNUSED,
    BENEFIT_EXHAUSTED,
    BENEFIT_INELIGIBLE,
    BENEFIT_NOT_ENROLLED,
    BENEFIT_UNPRICED,
    CRITERION_MAX_ASSERTED,
    CRITERION_MAX_PROTECTION_THEN_VALUE,
    DISCLOSE_ENROLLMENT_GATED,
    DISCLOSE_LINE_DETAIL_MISSING,
    DISCLOSE_NOT_PROVEN_OPTIMAL,
    DISCLOSE_UNPRICED_VALUE,
    REFUSE_CLAIM_UNSUPPORTED,
    REFUSE_CURRENCY_MISMATCH,
    REFUSE_DUPLICATE_MANIFEST,
    REFUSE_DUPLICATE_SKU,
    REFUSE_EMPTY_CART,
    REFUSE_MANIFEST_KEY_UNKNOWN,
    REFUSE_MANIFEST_POSTDATED,
    REFUSE_MANIFEST_SIGNATURE,
    REFUSE_MANIFEST_STALE,
    REFUSE_MANIFEST_UNSIGNED,
    REFUSE_NO_ATTESTABLE_INSTRUMENT,
    STATUS_ATTESTED,
    STATUS_REFUSED,
    EvaluationError,
    ValuationPolicy,
    build_derivation,
    evaluate,
    evaluate_normalized,
    evaluate_payload,
)
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    WINDOW_ANNUAL,
    WINDOW_MONTHLY,
    Benefit,
    Eligibility,
    Manifest,
    build_manifest,
    canonical_json,
    sign_manifest,
)
from plumbline.witness import verify_witness

T0 = 1_753_600_000
KEY = "prototype-issuer-secret"
KEY_ID = "issuer-2026"
KEYS = {KEY_ID: KEY}

DINNER = CartLine(
    sku="s_dinner", description="Tasting menu for two", amount=1_200_000, mcc=5812, category="dining"
)
HOTEL = CartLine(
    sku="s_hotel", description="One night, city centre", amount=2_500_000, mcc=7011, category="travel"
)
LAPTOP = CartLine(
    sku="s_laptop", description="14-inch laptop", amount=1_800_000, mcc=5732, category="electronics"
)


def cart() -> Cart:
    return Cart.of("m_resy", [DINNER, HOTEL])


def platinum(issued_at: int = T0 - 3_600) -> Manifest:
    """Two dining credits competing for one dinner — the cart the demo opens on."""
    return build_manifest(
        manifest_id="amex_platinum",
        issuer="American Express",
        product="Platinum",
        issued_at=issued_at,
        benefits=[
            Benefit(
                benefit_id="b_dining_credit",
                kind=KIND_CREDIT,
                label="Monthly dining credit",
                eligibility=Eligibility(mccs=(5812,)),
                capacity_minor=500_000,
                exclusivity_group="dining",
                window=WINDOW_MONTHLY,
            ),
            Benefit(
                benefit_id="b_resy_credit",
                kind=KIND_CREDIT,
                label="Resy dining credit",
                eligibility=Eligibility(mccs=(5812,)),
                capacity_minor=300_000,
                exclusivity_group="dining",
                window=WINDOW_MONTHLY,
            ),
            Benefit(
                benefit_id="b_travel_earn",
                kind=KIND_EARN,
                label="5x on travel",
                eligibility=Eligibility(mccs=(7011,)),
                rate_bp=500,
                capacity_minor=200_000,
                window=WINDOW_ANNUAL,
            ),
            Benefit(
                benefit_id="b_purchase_protection",
                kind=KIND_PROTECTION,
                label="Purchase protection",
                flat_minor=25_000,
            ),
            Benefit(
                benefit_id="b_electronics_offer",
                kind=KIND_CREDIT,
                label="Electronics offer",
                eligibility=Eligibility(mccs=(5732,)),
                capacity_minor=100_000,
                requires_enrollment=True,
                enrolled=False,
            ),
            Benefit(
                benefit_id="b_exhausted_credit",
                kind=KIND_CREDIT,
                label="Spent airline credit",
                eligibility=Eligibility(mccs=(7011,)),
                capacity_minor=0,
            ),
            Benefit(
                benefit_id="b_lounge",
                kind=KIND_UNPRICED,
                label="Centurion Lounge access",
                note="membership value; deliberately not priced",
            ),
        ],
    )


def flat_card(issued_at: int = T0 - 3_600) -> Manifest:
    return build_manifest(
        manifest_id="rival_flat_2pct",
        issuer="Rival Bank",
        product="Flat 2%",
        issued_at=issued_at,
        benefits=[
            Benefit(benefit_id="b_flat", kind=KIND_EARN, label="2% on everything", rate_bp=200)
        ],
    )


def protection_card(issued_at: int = T0 - 3_600) -> Manifest:
    return build_manifest(
        manifest_id="rival_cover",
        issuer="Rival Bank",
        product="Cover",
        issued_at=issued_at,
        benefits=[
            Benefit(
                benefit_id="b_cover",
                kind=KIND_PROTECTION,
                label="Trip cover",
                eligibility=Eligibility(mccs=(7011,)),
                flat_minor=80_000,
            )
        ],
    )


def signed(manifest: Manifest):
    return sign_manifest(manifest, KEY, key_id=KEY_ID)


# ----------------------------------------------------------------------------------
# Beat 1 — the naive sum overstates; the witness-backed number is provable.
# ----------------------------------------------------------------------------------


def test_witness_backed_value_sits_below_the_naive_sum():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    v = ev.valuation("amex_platinum")

    assert v.status == STATUS_ATTESTED
    assert v.asserted_minor < v.naive_sum_minor
    assert v.overstatement_avoided_minor() > 0
    assert v.naive_sum_minor == naive_sum(platinum(), cart())


def test_two_credits_cannot_both_claim_the_same_line():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    v = ev.valuation("amex_platinum")
    claimed = [a.benefit_id for a in v.witness.assignments if a.line_sku == DINNER.sku]

    assert "b_dining_credit" in claimed
    assert "b_resy_credit" not in claimed
    # The larger credit wins the line and yields its balance, not the line's whole amount.
    assert v.witness.realized_minor() == 500_000 + 125_000 + 25_000 * 2


def test_asserted_value_is_supported_by_its_own_witness():
    """Re-verify independently: linear-time, no solver, nothing but the manifest and cart."""
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    v = ev.valuation("amex_platinum")

    check = verify_witness(
        witness=v.witness, manifest=platinum(), cart=cart(), asserted_minor=v.asserted_minor
    )
    assert check.ok
    assert check.supports_assertion


def test_asserted_value_never_exceeds_naive_sum_across_many_random_carts():
    """Conservatism, over 200 pseudo-random problems. Seeded, so failures reproduce."""
    rng = random.Random(20260825)
    for _ in range(200):
        manifest, c = _random_problem(rng)
        ev = evaluate(
            cart=c,
            manifests=[manifest],
            now=T0,
            policy=ValuationPolicy(require_signed_manifest=False),
        )
        v = ev.candidates[0]
        assert v.status == STATUS_ATTESTED, [r.to_dict() for r in v.refusals]
        assert 0 <= v.asserted_minor <= v.naive_sum_minor
        assert v.derivation.consistent()
        assert v.derivation.value_minor == v.asserted_minor
        assert verify_witness(
            witness=v.witness, manifest=manifest, cart=c, asserted_minor=v.asserted_minor
        ).supports_assertion


def _random_problem(rng: random.Random) -> tuple[Manifest, Cart]:
    mccs = (5812, 7011, 5732, 5411)
    lines = [
        CartLine(
            sku=f"s{i}",
            description=f"line {i}",
            amount=rng.randrange(1_000, 5_000_000),
            mcc=rng.choice(mccs),
            category=rng.choice(("dining", "travel", "electronics", "groceries")),
        )
        for i in range(rng.randrange(1, 7))
    ]
    benefits = []
    for i in range(rng.randrange(1, 8)):
        kind = rng.choice((KIND_EARN, KIND_CREDIT, KIND_PROTECTION))
        benefits.append(
            Benefit(
                benefit_id=f"b{i}",
                kind=kind,
                label=f"benefit {i}",
                eligibility=Eligibility(mccs=tuple(rng.sample(mccs, rng.randrange(1, 4)))),
                rate_bp=rng.choice((0, 100, 200, 500, 1_000)) if kind == KIND_EARN else 0,
                capacity_minor=rng.choice((None, 0, 50_000, 250_000, 1_000_000)),
                flat_minor=rng.choice((0, 10_000, 50_000)) if kind == KIND_PROTECTION else 0,
                exclusivity_group=rng.choice((None, "g1", "g2")),
                requires_enrollment=rng.random() < 0.2,
                enrolled=rng.random() < 0.5,
            )
        )
    manifest = build_manifest(
        manifest_id="rand",
        issuer="Test",
        product="Random",
        benefits=benefits,
        issued_at=T0 - 10,
    )
    return manifest, Cart.of("m_test", lines)


# ----------------------------------------------------------------------------------
# The derivation tree.
# ----------------------------------------------------------------------------------


def test_derivation_totals_the_witness_and_is_internally_consistent():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    v = ev.valuation("amex_platinum")

    assert v.derivation.consistent()
    assert v.derivation.value_minor == v.witness.realized_minor() == v.asserted_minor
    assert sum(n.value_minor for n in v.derivation.leaves()) == v.asserted_minor


def test_derivation_records_every_benefit_that_did_not_apply_and_why():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    nodes = {n.facts["benefit_id"]: n for n in ev.valuation("amex_platinum").derivation.children}

    assert nodes["b_dining_credit"].status == BENEFIT_APPLIED
    assert nodes["b_resy_credit"].status == BENEFIT_ELIGIBLE_UNUSED
    assert "b_dining_credit" in nodes["b_resy_credit"].detail
    assert nodes["b_electronics_offer"].status == BENEFIT_NOT_ENROLLED
    assert nodes["b_exhausted_credit"].status == BENEFIT_EXHAUSTED
    assert nodes["b_lounge"].status == BENEFIT_UNPRICED
    assert nodes["b_lounge"].value_minor == 0


def test_ineligible_benefit_is_marked_ineligible_not_unused():
    c = Cart.of("m_croma", [LAPTOP])
    manifest = build_manifest(
        manifest_id="m_dining_only",
        issuer="Test",
        product="Dining",
        issued_at=T0 - 10,
        benefits=[
            Benefit(
                benefit_id="b_dining",
                kind=KIND_CREDIT,
                label="Dining credit",
                eligibility=Eligibility(mccs=(5812,)),
                capacity_minor=100_000,
            )
        ],
    )
    ev = evaluate(
        cart=c, manifests=[manifest], now=T0, policy=ValuationPolicy(require_signed_manifest=False)
    )
    node = ev.candidates[0].derivation.children[0]
    assert node.status == BENEFIT_INELIGIBLE
    assert ev.candidates[0].asserted_minor == 0


def test_build_derivation_is_callable_on_a_witness_directly():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    v = ev.valuation("amex_platinum")
    rebuilt = build_derivation(platinum(), cart(), v.witness)
    assert rebuilt.to_dict(currency=v.currency) == v.derivation.to_dict(currency=v.currency)
    assert rebuilt.render_lines(currency=v.currency)[0].startswith("American Express Platinum")
    # No float ever renders into a label that gets hashed.
    earn = next(n for n in rebuilt.walk() if n.facts.get("benefit_id") == "b_travel_earn")
    assert "at 5% on" in earn.children[0].label


# ----------------------------------------------------------------------------------
# Beat 2 — refusal is a typed, first-class output.
# ----------------------------------------------------------------------------------


def test_unsupported_claim_is_refused_and_never_silently_downgraded():
    ev = evaluate(
        cart=cart(),
        manifests=[signed(platinum())],
        now=T0,
        keys=KEYS,
        claims={"amex_platinum": 9_000_000},
    )
    v = ev.valuation("amex_platinum")

    assert v.status == STATUS_REFUSED
    assert v.asserted_minor is None, "a refused instrument asserts nothing at all"
    assert [r.code for r in v.refusals] == [REFUSE_CLAIM_UNSUPPORTED]
    assert v.claimed_minor == 9_000_000
    assert ev.ranking is None
    assert not ev.signable
    # The remedy names the number a witness would actually support.
    assert "6,750" in v.refusals[0].remedy


def test_a_claim_within_reach_of_the_witness_is_attested():
    ev = evaluate(
        cart=cart(),
        manifests=[signed(platinum())],
        now=T0,
        keys=KEYS,
        claims={"amex_platinum": 100_000},
    )
    v = ev.valuation("amex_platinum")
    assert v.status == STATUS_ATTESTED
    assert v.asserted_minor == 675_000


def test_a_refused_instrument_still_appears_in_the_candidate_set():
    """Omission is the attack. A refusal that deleted the candidate would be one."""
    ev = evaluate(
        cart=cart(),
        manifests=[signed(platinum()), signed(flat_card())],
        now=T0,
        keys=KEYS,
        claims={"amex_platinum": 9_000_000},
    )
    assert {c.manifest_id for c in ev.candidates} == {"amex_platinum", "rival_flat_2pct"}
    assert [e.manifest_id for e in ev.ranking.entries] == ["rival_flat_2pct"]


def test_zero_value_is_attested_not_refused():
    """Zero is a supported assertion: the empty allocation realizes it. Refusal is for
    claims no witness backs, not for instruments worth nothing on this cart."""
    ev = evaluate(
        cart=Cart.of("m_croma", [LAPTOP]),
        manifests=[signed(protection_card())],
        now=T0,
        keys=KEYS,
    )
    v = ev.valuation("rival_cover")
    assert v.status == STATUS_ATTESTED
    assert v.asserted_minor == 0
    assert v.witness.assignments == ()


def test_every_candidate_refused_leaves_an_evaluation_level_refusal():
    tampered = signed(platinum())
    ev = evaluate(cart=cart(), manifests=[tampered], now=T0, keys={KEY_ID: "wrong-key"})

    assert not ev.signable
    assert ev.ranking is None
    assert [r.code for r in ev.refusals] == [REFUSE_NO_ATTESTABLE_INSTRUMENT]
    assert [r.code for r in ev.valuation("amex_platinum").refusals] == [REFUSE_MANIFEST_SIGNATURE]


def inflated_after_signing():
    """A signed manifest whose dining credit was inflated after the signature was made."""
    original = platinum()
    sm = signed(original)
    forged = Manifest(
        manifest_id=original.manifest_id,
        issuer=original.issuer,
        product=original.product,
        currency=original.currency,
        benefits=tuple(
            b
            if b.benefit_id != "b_dining_credit"
            else Benefit(
                **{**b.to_dict(), "eligibility": b.eligibility, "capacity_minor": 99_000_000}
            )
            for b in original.benefits
        ),
        issued_at=original.issued_at,
    )
    return type(sm)(manifest=forged, signature=sm.signature, key_id=sm.key_id)


def test_manifest_mutated_after_signing_is_refused():
    ev = evaluate(cart=cart(), manifests=[inflated_after_signing()], now=T0, keys=KEYS)
    assert [r.code for r in ev.valuation("amex_platinum").refusals] == [REFUSE_MANIFEST_SIGNATURE]


def test_a_broken_signature_is_refused_even_when_the_policy_does_not_require_one():
    """`require_signed_manifest=False` makes signing optional. It does not make a signature
    that fails ignorable."""
    ev = evaluate(
        cart=cart(),
        manifests=[inflated_after_signing()],
        now=T0,
        keys=KEYS,
        policy=ValuationPolicy(require_signed_manifest=False),
    )
    refusal = ev.valuation("amex_platinum").refusals[0]
    assert refusal.code == REFUSE_MANIFEST_SIGNATURE
    assert refusal.describe().startswith(REFUSE_MANIFEST_SIGNATURE)


def test_an_unverifiable_signature_is_admitted_only_when_the_policy_says_signing_is_optional():
    permissive = ValuationPolicy(require_signed_manifest=False)
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys={}, policy=permissive)
    assert ev.valuation("amex_platinum").status == STATUS_ATTESTED


def test_unsigned_manifest_is_refused_by_default_and_admitted_when_policy_says_so():
    refusing = evaluate(cart=cart(), manifests=[platinum()], now=T0, keys=KEYS)
    assert [r.code for r in refusing.valuation("amex_platinum").refusals] == [
        REFUSE_MANIFEST_UNSIGNED
    ]

    admitting = evaluate(
        cart=cart(),
        manifests=[platinum()],
        now=T0,
        policy=ValuationPolicy(require_signed_manifest=False),
    )
    assert admitting.valuation("amex_platinum").status == STATUS_ATTESTED


def test_manifest_signed_under_an_unknown_key_is_refused():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys={})
    refusal = ev.valuation("amex_platinum").refusals[0]
    assert refusal.code == REFUSE_MANIFEST_KEY_UNKNOWN
    assert KEY_ID in refusal.remedy


def test_stale_and_postdated_manifests_are_refused():
    stale = evaluate(
        cart=cart(),
        manifests=[signed(platinum(issued_at=T0 - 10_000))],
        now=T0,
        keys=KEYS,
        policy=ValuationPolicy(max_manifest_age_s=3_600),
    )
    assert [r.code for r in stale.valuation("amex_platinum").refusals] == [REFUSE_MANIFEST_STALE]

    postdated = evaluate(
        cart=cart(), manifests=[signed(platinum(issued_at=T0 + 10))], now=T0, keys=KEYS
    )
    assert [r.code for r in postdated.valuation("amex_platinum").refusals] == [
        REFUSE_MANIFEST_POSTDATED
    ]


def test_currency_mismatch_is_refused_rather_than_converted():
    usd = build_manifest(
        manifest_id="usd_card",
        issuer="Test",
        product="USD",
        currency="USD",
        issued_at=T0 - 10,
        benefits=[Benefit(benefit_id="b", kind=KIND_EARN, label="1%", rate_bp=100)],
    )
    ev = evaluate(cart=cart(), manifests=[signed(usd)], now=T0, keys=KEYS)
    assert [r.code for r in ev.valuation("usd_card").refusals] == [REFUSE_CURRENCY_MISMATCH]


def test_duplicate_manifest_id_refuses_the_second_copy():
    ev = evaluate(cart=cart(), manifests=[signed(platinum()), signed(platinum())], now=T0, keys=KEYS)
    statuses = [c.status for c in ev.candidates]
    assert statuses.count(STATUS_ATTESTED) == 1
    assert REFUSE_DUPLICATE_MANIFEST in {r.code for c in ev.candidates for r in c.refusals}


def test_empty_cart_and_duplicate_skus_are_refused_before_any_allocation():
    empty = evaluate(cart=Cart.of("m_resy", []), manifests=[signed(platinum())], now=T0, keys=KEYS)
    assert [r.code for r in empty.refusals] == [REFUSE_EMPTY_CART]
    assert empty.candidates == ()

    dupes = evaluate(
        cart=Cart.of("m_resy", [DINNER, DINNER]),
        manifests=[signed(platinum())],
        now=T0,
        keys=KEYS,
    )
    assert [r.code for r in dupes.refusals] == [REFUSE_DUPLICATE_SKU]
    assert "fold repeated SKUs" in dupes.refusals[0].remedy


def test_all_refusals_collects_both_levels():
    ev = evaluate(cart=cart(), manifests=[platinum()], now=T0, keys=KEYS)
    codes = {r.code for r in ev.all_refusals()}
    assert codes == {REFUSE_MANIFEST_UNSIGNED, REFUSE_NO_ATTESTABLE_INSTRUMENT}


# ----------------------------------------------------------------------------------
# The signature boundary.
# ----------------------------------------------------------------------------------


def test_an_instruments_attestable_body_is_unchanged_by_the_presence_of_a_competitor():
    """The structural form of 'no issuer signs that a competitor won'.

    If adding a rival changed one byte of what the issuer could sign, the signature would
    be over a comparison.
    """
    alone = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    contested = evaluate(
        cart=cart(), manifests=[signed(platinum()), signed(flat_card())], now=T0, keys=KEYS
    )

    a = alone.valuation("amex_platinum").attestable_body()
    b = contested.valuation("amex_platinum").attestable_body()
    assert canonical_json(a) == canonical_json(b)
    assert alone.valuation("amex_platinum").attestable_hash() == contested.valuation(
        "amex_platinum"
    ).attestable_hash()


def test_no_attestable_body_mentions_another_instrument_a_rank_or_a_policy():
    ev = evaluate(
        cart=cart(), manifests=[signed(platinum()), signed(flat_card())], now=T0, keys=KEYS
    )
    for v in ev.candidates:
        blob = json.dumps(v.attestable_body())
        others = {c.manifest_id for c in ev.candidates} - {v.manifest_id}
        for other in others:
            assert other not in blob
        for forbidden in ("rank", "ranking", "policy", "baseline", "incremental", "criterion"):
            assert forbidden not in blob


def test_ranking_is_never_issuer_endorsed_and_carries_the_policy_hash():
    policy = ValuationPolicy(policy_id="cardholder_v3", baseline_earn_bp=100)
    ev = evaluate(
        cart=cart(),
        manifests=[signed(platinum()), signed(flat_card())],
        now=T0,
        keys=KEYS,
        policy=policy,
    )
    assert ev.ranking.issuer_endorsed is False
    assert ev.ranking.policy_hash == policy.policy_hash()
    assert "ranking" not in json.dumps(ev.attestable_body())
    assert ev.ranking.chosen_manifest_id == "amex_platinum"
    assert ev.ranking.margin_minor == 675_000 - 74_000


def test_incremental_value_is_measured_against_the_policy_baseline():
    policy = ValuationPolicy(baseline_earn_bp=100)  # 1% assumed from whatever else they hold
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS, policy=policy)
    entry = ev.ranking.entries[0]

    assert entry.baseline_minor == cart().total() * 100 // 10_000
    assert entry.incremental_minor == entry.asserted_minor - entry.baseline_minor


def test_criterion_changes_the_order_and_is_recorded():
    manifests = [signed(platinum()), signed(protection_card())]
    by_value = evaluate(
        cart=cart(),
        manifests=manifests,
        now=T0,
        keys=KEYS,
        policy=ValuationPolicy(criterion=CRITERION_MAX_ASSERTED),
    )
    by_cover = evaluate(
        cart=cart(),
        manifests=manifests,
        now=T0,
        keys=KEYS,
        policy=ValuationPolicy(criterion=CRITERION_MAX_PROTECTION_THEN_VALUE),
    )

    assert by_value.ranking.chosen_manifest_id == "amex_platinum"
    assert by_cover.ranking.chosen_manifest_id == "rival_cover"
    assert by_cover.ranking.criterion == CRITERION_MAX_PROTECTION_THEN_VALUE


def test_min_value_to_rank_keeps_a_worthless_instrument_out_of_the_order():
    policy = ValuationPolicy(min_value_to_rank_minor=30_000)
    ev = evaluate(
        cart=Cart.of("m_croma", [LAPTOP]),
        manifests=[signed(platinum()), signed(flat_card())],
        now=T0,
        keys=KEYS,
        policy=policy,
    )
    # Platinum's electronics offer is enrollment-gated, so all it scores on this cart is
    # ₹250 of purchase protection — below the floor the cardholder set.
    assert [e.manifest_id for e in ev.ranking.entries] == ["rival_flat_2pct"]
    assert ev.valuation("amex_platinum").status == STATUS_ATTESTED


def test_policy_rejects_an_issuer_author_and_an_unknown_criterion():
    with pytest.raises(EvaluationError, match="belongs to"):
        ValuationPolicy(author="issuer")
    with pytest.raises(EvaluationError, match="unknown ranking criterion"):
        ValuationPolicy(criterion="whatever_the_merchant_prefers")
    with pytest.raises(EvaluationError, match="baseline_earn_bp"):
        ValuationPolicy(baseline_earn_bp=-1)
    with pytest.raises(EvaluationError, match="max_manifest_age_s"):
        ValuationPolicy(max_manifest_age_s=-1)
    with pytest.raises(EvaluationError, match="min_value_to_rank_minor"):
        ValuationPolicy(min_value_to_rank_minor=-1)


def test_evaluate_rejects_a_non_integer_clock_and_a_non_manifest_candidate():
    with pytest.raises(EvaluationError, match="now must be epoch seconds"):
        evaluate(cart=cart(), manifests=[signed(platinum())], now=float(T0), keys=KEYS)
    with pytest.raises(EvaluationError, match="Manifest or SignedManifest"):
        evaluate(cart=cart(), manifests=[{"manifest_id": "x"}], now=T0, keys=KEYS)


# ----------------------------------------------------------------------------------
# Determinism and disclosures.
# ----------------------------------------------------------------------------------


def test_evaluation_replays_byte_for_byte():
    args = dict(cart=cart(), manifests=[signed(platinum()), signed(flat_card())], now=T0, keys=KEYS)
    first = evaluate(**args)
    second = evaluate(**args)

    assert canonical_json(first.attestable_body()) == canonical_json(second.attestable_body())
    assert first.ranking.to_dict(currency=first.cart.currency) == second.ranking.to_dict(
        currency=second.cart.currency
    )


def test_candidates_are_ordered_by_manifest_id_not_by_input_order():
    forward = evaluate(
        cart=cart(), manifests=[signed(platinum()), signed(flat_card())], now=T0, keys=KEYS
    )
    reverse = evaluate(
        cart=cart(), manifests=[signed(flat_card()), signed(platinum())], now=T0, keys=KEYS
    )
    assert [c.manifest_id for c in forward.candidates] == ["amex_platinum", "rival_flat_2pct"]
    assert canonical_json(forward.attestable_body()) == canonical_json(reverse.attestable_body())


def test_money_fields_are_integers_everywhere_in_the_output():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    floats = _floats_under_money_keys(ev.to_dict())
    assert floats == [], floats


def _floats_under_money_keys(obj, path="$"):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("_minor") or k.endswith("_bp"):
                if isinstance(v, float):
                    out.append(f"{path}.{k}")
            out.extend(_floats_under_money_keys(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_floats_under_money_keys(v, f"{path}[{i}]"))
    return out


def test_disclosures_name_the_direction_of_every_limitation():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    codes = {d.code for d in ev.valuation("amex_platinum").disclosures}
    assert DISCLOSE_NOT_PROVEN_OPTIMAL in codes
    assert DISCLOSE_ENROLLMENT_GATED in codes
    assert DISCLOSE_UNPRICED_VALUE in codes
    # Nothing may ever disclose an inflation; an inflation is a refusal.
    assert all(d.direction in ("understates", "neutral") for d in ev.valuation("amex_platinum").disclosures)


def test_unpriced_benefits_are_listed_but_never_scored():
    ev = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS)
    v = ev.valuation("amex_platinum")
    assert v.unpriced_labels == ("Centurion Lounge access",)
    assert all(
        n.value_minor == 0 for n in v.derivation.children if n.status == BENEFIT_UNPRICED
    )


def test_render_text_shows_the_refusal_and_the_naive_comparison():
    ev = evaluate(
        cart=cart(),
        manifests=[signed(platinum())],
        now=T0,
        keys=KEYS,
        claims={"amex_platinum": 9_000_000},
    )
    text = ev.render_text()
    assert REFUSE_CLAIM_UNSUPPORTED in text
    assert "NO RANKING" in text

    clean = evaluate(cart=cart(), manifests=[signed(platinum())], now=T0, keys=KEYS).render_text()
    assert "naive sum would claim" in clean
    assert "not issuer-endorsed" in clean


# ----------------------------------------------------------------------------------
# Protocol wiring — a real ACP checkout session shape.
# ----------------------------------------------------------------------------------


def acp_payload(*, mcc: int | None = 5812) -> dict:
    item = {
        "id": "li_1",
        "item": {"id": "s_dinner", "name": "Tasting menu for two"},
        "base_amount": 1_200_000,
        "subtotal": 1_200_000,
        "total": 1_200_000,
        "category": "dining",
    }
    if mcc is not None:
        item["merchant_category_code"] = mcc
    return {
        "shared_payment_token": {
            "id": "spt_1QxAbC",
            "payment_method": {"type": "card", "card_number_type": "network_token"},
            "allowance": {
                "reason": "one_time",
                "max_amount": 1_500_000,
                "currency": "inr",
                "merchant_id": "m_resy",
                "checkout_session_id": "cs_test_123",
            },
            "agent": {"id": "op_shopbot"},
        },
        "checkout_session": {
            "id": "cs_test_123",
            "currency": "inr",
            "merchant": {"id": "m_resy"},
            "line_items": [item],
            "totals": [{"type": "total", "amount": 1_200_000}],
        },
    }


def test_an_acp_checkout_session_flows_straight_into_a_valuation():
    ev = evaluate_payload(acp_payload(), [signed(platinum())], now=T0, keys=KEYS)

    assert ev.protocol == "acp"
    assert ev.cart_hash == Cart.of("m_resy", [DINNER]).hash()
    assert ev.valuation("amex_platinum").asserted_minor == 500_000 + 25_000


def test_acp_line_without_an_mcc_is_disclosed_as_understating():
    ev = evaluate_payload(acp_payload(mcc=None), [signed(platinum())], now=T0, keys=KEYS)
    matching = [d for d in ev.disclosures if d.code == DISCLOSE_LINE_DETAIL_MISSING]

    assert len(matching) == 1, "the cart-level and protocol-level notes collapse to one"
    assert matching[0].direction == "understates"
    assert "s_dinner" in matching[0].detail
    # The dining credit is keyed on MCC 5812 and cannot attach to a line that carries none.
    assert ev.valuation("amex_platinum").asserted_minor == 25_000


def test_the_executed_cart_is_valued_in_preference_to_signed_intent():
    payload = acp_payload()
    payload["executed_order"] = {
        "id": "ord_1",
        "currency": "inr",
        "merchant": {"id": "m_resy"},
        "line_items": [
            {
                "id": "li_1",
                "item": {"id": "s_hotel", "name": "One night, city centre"},
                "total": 2_500_000,
                "merchant_category_code": 7011,
                "category": "travel",
            }
        ],
    }
    request = normalize(payload)
    ev = evaluate_normalized(request, [signed(platinum())], now=T0, keys=KEYS)

    assert ev.cart_hash == request.executed_cart.hash()
    # Travel earn plus protection, not the dining credit the intent cart would have used.
    assert ev.valuation("amex_platinum").asserted_minor == 125_000 + 25_000


def test_a_payload_no_adapter_recognises_raises_rather_than_valuing_nothing():
    with pytest.raises(AdapterError):
        evaluate_payload({"nothing": "useful"}, [signed(platinum())], now=T0, keys=KEYS)


def test_the_adapter_constants_this_module_mirrors_have_not_drifted():
    from caveat.adapters import base

    assert evaluate_module.UNKNOWN_MCC == base.UNKNOWN_MCC
    assert evaluate_module.WARN_MISSING_LINE_DETAIL == base.WARN_MISSING_LINE_DETAIL


def test_importing_the_valuation_path_loads_no_solver():
    """The no-solver claim is about the import graph too, so it is checked, not asserted."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, plumbline.evaluate, plumbline.attribution;"
            "print([m for m in sys.modules if m == 'z3' or m.startswith('z3.')])",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", f"a solver reached the valuation path: {proc.stdout}"
