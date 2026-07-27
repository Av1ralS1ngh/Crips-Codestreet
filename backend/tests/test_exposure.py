"""Behavioural risk scoring and the Agent Purchase Protection exposure book.

Every number under test comes out of the real decision path: the fixtures drive a
CaveatEngine, and the registry reads only the telemetry that engine recorded. Nothing here
asserts against a hand-written event table.
"""

from __future__ import annotations

import pytest

from caveat.cart import Cart, CartLine
from caveat.constraints import (
    REASON_MCC_NOT_ALLOWED,
    REASON_MERCHANT_NOT_ALLOWED,
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    CumulativeMax,
    MccAllow,
    MerchantAllow,
    StepUpOver,
)
from caveat.engine import CaveatEngine
from caveat.exposure import (
    BP,
    DISCLAIMER,
    ExposureAssumptions,
    ExposureModel,
    agent_error_attempts,
    authorized_volume_by_operator,
    is_agent_error_attempt,
    smoothed_rate_bp,
)
from caveat.pdp import (
    REASON_CART_DIVERGENCE,
    VERDICT_AGENT_ERROR,
    VERDICT_INJECTION_COMPROMISE,
)
from caveat.registry import (
    BAND_SUSPENDED,
    BAND_TRUSTED,
    SIGNAL_INJECTION_ABSORBED,
    SIGNAL_SCOPE_ESCALATION,
    AgentRegistry,
    RiskWeights,
    band_for,
    exposure_cap_minor,
)

T0 = 1_753_600_000

SCOPE = ConstraintSet(
    [
        AmountMax(1_000_000),
        CumulativeMax(5_000_000_000),
        CategoryAllow(("groceries", "appliances")),
        MerchantAllow(("m_bigbasket", "m_croma")),
        MccAllow((5411, 5722)),
        StepUpOver(800_000),
    ]
)

ESPRESSO = CartLine(
    sku="sku_espresso_01",
    description="Budget espresso machine",
    amount=400_000,
    mcc=5722,
    category="appliances",
)

GIFT_CARDS = [
    CartLine(
        sku=f"sku_giftcard_{i}",
        description="₹5,000 stored-value gift card",
        amount=500_000,
        mcc=6540,
        category="stored_value",
    )
    for i in range(3)
]

CLEAN_CART = Cart.of("m_croma", [ESPRESSO])
# Same cart, a merchant the mandate never allowed: registered-agent error, not injection.
OFF_MANDATE_CART = Cart.of("m_darkbazaar", [ESPRESSO])
INJECTED_CART = Cart.of("m_croma", [ESPRESSO, *GIFT_CARDS])


# ----------------------------------------------------------------------------------
# Traffic helpers — every one of these goes through the real PDP.
# ----------------------------------------------------------------------------------


def drive_clean(engine: CaveatEngine, mandate, n: int, *, start: int) -> None:
    for i in range(n):
        d = engine.authorize(
            mandate=mandate, intent_cart=CLEAN_CART, executed_cart=CLEAN_CART, now=start + i
        )
        assert d.outcome == "ALLOW", d.to_dict()


def drive_agent_error(engine: CaveatEngine, mandate, n: int, *, start: int) -> None:
    for i in range(n):
        d = engine.authorize(
            mandate=mandate,
            intent_cart=OFF_MANDATE_CART,
            executed_cart=OFF_MANDATE_CART,
            now=start + i,
        )
        assert d.outcome == "DENY"
        assert REASON_MERCHANT_NOT_ALLOWED in d.reason_codes
        assert d.verdict == VERDICT_AGENT_ERROR, d.to_dict()


def drive_injection(engine: CaveatEngine, mandate, n: int, *, start: int) -> None:
    for i in range(n):
        d = engine.authorize(
            mandate=mandate,
            intent_cart=CLEAN_CART,
            executed_cart=INJECTED_CART,
            now=start + i,
        )
        assert d.outcome == "DENY"
        assert d.verdict == VERDICT_INJECTION_COMPROMISE


def drive_escalation(engine: CaveatEngine, parent, holder: str, n: int, *, start: int) -> None:
    for i in range(n):
        outcome = engine.delegate(
            parent=parent,
            child_holder=holder,
            declared_scope=ConstraintSet([AmountMax(9_000_000)]),
            now=start + i,
        )
        assert not outcome.accepted


@pytest.fixture()
def book() -> tuple[CaveatEngine, AgentRegistry]:
    """Three operators with genuinely different behaviour, on one engine.

    op_clean     60 clean authorizations                            → high volume, no incidents
    op_watchful  30 clean authorizations + 3 absorbed injections    → incidents, no intent to exceed
    op_rogue     10 clean + 8 out-of-mandate attempts + 4 escalations
    """
    e = CaveatEngine()
    e.register_operator("op_clean", "SteadyShopper", now=T0)
    e.register_operator("op_watchful", "WatchfulBot", now=T0)
    e.register_operator("op_rogue", "RogueBot", now=T0)

    clean = e.grant(holder="op_clean", scope=SCOPE, now=T0)
    drive_clean(e, clean, 60, start=T0 + 100)

    watchful = e.grant(holder="op_watchful", scope=SCOPE, now=T0)
    drive_clean(e, watchful, 30, start=T0 + 1_000)
    drive_injection(e, watchful, 3, start=T0 + 1_100)

    rogue = e.grant(holder="op_rogue", scope=SCOPE, now=T0)
    drive_clean(e, rogue, 10, start=T0 + 2_000)
    drive_agent_error(e, rogue, 8, start=T0 + 2_100)
    drive_escalation(e, rogue, "op_rogue", 4, start=T0 + 2_200)

    return e, AgentRegistry(e.store)


# ----------------------------------------------------------------------------------
# The score
# ----------------------------------------------------------------------------------


def test_risk_score_rises_after_escalation_attempts():
    e = CaveatEngine()
    e.register_operator("op_x", "AgentX", now=T0)
    registry = AgentRegistry(e.store)
    mandate = e.grant(holder="op_x", scope=SCOPE, now=T0)
    drive_clean(e, mandate, 20, start=T0 + 10)

    before = registry.assess("op_x", now=T0 + 100)
    drive_escalation(e, mandate, "op_x", 3, start=T0 + 200)
    after = registry.assess("op_x", now=T0 + 300)

    assert after.score > before.score
    assert after.counts[SIGNAL_SCOPE_ESCALATION] == 3
    escalation = next(s for s in after.signals if s.signal == SIGNAL_SCOPE_ESCALATION)
    assert escalation.points > 0
    assert escalation.describe() in [d.describe() for d in after.drivers()]
    assert after.suggested_exposure_cap_minor < before.suggested_exposure_cap_minor


def test_risk_score_rises_after_injection_events():
    e = CaveatEngine()
    e.register_operator("op_y", "AgentY", now=T0)
    registry = AgentRegistry(e.store)
    mandate = e.grant(holder="op_y", scope=SCOPE, now=T0)
    drive_clean(e, mandate, 20, start=T0 + 10)

    before = registry.assess("op_y", now=T0 + 100)
    drive_injection(e, mandate, 2, start=T0 + 200)
    mid = registry.assess("op_y", now=T0 + 300)
    drive_injection(e, mandate, 2, start=T0 + 400)
    after = registry.assess("op_y", now=T0 + 500)

    assert before.score < mid.score < after.score
    assert after.counts[SIGNAL_INJECTION_ABSORBED] == 4
    injection = next(s for s in after.signals if s.signal == SIGNAL_INJECTION_ABSORBED)
    assert injection.count == 4
    assert injection.observed_rate > 0


def test_clean_volume_lowers_the_score_and_earns_the_trusted_band():
    e = CaveatEngine()
    e.register_operator("op_z", "AgentZ", now=T0)
    registry = AgentRegistry(e.store)
    mandate = e.grant(holder="op_z", scope=SCOPE, now=T0)

    drive_clean(e, mandate, 5, start=T0 + 10)
    early = registry.assess("op_z", now=T0 + 100)
    drive_clean(e, mandate, 95, start=T0 + 200)
    later = registry.assess("op_z", now=T0 + 400)

    assert later.score < early.score
    assert later.band == BAND_TRUSTED
    assert later.confidence > early.confidence
    # An unproven operator is not a clean one: assumption must dominate early on.
    assert early.unproven_premium_points > early.behavioural_points


def test_unregistered_operator_carries_a_premium():
    """The shadow-agent case: holds a credential, was never onboarded."""
    e = CaveatEngine()
    e.register_operator("op_parent", "ParentBot", now=T0)
    registry = AgentRegistry(e.store)
    root = e.grant(holder="op_parent", scope=SCOPE, now=T0)
    outcome = e.attenuate(
        parent=root, child_holder="op_never_registered", added=[AmountMax(500_000)], now=T0 + 1
    )
    assert outcome.mandate is not None
    drive_clean(e, outcome.mandate, 20, start=T0 + 10)

    shadow = registry.assess("op_never_registered", now=T0 + 100)
    parent = registry.assess("op_parent", now=T0 + 100)

    assert shadow.registered is False
    assert shadow.unregistered_premium_points > 0
    assert parent.unregistered_premium_points == 0
    assert "op_never_registered" in registry.known_operator_ids()


def test_bands_separate_the_three_operators(book):
    _e, registry = book
    scores = {a.operator_id: a for a in registry.portfolio(now=T0 + 5_000)}

    assert scores["op_clean"].score < scores["op_watchful"].score < scores["op_rogue"].score
    assert scores["op_clean"].band == BAND_TRUSTED
    assert scores["op_rogue"].band == BAND_SUSPENDED
    # portfolio() is ordered riskiest first — the console reads top-down.
    ordered = [a.operator_id for a in registry.portfolio(now=T0 + 5_000)]
    assert ordered[0] == "op_rogue"


def test_score_is_fully_explained_by_its_evidence_trail(book):
    _e, registry = book
    assessment = registry.assess("op_rogue", now=T0 + 5_000)

    recomputed = sum(s.points for s in assessment.signals)
    assert recomputed == pytest.approx(assessment.behavioural_points)
    total = (
        assessment.behavioural_points
        + assessment.unproven_premium_points
        + assessment.unregistered_premium_points
    )
    assert assessment.score == pytest.approx(min(100.0, total))

    payload = assessment.to_dict()
    assert payload["weights"]["signals"], "the weighting must ship with the score"
    assert payload["drivers"]
    assert "scope_escalation_attempt" in payload["drivers"][0]
    assert "how the score was computed" in assessment.render_text()


def test_weights_are_tunable_and_change_the_number(book):
    _e, registry = book
    baseline = registry.assess("op_watchful", now=T0 + 5_000)

    softened = AgentRegistry(
        registry.store,
        RiskWeights().with_signal(SIGNAL_INJECTION_ABSORBED, weight=1.0),
    ).assess("op_watchful", now=T0 + 5_000)

    assert softened.score < baseline.score
    injection = next(s for s in softened.signals if s.signal == SIGNAL_INJECTION_ABSORBED)
    assert injection.weight == 1.0


def test_exposure_cap_falls_monotonically_as_score_rises():
    weights = RiskWeights()
    caps = [
        exposure_cap_minor(float(score), band_for(float(score), weights), weights)
        for score in range(0, 101)
    ]

    assert caps[0] == weights.base_exposure_cap_minor
    assert caps[-1] == 0
    for lower, higher in zip(caps, caps[1:]):
        assert higher <= lower
    assert all(isinstance(c, int) for c in caps), "caps are quoted as integer minor units"


def test_record_signal_rejects_names_outside_the_vocabulary():
    e = CaveatEngine()
    registry = AgentRegistry(e.store)
    registry.register("op_a", "A", now=T0)
    with pytest.raises(ValueError):
        registry.record_signal("op_a", "vibes_were_off", now=T0)


# ----------------------------------------------------------------------------------
# Agent-error classification: the input the whole exposure model rests on.
# ----------------------------------------------------------------------------------


def test_agent_error_classification_mirrors_the_pdp_precedence():
    assert is_agent_error_attempt([REASON_MERCHANT_NOT_ALLOWED])
    assert is_agent_error_attempt([REASON_MCC_NOT_ALLOWED, "AMOUNT_EXCEEDED"])
    assert not is_agent_error_attempt([])
    assert not is_agent_error_attempt(["AMOUNT_EXCEEDED"])
    # An injected cart also trips MCC_NOT_ALLOWED, but it is fraud, not agent error, and
    # Agent Purchase Protection must not be priced for it.
    assert not is_agent_error_attempt([REASON_CART_DIVERGENCE, REASON_MCC_NOT_ALLOWED])


def test_agent_error_attempts_are_counted_from_real_telemetry(book):
    e, _registry = book
    assert agent_error_attempts(e.store, "op_rogue") == 8
    assert agent_error_attempts(e.store, "op_watchful") == 0, "injections are fraud, not agent error"
    assert agent_error_attempts(e.store, "op_clean") == 0


def test_smoothed_rate_shrinks_small_samples_toward_the_prior():
    # One attempt out of one decision is not a 100% error rate.
    aggressive = smoothed_rate_bp(1, 1, prior_bp=150, prior_weight=5)
    assert aggressive < BP
    # The prior fades as evidence accumulates.
    assert smoothed_rate_bp(50, 100, prior_bp=150, prior_weight=5) > smoothed_rate_bp(
        5, 100, prior_bp=150, prior_weight=5
    )
    # No observations at all: price at the prior, not at zero.
    assert smoothed_rate_bp(0, 0, prior_bp=150, prior_weight=5) == 150


# ----------------------------------------------------------------------------------
# The book
# ----------------------------------------------------------------------------------


def test_volume_is_attributed_to_the_holder_that_authorized(book):
    e, _registry = book
    volumes = authorized_volume_by_operator(e.store)
    assert volumes["op_clean"] == 60 * 400_000
    assert volumes["op_watchful"] == 30 * 400_000
    assert volumes["op_rogue"] == 10 * 400_000
    assert all(isinstance(v, int) for v in volumes.values())


def test_book_prices_every_known_operator_in_integer_minor_units(book):
    _e, registry = book
    result = ExposureModel(registry).book(now=T0 + 5_000)

    assert {e.operator_id for e in result.entries} == {"op_clean", "op_watchful", "op_rogue"}
    for entry in result.entries:
        for value in (
            entry.authorized_volume_minor,
            entry.covered_volume_minor,
            entry.expected_loss_minor,
            entry.ungoverned_expected_loss_minor,
            entry.stressed_loss_minor,
            entry.prevented_loss_minor,
            entry.agent_error_rate_bp,
        ):
            assert isinstance(value, int), "no floats anywhere in the money path"
        assert entry.expected_loss_minor <= entry.ungoverned_expected_loss_minor
        assert entry.prevented_loss_minor >= 0


def test_the_riskiest_operator_dominates_exposure_despite_the_smallest_volume(book):
    """The point of the whole book: volume share and risk share are not the same number."""
    _e, registry = book
    result = ExposureModel(registry).book(now=T0 + 5_000)
    by_id = {e.operator_id: e for e in result.entries}

    assert by_id["op_rogue"].authorized_volume_minor < by_id["op_clean"].authorized_volume_minor
    assert by_id["op_rogue"].expected_loss_minor > by_id["op_clean"].expected_loss_minor
    assert by_id["op_rogue"].agent_error_rate_bp > by_id["op_clean"].agent_error_rate_bp


def test_cutoff_curve_moves_monotonically(book):
    _e, registry = book
    result = ExposureModel(registry).book(now=T0 + 5_000)
    assert len(result.curve) > 1

    for lower, higher in zip(result.curve, result.curve[1:]):
        assert higher.cutoff > lower.cutoff
        # A looser cutoff can only ever admit more.
        assert higher.admitted_operators >= lower.admitted_operators
        assert higher.admitted_volume_minor >= lower.admitted_volume_minor
        assert higher.expected_loss_minor >= lower.expected_loss_minor
        assert higher.stressed_loss_minor >= lower.stressed_loss_minor

    strictest, loosest = result.curve[0], result.curve[-1]
    assert strictest.admitted_operators == 0
    assert loosest.admitted_operators == len(result.entries)
    assert loosest.expected_loss_minor == result.total_expected_loss_minor
    assert loosest.admitted_volume_share_bp == BP


def test_a_cutoff_that_excludes_the_rogue_keeps_most_volume_and_caps_exposure(book):
    """The sentence an exec reads off the slide."""
    _e, registry = book
    result = ExposureModel(registry).book(now=T0 + 5_000, cutoffs=range(0, 101, 5))
    by_id = {e.operator_id: e for e in result.entries}
    rogue_score = by_id["op_rogue"].risk_score

    admitting_all = result.curve[-1]
    excluding_rogue = max(
        (p for p in result.curve if p.cutoff < rogue_score), key=lambda p: p.cutoff
    )

    assert excluding_rogue.admitted_operators == len(result.entries) - 1
    assert excluding_rogue.expected_loss_minor < admitting_all.expected_loss_minor
    # Most of the book's volume survives the exclusion; most of its exposure does not.
    assert excluding_rogue.admitted_volume_share_bp >= 8_000
    assert excluding_rogue.expected_loss_minor * 2 < admitting_all.expected_loss_minor
    assert "modelled exposure" in excluding_rogue.headline()


def test_recommended_cutoff_is_always_plotted(book):
    _e, registry = book
    assumptions = ExposureAssumptions(admission_cutoff_default=55)
    # A grid that does not contain 55; the model must add it anyway.
    result = ExposureModel(registry, assumptions).book(now=T0 + 5_000, cutoffs=(0, 50, 100))

    assert result.recommended is not None
    assert result.recommended.cutoff == 55
    assert [p.cutoff for p in result.curve] == [0, 50, 55, 100]


def test_suspended_operators_get_a_zero_exposure_cap(book):
    _e, registry = book
    result = ExposureModel(registry).book(now=T0 + 5_000)
    rogue = next(e for e in result.entries if e.operator_id == "op_rogue")
    assert rogue.band == BAND_SUSPENDED
    assert rogue.suggested_exposure_cap_minor == 0


def test_assumptions_are_tunable_and_move_the_book(book):
    _e, registry = book
    base = ExposureModel(registry).book(now=T0 + 5_000)
    leaky = ExposureModel(
        registry, ExposureAssumptions(residual_leakage_bp=10_000)
    ).book(now=T0 + 5_000)

    # With no prevention credit at all, governed loss collapses onto ungoverned loss.
    assert leaky.total_expected_loss_minor > base.total_expected_loss_minor
    assert leaky.total_expected_loss_minor == leaky.total_ungoverned_loss_minor
    assert leaky.total_prevented_loss_minor == 0


def test_every_assumption_is_named_in_the_output(book):
    _e, registry = book
    payload = ExposureModel(registry).book(now=T0 + 5_000).to_dict()

    assumptions = payload["assumptions"]
    for field in (
        "coverage_share_bp",
        "severity_bp",
        "residual_leakage_bp",
        "baseline_agent_error_rate_bp",
        "prior_weight_decisions",
        "stress_multiplier_bp",
    ):
        assert field in assumptions

    # The honesty requirement, enforced by a test rather than by good intentions.
    assert assumptions["disclaimer"] == DISCLAIMER
    assert "not a measurement" in payload["disclaimer"]
    assert any("residual leakage" in note for note in payload["assumption_notes"])


def test_volume_overrides_let_an_analyst_ask_what_if(book):
    _e, registry = book
    model = ExposureModel(registry)
    base = model.book(now=T0 + 5_000)
    scaled = model.book(now=T0 + 5_000, volume_overrides={"op_rogue": 1_000_000_000})

    base_rogue = next(e for e in base.entries if e.operator_id == "op_rogue")
    scaled_rogue = next(e for e in scaled.entries if e.operator_id == "op_rogue")
    assert scaled_rogue.authorized_volume_minor == 1_000_000_000
    assert scaled_rogue.expected_loss_minor > base_rogue.expected_loss_minor
    # The real book is unchanged: an override is a question, not a write.
    assert next(e for e in model.book(now=T0 + 5_000).entries if e.operator_id == "op_rogue") == base_rogue


def test_book_renders_for_the_deck(book):
    _e, registry = book
    text = ExposureModel(registry).book(now=T0 + 5_000).render_text()
    assert "MODELLED EXPOSURE BOOK" in text
    assert "ADMISSION CUTOFF CURVE" in text
    assert "ASSUMPTIONS" in text
    assert "op_rogue" in text
    assert "not a measurement" in text


def test_empty_book_does_not_divide_by_zero():
    e = CaveatEngine()
    registry = AgentRegistry(e.store)
    result = ExposureModel(registry).book(now=T0)
    assert result.entries == ()
    assert result.total_expected_loss_minor == 0
    for point in result.curve:
        assert point.admitted_volume_share_bp == 0
        assert point.loss_rate_bp == 0
