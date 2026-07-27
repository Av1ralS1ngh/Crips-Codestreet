"""The four demo beats, as tests.

If any of these fails, the demo fails. CI gates on this file.
"""

from __future__ import annotations

import pytest

from caveat.cart import Cart, CartLine
from caveat.constraints import (
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    CumulativeMax,
    MccAllow,
    MerchantAllow,
    StepUpOver,
)
from caveat.engine import CaveatEngine
from caveat.pdp import (
    REASON_CART_DIVERGENCE,
    REASON_MANDATE_REVOKED,
    VERDICT_INJECTION_COMPROMISE,
)

T0 = 1_753_600_000

GROCERY_SCOPE = ConstraintSet(
    [
        AmountMax(1_000_000),  # ₹10,000 per transaction
        CumulativeMax(5_000_000),
        CategoryAllow(("groceries", "appliances")),
        MerchantAllow(("m_bigbasket", "m_croma")),
        MccAllow((5411, 5722)),
        StepUpOver(800_000),  # ₹8,000
    ]
)

ESPRESSO = CartLine(
    sku="sku_espresso_01",
    description="Budget espresso machine",
    amount=400_000,  # ₹4,000
    mcc=5722,
    category="appliances",
)

GIFT_CARDS = [
    CartLine(
        sku=f"sku_giftcard_{i}",
        description="₹5,000 stored-value gift card",
        amount=500_000,
        mcc=6540,  # stored value — not in the allowlist
        category="stored_value",
    )
    for i in range(10)
]


@pytest.fixture()
def engine() -> CaveatEngine:
    e = CaveatEngine()
    e.register_operator("op_shopbot", "ShopBot v2.1", now=T0)
    e.register_operator("op_pricechecker", "PriceCheckerBot", now=T0)
    return e


# ----------------------------------------------------------------------------------
# Beat 1 — theft, then no theft.
# ----------------------------------------------------------------------------------


def test_beat1_clean_purchase_is_allowed(engine: CaveatEngine):
    """The honest path must work, or the contrast in beat 1 means nothing."""
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    cart = Cart.of("m_croma", [ESPRESSO])

    decision = engine.authorize(
        mandate=mandate, intent_cart=cart, executed_cart=cart, now=T0 + 10
    )
    assert decision.outcome == "ALLOW", decision.to_dict()
    assert decision.amount == 400_000
    assert not decision.diff.diverged()


def test_beat1_injected_cart_is_denied(engine: CaveatEngine):
    """Same agent, same task, injected merchant page. This is the money shot."""
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)

    intent = Cart.of("m_croma", [ESPRESSO])
    executed = Cart.of("m_croma", [ESPRESSO, *GIFT_CARDS])

    decision = engine.authorize(
        mandate=mandate, intent_cart=intent, executed_cart=executed, now=T0 + 10
    )

    assert decision.outcome == "DENY"
    # The two independent reasons that catch it: the cart moved after intent was signed,
    # and stored value was never in the mandate.
    assert REASON_CART_DIVERGENCE in decision.reason_codes
    assert "MCC_NOT_ALLOWED" in decision.reason_codes
    assert decision.verdict == VERDICT_INJECTION_COMPROMISE
    assert "not liable" in decision.liable_party

    assert len(decision.diff.added) == 10
    assert decision.diff.added_value() == 5_000_000
    assert decision.diff.delta == 5_000_000


def test_beat1_ungoverned_baseline_would_have_paid(engine: CaveatEngine):
    """The left-hand pane: without a mandate layer the full amount authorizes."""
    executed = Cart.of("m_croma", [ESPRESSO, *GIFT_CARDS])
    assert executed.total() == 5_400_000  # ₹54,000 out the door


# ----------------------------------------------------------------------------------
# Beat 2 — the escalation proof.
# ----------------------------------------------------------------------------------


def test_beat2_legitimate_attenuation_accepted(engine: CaveatEngine):
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    outcome = engine.attenuate(
        parent=mandate,
        child_holder="op_pricechecker",
        added=[AmountMax(200_000), MerchantAllow(("m_bigbasket",))],
        now=T0 + 1,
    )
    assert outcome.accepted
    assert outcome.entailment.entailed
    assert outcome.mandate is not None
    assert outcome.mandate.depth == 1
    assert outcome.entailment.elapsed_ms < 100


def test_beat2_widened_bound_rejected_with_counterexample(engine: CaveatEngine):
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    outcome = engine.delegate(
        parent=mandate,
        child_holder="op_pricechecker",
        declared_scope=ConstraintSet(
            [
                AmountMax(5_000_000),
                CategoryAllow(("groceries", "appliances")),
                MerchantAllow(("m_bigbasket",)),
                MccAllow((5411,)),
            ]
        ),
        now=T0 + 1,
    )
    assert not outcome.accepted
    ce = outcome.entailment.counterexample
    assert ce is not None and ce.amount > 1_000_000
    assert outcome.mandate is None


def test_beat2_dropped_constraint_rejected_where_naive_check_is_fooled(engine: CaveatEngine):
    """The line that lands: fewer caveats, more authority."""
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    outcome = engine.delegate(
        parent=mandate,
        child_holder="op_pricechecker",
        declared_scope=ConstraintSet([AmountMax(1_000_000)]),
        now=T0 + 1,
    )
    assert not outcome.accepted
    assert outcome.naive_verdict is True, "the naive subset check should be fooled"
    assert outcome.to_dict()["naive_disagrees"] is True

    ce = outcome.entailment.counterexample
    assert ce is not None
    # The counterexample must exercise one of the constraints the child dropped.
    assert ce.violated


def test_beat2_rejection_is_recorded_in_the_ledger(engine: CaveatEngine):
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    engine.delegate(
        parent=mandate,
        child_holder="op_rogue",
        declared_scope=ConstraintSet([AmountMax(9_000_000)]),
        now=T0 + 1,
    )
    rejected = engine.ledger.filter("delegation_rejected")
    assert len(rejected) == 1
    assert rejected[0].payload["reason"] == "SCOPE_ESCALATION"


# ----------------------------------------------------------------------------------
# Beat 3 — the kill switch.
# ----------------------------------------------------------------------------------


def test_beat3_revocation_kills_deep_descendant_with_one_row(engine: CaveatEngine):
    root = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)

    current = root
    for i in range(1, 5):
        engine.register_operator(f"op_hop{i}", f"SubAgent {i}", now=T0)
        outcome = engine.attenuate(
            parent=current,
            child_holder=f"op_hop{i}",
            added=[AmountMax(1_000_000 - i * 100_000)],
            now=T0 + i,
        )
        assert outcome.accepted
        assert outcome.mandate is not None
        current = outcome.mandate

    assert current.depth == 4
    cart = Cart.of("m_croma", [ESPRESSO])

    before = engine.authorize(
        mandate=current, intent_cart=cart, executed_cart=cart, now=T0 + 10
    )
    assert before.outcome == "ALLOW"

    record = engine.revoke(root.root_id, now=T0 + 11, cause="cardholder tapped revoke")
    assert record.rows_written == 1
    assert engine.store.revocation_count() == 1

    after = engine.authorize(
        mandate=current, intent_cart=cart, executed_cart=cart, now=T0 + 12
    )
    assert after.outcome == "DENY"
    assert REASON_MANDATE_REVOKED in after.reason_codes


def test_beat3_revocation_reaches_an_unregistered_subagent(engine: CaveatEngine):
    """The shadow-agent case: nobody enumerated this child, and it still dies."""
    root = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    outcome = engine.attenuate(
        parent=root, child_holder="op_never_registered", added=[AmountMax(500_000)], now=T0 + 1
    )
    assert outcome.accepted and outcome.mandate is not None
    shadow = outcome.mandate

    engine.revoke(root.root_id, now=T0 + 5, cause="kill switch")
    cart = Cart.of("m_croma", [ESPRESSO])
    decision = engine.authorize(
        mandate=shadow, intent_cart=cart, executed_cart=cart, now=T0 + 6
    )
    assert decision.outcome == "DENY"
    assert REASON_MANDATE_REVOKED in decision.reason_codes


def test_beat3_containment_is_constant_work(engine: CaveatEngine):
    """Twenty descendants, still one row. Containment does not scale with the fan-out."""
    root = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    leaves = []
    for i in range(20):
        outcome = engine.attenuate(
            parent=root, child_holder=f"op_fan{i}", added=[AmountMax(300_000)], now=T0 + i
        )
        assert outcome.mandate is not None
        leaves.append(outcome.mandate)

    engine.revoke(root.root_id, now=T0 + 100, cause="kill switch")
    assert engine.store.revocation_count() == 1

    cart = Cart.of("m_croma", [ESPRESSO])
    for leaf in leaves:
        d = engine.authorize(mandate=leaf, intent_cart=cart, executed_cart=cart, now=T0 + 101)
        assert d.outcome == "DENY"


# ----------------------------------------------------------------------------------
# Beat 4 — step-up and the evidence handoff.
# ----------------------------------------------------------------------------------


def test_beat4_high_value_triggers_step_up_then_allows(engine: CaveatEngine):
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    big = CartLine(
        sku="sku_fridge",
        description="Refrigerator",
        amount=950_000,  # over the ₹8,000 step-up threshold, under the ₹10,000 cap
        mcc=5722,
        category="appliances",
    )
    cart = Cart.of("m_croma", [big])

    first = engine.authorize(mandate=mandate, intent_cart=cart, executed_cart=cart, now=T0 + 10)
    assert first.outcome == "STEP_UP"
    assert first.step_up_challenge_id is not None

    engine.satisfy_step_up(first.step_up_challenge_id, now=T0 + 20)

    second = engine.authorize(mandate=mandate, intent_cart=cart, executed_cart=cart, now=T0 + 21)
    assert second.outcome == "ALLOW", second.to_dict()


def test_beat4_ledger_is_tamper_evident_and_provable(engine: CaveatEngine):
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    intent = Cart.of("m_croma", [ESPRESSO])
    executed = Cart.of("m_croma", [ESPRESSO, *GIFT_CARDS])
    decision = engine.authorize(
        mandate=mandate, intent_cart=intent, executed_cart=executed, now=T0 + 10
    )

    ok, err = engine.verify_ledger()
    assert ok, err

    proof = engine.ledger.inclusion_proof(decision.ledger_seq)
    assert proof is not None
    assert proof.verify(), "inclusion proof must recompute the published root"
    assert proof.root == engine.ledger_root()


def test_beat4_tampering_with_the_ledger_is_detected(engine: CaveatEngine):
    mandate = engine.grant(holder="op_shopbot", scope=GROCERY_SCOPE, now=T0)
    cart = Cart.of("m_croma", [ESPRESSO])
    engine.authorize(mandate=mandate, intent_cart=cart, executed_cart=cart, now=T0 + 10)

    # Rewrite history the way a dishonest operator would.
    entries = list(engine.ledger.entries)
    entries[-1].payload["outcome"] = "ALLOW_TAMPERED"

    ok, err = engine.verify_ledger()
    assert not ok
    assert err is not None
