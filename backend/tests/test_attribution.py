"""Tests for plumbline.attribution — the receipt corpus read as a benefit-cutting instrument.

The load-bearing assertions here are about what the module refuses to say: it counts a
benefit as available only where it could actually attach, refuses to classify a benefit it
has barely seen, refuses to place one with no issuer-supplied cost, and carries no member
identifier anywhere in its output.
"""

from __future__ import annotations

import json

import pytest

from caveat.cart import Cart, CartLine
from plumbline.attribution import (
    ACTION_BY_QUADRANT,
    CLAIM_BOUNDARY,
    NEVER_A_NUDGE,
    PIVOTAL_DEFINITION,
    QUADRANT_DEAD_WEIGHT,
    QUADRANT_INSUFFICIENT_EVIDENCE,
    QUADRANT_LOAD_BEARING,
    QUADRANT_NO_COST_BASIS,
    QUADRANT_NOISE,
    QUADRANT_OPTION,
    AttributionError,
    AttributionSettings,
    BenefitKey,
    attribute,
    median_cost_minor,
    observe_evaluation,
    observe_receipt,
)
from plumbline.evaluate import ValuationPolicy, evaluate
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    Benefit,
    Eligibility,
    Manifest,
    build_manifest,
    sign_manifest,
)

T0 = 1_753_600_000
UNSIGNED = ValuationPolicy(require_signed_manifest=False)

DINING_CREDIT = BenefitKey("American Express", "Platinum", "b_dining_credit").as_str()
RESY_CREDIT = BenefitKey("American Express", "Platinum", "b_resy_credit").as_str()
PROTECTION = BenefitKey("American Express", "Platinum", "b_protect").as_str()
GATED = BenefitKey("American Express", "Platinum", "b_gated_offer").as_str()
FLAT = BenefitKey("Rival Bank", "Flat 2%", "b_flat").as_str()

COSTS = {
    DINING_CREDIT: 30_000_000,
    RESY_CREDIT: 25_000_000,
    PROTECTION: 100_000,
    FLAT: 200_000,
}


def platinum() -> Manifest:
    return build_manifest(
        manifest_id="amex_platinum",
        issuer="American Express",
        product="Platinum",
        issued_at=T0 - 60,
        benefits=[
            Benefit(
                benefit_id="b_dining_credit",
                kind=KIND_CREDIT,
                label="Monthly dining credit",
                eligibility=Eligibility(mccs=(5812,)),
                capacity_minor=500_000,
                exclusivity_group="dining",
            ),
            Benefit(
                benefit_id="b_resy_credit",
                kind=KIND_CREDIT,
                label="Resy dining credit",
                eligibility=Eligibility(mccs=(5812,)),
                capacity_minor=300_000,
                exclusivity_group="dining",
            ),
            Benefit(
                benefit_id="b_protect",
                kind=KIND_PROTECTION,
                label="Purchase protection",
                flat_minor=25_000,
            ),
            Benefit(
                benefit_id="b_gated_offer",
                kind=KIND_CREDIT,
                label="Unenrolled dining offer",
                eligibility=Eligibility(mccs=(5812,)),
                capacity_minor=100_000,
                requires_enrollment=True,
                enrolled=False,
            ),
            Benefit(
                benefit_id="b_lounge",
                kind=KIND_UNPRICED,
                label="Lounge access",
                note="not priced",
            ),
        ],
    )


def rival_flat() -> Manifest:
    return build_manifest(
        manifest_id="rival_flat",
        issuer="Rival Bank",
        product="Flat 2%",
        issued_at=T0 - 60,
        benefits=[
            Benefit(benefit_id="b_flat", kind=KIND_EARN, label="2% on everything", rate_bp=200)
        ],
    )


def rival_cover(flat_minor: int) -> Manifest:
    return build_manifest(
        manifest_id="rival_cover",
        issuer="Rival Bank",
        product="Cover",
        issued_at=T0 - 60,
        benefits=[
            Benefit(
                benefit_id="b_cover",
                kind=KIND_PROTECTION,
                label="Blanket cover",
                flat_minor=flat_minor,
            )
        ],
    )


def dining_line(i: int, amount: int = 1_200_000) -> CartLine:
    return CartLine(
        sku=f"s_dinner_{i}",
        description=f"Dinner {i}",
        amount=amount,
        mcc=5812,
        category="dining",
    )


def decide(
    lines,
    *,
    decision_id: str,
    manifests,
    chosen: str | None = None,
    merchant: str = "m_resy",
    claims=None,
):
    ev = evaluate(
        cart=Cart.of(merchant, lines),
        manifests=manifests,
        now=T0,
        policy=UNSIGNED,
        claims=claims,
    )
    return observe_evaluation(ev, decision_id=decision_id, chosen_manifest_id=chosen)


def corpus(n: int = 8):
    """n contested dining decisions, all won by the Platinum manifest."""
    return [
        decide(
            [dining_line(i)],
            decision_id=f"d{i}",
            manifests=[platinum(), rival_flat()],
        )
        for i in range(n)
    ]


# ----------------------------------------------------------------------------------
# Counting.
# ----------------------------------------------------------------------------------


def test_a_benefit_in_the_winning_derivation_is_counted_decisive():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    rows = {e.key.as_str(): e for e in report.entries}

    dining = rows[DINING_CREDIT]
    assert (dining.in_play, dining.applied, dining.winning) == (8, 8, 8)
    assert dining.decisive_rate_bp == 10_000

    flat = rows[FLAT]
    assert (flat.in_play, flat.applied, flat.winning) == (8, 8, 0)
    assert flat.decisive_rate_bp == 0
    assert report.decisions == 8
    assert report.contested_decisions == 8


def test_available_but_displaced_is_in_play_and_never_decisive():
    """The dead-weight signal: a second dining credit the allocator never reaches."""
    rows = {e.key.as_str(): e for e in attribute(corpus(), now=T0, cost_basis=COSTS).entries}
    resy = rows[RESY_CREDIT]

    assert resy.in_play == 8
    assert resy.applied == 0
    assert resy.winning == 0
    assert resy.applied_rate_bp == 0
    assert resy.decisive_rate_bp == 0


def test_a_benefit_that_could_not_attach_is_not_counted_against_itself():
    """A dining credit on an electronics cart was never in play; the denominator says so."""
    laptop = CartLine(
        sku="s_laptop", description="Laptop", amount=1_000_000, mcc=5732, category="electronics"
    )
    obs = [
        decide([laptop], decision_id=f"e{i}", manifests=[platinum(), rival_flat()], merchant="m_croma")
        for i in range(6)
    ]
    rows = {e.key.as_str(): e for e in attribute(obs, now=T0, cost_basis=COSTS).entries}

    assert rows[DINING_CREDIT].in_play == 0
    assert rows[DINING_CREDIT].quadrant == QUADRANT_INSUFFICIENT_EVIDENCE
    # Protection has no eligibility predicate, so it was genuinely in play and decisive.
    assert rows[PROTECTION].in_play == 6
    assert rows[PROTECTION].winning == 6


def test_benefits_on_a_refused_instrument_are_not_counted_in_play():
    obs = [
        decide(
            [dining_line(i)],
            decision_id=f"r{i}",
            manifests=[platinum(), rival_flat()],
            claims={"amex_platinum": 99_000_000},
        )
        for i in range(6)
    ]
    rows = {e.key.as_str(): e for e in attribute(obs, now=T0, cost_basis=COSTS).entries}

    assert rows[DINING_CREDIT].in_play == 0
    assert rows[FLAT].in_play == 6
    assert rows[FLAT].winning == 6


def test_unpriced_benefits_are_recorded_in_the_receipt_but_never_scored():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    assert all(e.key.benefit_id != "b_lounge" for e in report.entries)


# ----------------------------------------------------------------------------------
# The pivotal test.
# ----------------------------------------------------------------------------------


def test_a_benefit_is_pivotal_only_when_it_covers_the_margin():
    # Platinum: ₹5,000 credit + ₹250 protection = ₹5,250. Rival cover: ₹5,000 flat.
    # Margin is ₹250, so the dining credit covers it and the protection exactly does not.
    obs = [
        decide(
            [dining_line(i)],
            decision_id=f"p{i}",
            manifests=[platinum(), rival_cover(500_000)],
        )
        for i in range(6)
    ]
    report = attribute(obs, now=T0, cost_basis=COSTS)
    rows = {e.key.as_str(): e for e in report.entries}

    assert rows[DINING_CREDIT].pivotal == 6
    assert rows[DINING_CREDIT].pivotal_rate_bp == 10_000
    assert rows[PROTECTION].winning == 6
    assert rows[PROTECTION].pivotal == 0, "a contribution equal to the margin is not pivotal"


def test_a_wide_margin_leaves_nothing_pivotal():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    rows = {e.key.as_str(): e for e in report.entries}
    assert rows[DINING_CREDIT].winning == 8
    assert rows[DINING_CREDIT].pivotal == 0


def test_an_uncontested_decision_supports_no_pivotal_claim():
    obs = [
        decide([dining_line(i)], decision_id=f"s{i}", manifests=[platinum()]) for i in range(6)
    ]
    report = attribute(obs, now=T0, cost_basis=COSTS)
    rows = {e.key.as_str(): e for e in report.entries}

    assert report.contested_decisions == 0
    assert rows[DINING_CREDIT].winning == 6
    assert rows[DINING_CREDIT].contested_in_play == 0
    assert rows[DINING_CREDIT].pivotal == 0
    assert rows[DINING_CREDIT].pivotal_rate_bp == 0


def test_choosing_against_the_ranking_credits_no_benefit_with_the_decision():
    """The agent picked the instrument its own criterion ranked second. Whatever decided
    that, it was not these numbers, and no benefit gets credit for it."""
    obs = [
        decide(
            [dining_line(i)],
            decision_id=f"x{i}",
            manifests=[platinum(), rival_flat()],
            chosen="rival_flat",
        )
        for i in range(6)
    ]
    report = attribute(obs, now=T0, cost_basis=COSTS)
    rows = {e.key.as_str(): e for e in report.entries}

    assert rows[FLAT].winning == 6
    assert rows[FLAT].pivotal == 0
    assert rows[FLAT].contested_in_play == 0
    assert rows[DINING_CREDIT].winning == 0


# ----------------------------------------------------------------------------------
# The 2x2.
# ----------------------------------------------------------------------------------


def test_the_corpus_places_every_benefit_in_the_right_quadrant():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    quadrants = {e.key.as_str(): e.quadrant for e in report.entries}

    assert quadrants[DINING_CREDIT] == QUADRANT_LOAD_BEARING
    assert quadrants[RESY_CREDIT] == QUADRANT_DEAD_WEIGHT
    assert quadrants[PROTECTION] == QUADRANT_OPTION
    assert quadrants[FLAT] == QUADRANT_NOISE
    assert report.resolved_cost_threshold_minor == median_cost_minor(list(COSTS.values()))


def test_cut_candidates_are_dead_weight_most_expensive_first():
    costs = {**COSTS, FLAT: 50_000_000}  # a rival benefit that is expensive and never decides
    report = attribute(
        corpus(),
        now=T0,
        cost_basis=costs,
        settings=AttributionSettings(cost_threshold_minor=20_000_000),
    )
    assert [e.key.as_str() for e in report.cut_candidates()] == [FLAT, RESY_CREDIT]
    assert report.quadrant(QUADRANT_LOAD_BEARING)
    assert ACTION_BY_QUADRANT[QUADRANT_DEAD_WEIGHT] == "cut or renegotiate"
    assert report.cut_candidates()[0].action == "cut or renegotiate"


def test_a_benefit_seen_too_few_times_is_not_classified():
    report = attribute(corpus(n=3), now=T0, cost_basis=COSTS)
    assert {e.quadrant for e in report.entries} == {QUADRANT_INSUFFICIENT_EVIDENCE}

    loosened = attribute(
        corpus(n=3), now=T0, cost_basis=COSTS, settings=AttributionSettings(min_observations=3)
    )
    rows = {e.key.as_str(): e for e in loosened.entries}
    assert rows[DINING_CREDIT].quadrant == QUADRANT_LOAD_BEARING
    assert rows[RESY_CREDIT].quadrant == QUADRANT_DEAD_WEIGHT
    # The gated offer was never in play at all, so no threshold makes it classifiable.
    assert rows[GATED].quadrant == QUADRANT_INSUFFICIENT_EVIDENCE


def test_a_benefit_with_no_supplied_cost_is_placed_in_no_quadrant():
    report = attribute(corpus(), now=T0, cost_basis={DINING_CREDIT: 30_000_000})
    rows = {e.key.as_str(): e for e in report.entries}

    assert rows[RESY_CREDIT].quadrant == QUADRANT_NO_COST_BASIS
    assert rows[RESY_CREDIT].annual_cost_minor is None
    assert "supply an annualised cost basis" in rows[RESY_CREDIT].action


def test_an_explicit_cost_threshold_overrides_the_median():
    report = attribute(
        corpus(),
        now=T0,
        cost_basis=COSTS,
        settings=AttributionSettings(cost_threshold_minor=1),
    )
    rows = {e.key.as_str(): e for e in report.entries}
    assert report.resolved_cost_threshold_minor == 1
    assert rows[PROTECTION].quadrant == QUADRANT_LOAD_BEARING
    assert rows[FLAT].quadrant == QUADRANT_DEAD_WEIGHT


def test_the_decisive_threshold_moves_the_boundary():
    obs = corpus(n=4) + [
        decide([dining_line(9)], decision_id="d9", manifests=[platinum(), rival_flat()], chosen="rival_flat")
    ]
    lenient = attribute(obs, now=T0, cost_basis=COSTS, settings=AttributionSettings(min_observations=5))
    strict = attribute(
        obs,
        now=T0,
        cost_basis=COSTS,
        settings=AttributionSettings(min_observations=5, decisive_threshold_bp=9_000),
    )
    lenient_rows = {e.key.as_str(): e for e in lenient.entries}
    strict_rows = {e.key.as_str(): e for e in strict.entries}

    assert lenient_rows[DINING_CREDIT].decisive_rate_bp == 8_000
    assert lenient_rows[DINING_CREDIT].quadrant == QUADRANT_LOAD_BEARING
    assert strict_rows[DINING_CREDIT].quadrant == QUADRANT_DEAD_WEIGHT


def test_median_cost_minor_is_integer_and_handles_both_parities():
    assert median_cost_minor([]) is None
    assert median_cost_minor([5]) == 5
    assert median_cost_minor([3, 1, 2]) == 2
    assert median_cost_minor([1, 2, 3, 4]) == 2


# ----------------------------------------------------------------------------------
# Reading a corpus.
# ----------------------------------------------------------------------------------


def test_a_receipt_envelope_is_read_and_its_selection_respected():
    ev = evaluate(
        cart=Cart.of("m_resy", [dining_line(0)]),
        manifests=[platinum(), rival_flat()],
        now=T0,
        policy=UNSIGNED,
    )
    receipt = {
        "receipt_id": "rcpt_001",
        "selection": {"manifest_id": "rival_flat"},
        "evaluation": ev.to_dict(),
    }
    obs = observe_receipt(receipt)

    assert obs.decision_id == "rcpt_001"
    assert obs.chosen_manifest_id == "rival_flat"
    assert obs.margin_minor == 24_000 - 525_000
    assert not obs.pivotal_testable


def test_a_receipt_survives_a_json_round_trip():
    ev = evaluate(
        cart=Cart.of("m_resy", [dining_line(0)]),
        manifests=[platinum(), rival_flat()],
        now=T0,
        policy=UNSIGNED,
    )
    reloaded = observe_receipt(json.loads(json.dumps(ev.to_dict())))
    assert reloaded.chosen_manifest_id == "amex_platinum"
    assert reloaded.decision_id == f"{ev.cart_hash[:16]}:{T0}"
    assert len(reloaded.benefits) == 6


def test_an_observation_serializes_without_naming_a_member_or_an_agent():
    obs = corpus(n=1)[0]
    body = obs.to_dict()

    assert body["decision_id"] == "d0"
    assert body["contested"] is True
    assert body["chosen_manifest_id"] == "amex_platinum"
    assert {b["benefit_id"] for b in body["benefits"]} >= {"b_dining_credit", "b_flat"}
    assert "holder" not in json.dumps(body)


def test_a_record_this_module_cannot_read_says_what_it_expected():
    with pytest.raises(AttributionError, match="candidates"):
        observe_receipt({"receipt_id": "rcpt", "totally": "unrelated"})
    with pytest.raises(AttributionError, match="mapping"):
        observe_receipt(["not", "a", "mapping"])
    with pytest.raises(AttributionError, match="DecisionObservation"):
        attribute([{"decision_id": "d"}], now=T0)


def test_the_same_receipt_twice_is_counted_once():
    obs = corpus(n=6)
    report = attribute(obs + obs, now=T0, cost_basis=COSTS)

    assert report.decisions == 6
    assert report.duplicates_ignored == 6
    assert {e.key.as_str(): e for e in report.entries}[DINING_CREDIT].winning == 6


def test_a_real_signed_decision_receipt_flows_into_the_corpus():
    """End to end: evaluation -> Decision Receipt -> JSON -> corpus -> 2x2.

    The corpus reader takes the receipt envelope receipt.py actually emits, not a shape
    invented here, so a change to either side breaks this rather than the demo.
    """
    from plumbline import receipt as receipt_module

    key, key_id = "secret", "issuer-2026"
    observations = []
    for i in range(6):
        c = Cart.of("m_resy", [dining_line(i)])
        ev = evaluate(
            cart=c,
            manifests=[sign_manifest(platinum(), key, key_id=key_id), rival_flat()],
            now=T0,
            keys={key_id: key},
            policy=ValuationPolicy(require_signed_manifest=False),
        )
        rcpt = receipt_module.build_receipt_from_evaluation(
            receipt_id=f"rcpt_{i}",
            issued_at=T0 + i,
            evaluation=ev,
            session=receipt_module.CheckoutSession.of(f"cs_{i}", c, T0),
            mandate=receipt_module.MandateBinding(
                mandate_id="mnd_1",
                authorized_instrument_ids=("amex_platinum", "rival_flat"),
            ),
            agent=receipt_module.Identity(kind="agent", identifier="op_shopbot"),
            platform=receipt_module.Identity(kind="platform", identifier="acme_checkout"),
            chosen_instrument_id="amex_platinum",
        )
        observations.append(observe_receipt(json.loads(json.dumps(rcpt.to_dict()))))

    assert [o.decision_id for o in observations] == [f"rcpt_{i}" for i in range(6)]
    report = attribute(observations, now=T0, cost_basis=COSTS)
    rows = {e.key.as_str(): e for e in report.entries}

    assert rows[DINING_CREDIT].winning == 6
    assert rows[DINING_CREDIT].quadrant == QUADRANT_LOAD_BEARING
    assert rows[RESY_CREDIT].quadrant == QUADRANT_DEAD_WEIGHT


def test_signed_manifests_flow_through_the_corpus_unchanged():
    key, key_id = "secret", "issuer-2026"
    ev = evaluate(
        cart=Cart.of("m_resy", [dining_line(0)]),
        manifests=[sign_manifest(platinum(), key, key_id=key_id)],
        now=T0,
        keys={key_id: key},
    )
    obs = observe_evaluation(ev, decision_id="signed_1")
    assert obs.chosen_manifest_id == "amex_platinum"
    assert any(b.winning for b in obs.benefits)


# ----------------------------------------------------------------------------------
# What the report is not allowed to say or carry.
# ----------------------------------------------------------------------------------


def test_the_report_carries_no_member_identifier_anywhere():
    """The inversion trap, enforced structurally: this is portfolio analytics, not a nudge."""
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    forbidden = ("member", "cardholder", "holder", "customer", "email", "account", "user", "pan")
    offenders = [k for k in _all_keys(report.to_dict()) if any(f in k.lower() for f in forbidden)]
    assert offenders == []


def _all_keys(obj, out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _all_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, out)
    return out


def test_every_entry_states_what_it_observes_and_what_it_does_not():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    body = report.to_dict()

    assert body["observes"] == CLAIM_BOUNDARY
    assert body["pivotal_definition"] == PIVOTAL_DEFINITION
    assert body["audience"] == NEVER_A_NUDGE
    assert all(e["observes"] == CLAIM_BOUNDARY for e in body["entries"])
    assert "retention" in CLAIM_BOUNDARY
    assert "not by re-running the evaluator" in PIVOTAL_DEFINITION


def test_the_criteria_the_corpus_actually_ranked_on_are_recorded():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    assert report.criteria == ("max_incremental_value",)


def test_activation_gaps_are_reported_to_the_issuer_and_only_the_issuer():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    gaps = report.activation_gaps()

    assert [e.key.as_str() for e in gaps] == [GATED]
    assert gaps[0].not_enrolled_while_eligible == 8
    assert gaps[0].in_play == 0
    assert "converts breakage into" in report.activation_gaps.__doc__


def test_settings_reject_thresholds_that_cannot_mean_anything():
    with pytest.raises(AttributionError, match="min_observations"):
        AttributionSettings(min_observations=0)
    with pytest.raises(AttributionError, match="decisive_threshold_bp"):
        AttributionSettings(decisive_threshold_bp=10_001)
    with pytest.raises(AttributionError, match="cost_threshold_minor"):
        AttributionSettings(cost_threshold_minor=-1)


def test_quadrant_lookup_rejects_an_unknown_name():
    report = attribute(corpus(), now=T0, cost_basis=COSTS)
    with pytest.raises(AttributionError, match="unknown quadrant"):
        report.quadrant("profitable")


def test_the_report_replays_byte_for_byte():
    first = attribute(corpus(), now=T0, cost_basis=COSTS)
    second = attribute(corpus(), now=T0, cost_basis=COSTS)
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )


def test_render_text_puts_the_cut_list_and_the_caveats_on_one_page():
    text = attribute(corpus(), now=T0, cost_basis=COSTS).render_text()

    assert "CUT CANDIDATES" in text
    assert "Resy dining credit" in text
    assert QUADRANT_LOAD_BEARING in text
    assert "SELECTION INFLUENCE" in text
    assert NEVER_A_NUDGE in text


def test_an_empty_corpus_reports_nothing_rather_than_guessing():
    report = attribute([], now=T0, cost_basis=COSTS)
    assert report.entries == ()
    assert report.decisions == 0
    assert report.criteria == ()
    assert report.cut_candidates() == ()
    assert "none at these thresholds" in report.render_text()
