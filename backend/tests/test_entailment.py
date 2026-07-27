"""The entailment engine is the technical core of the submission. Test it hardest."""

from __future__ import annotations

from caveat.constraints import (
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    CumulativeMax,
    ExpiresAt,
    GeoAllow,
    MccAllow,
    MerchantAllow,
    StepUpOver,
    VelocityMax,
)
from caveat.entailment import entails, naive_subset_check

GROCERY_MANDATE = ConstraintSet(
    [
        AmountMax(500_000),  # ₹5,000
        CategoryAllow(("groceries",)),
        MerchantAllow(("m_bigbasket", "m_zepto", "m_blinkit")),
        MccAllow((5411, 5499)),
    ]
)


def test_identical_scope_entails_itself():
    result = entails(GROCERY_MANDATE, GROCERY_MANDATE)
    assert result.entailed
    assert result.solver_result == "unsat"


def test_legitimate_attenuation_is_accepted():
    """Browse-only child: tighter amount, fewer merchants. Genuinely narrower."""
    child = ConstraintSet(
        [
            AmountMax(200_000),  # ₹2,000
            CategoryAllow(("groceries",)),
            MerchantAllow(("m_bigbasket",)),
            MccAllow((5411,)),
        ]
    )
    result = entails(child, GROCERY_MANDATE)
    assert result.entailed, result.counterexample
    assert result.elapsed_ms < 500


def test_escalation_by_widened_bound_is_rejected():
    """Child claims ₹50,000 under a ₹5,000 parent."""
    child = ConstraintSet(
        [
            AmountMax(5_000_000),
            CategoryAllow(("groceries",)),
            MerchantAllow(("m_bigbasket",)),
            MccAllow((5411,)),
        ]
    )
    result = entails(child, GROCERY_MANDATE)
    assert not result.entailed
    ce = result.counterexample
    assert ce is not None
    assert ce.amount > 500_000
    assert any("amount" in v for v in ce.violated)


def test_escalation_by_dropped_constraint_is_rejected():
    """The headline case: FEWER caveats, MORE authority.

    A naive subset check passes this. Entailment does not.
    """
    child = ConstraintSet([AmountMax(500_000)])

    assert naive_subset_check(child, GROCERY_MANDATE) is True, (
        "the naive check is supposed to be fooled here — that is the whole point"
    )

    result = entails(child, GROCERY_MANDATE)
    assert not result.entailed
    ce = result.counterexample
    assert ce is not None
    # The counterexample must break at least one of the constraints the child dropped.
    assert ce.violated, "counterexample should name the violated parent constraints"
    assert any(
        "category" in v or "merchant" in v or "MCC" in v for v in ce.violated
    ), ce.violated


def test_merchant_outside_allowlist_is_rejected():
    child = ConstraintSet(
        [
            AmountMax(500_000),
            CategoryAllow(("groceries",)),
            MerchantAllow(("m_bigbasket", "m_rogue")),
            MccAllow((5411,)),
        ]
    )
    result = entails(child, GROCERY_MANDATE)
    assert not result.entailed
    assert result.counterexample is not None
    assert result.counterexample.merchant == "m_rogue"


def test_gift_card_mcc_escalation_is_rejected():
    """MCC 6540 is stored value — the injection payload's target."""
    child = ConstraintSet(
        [
            AmountMax(500_000),
            CategoryAllow(("groceries",)),
            MerchantAllow(("m_bigbasket",)),
            MccAllow((5411, 6540)),
        ]
    )
    result = entails(child, GROCERY_MANDATE)
    assert not result.entailed
    assert result.counterexample is not None
    assert result.counterexample.mcc == "6540"


def test_empty_child_scope_cannot_inherit_full_authority():
    """A child declaring nothing authorizes everything, so it must be rejected."""
    result = entails(ConstraintSet([]), GROCERY_MANDATE)
    assert not result.entailed


def test_child_may_add_constraints_the_parent_lacks():
    child = GROCERY_MANDATE.with_added([GeoAllow(("IN",)), ExpiresAt(1_800_000_000)])
    assert entails(child, GROCERY_MANDATE).entailed


def test_velocity_windows_must_match_to_entail():
    """A bound on a 1h window says nothing about a 24h window. Fail closed."""
    parent = ConstraintSet([VelocityMax(5, 86_400)])
    same_window = ConstraintSet([VelocityMax(3, 86_400)])
    other_window = ConstraintSet([VelocityMax(1, 3_600)])

    assert entails(same_window, parent).entailed
    assert not entails(other_window, parent).entailed


def test_cumulative_budget_narrowing():
    parent = ConstraintSet([CumulativeMax(2_000_000)])
    assert entails(ConstraintSet([CumulativeMax(500_000)]), parent).entailed
    assert not entails(ConstraintSet([CumulativeMax(9_000_000)]), parent).entailed


def test_step_up_threshold_narrowing_follows_authority_region():
    """A lower step-up threshold means a smaller auto-authorized region."""
    parent = ConstraintSet([StepUpOver(1_000_000)])
    assert entails(ConstraintSet([StepUpOver(100_000)]), parent).entailed
    assert not entails(ConstraintSet([StepUpOver(9_000_000)]), parent).entailed


def test_expiry_narrowing():
    parent = ConstraintSet([ExpiresAt(2_000)])
    assert entails(ConstraintSet([ExpiresAt(1_000)]), parent).entailed
    assert not entails(ConstraintSet([ExpiresAt(3_000)]), parent).entailed


def test_entailment_is_fast_enough_for_the_authorization_path():
    """Single-digit milliseconds is a claim we make on stage. Keep it honest."""
    child = GROCERY_MANDATE.with_added([GeoAllow(("IN",))])
    timings = [entails(child, GROCERY_MANDATE).elapsed_ms for _ in range(20)]
    assert max(timings) < 100, f"slowest entailment {max(timings):.1f}ms"


def test_transitive_delegation_chain_holds():
    """root -> shopper -> price checker: each hop must entail its own parent."""
    shopper = GROCERY_MANDATE.with_added([AmountMax(300_000)])
    checker = shopper.with_added([AmountMax(100_000), MerchantAllow(("m_bigbasket",))])

    assert entails(shopper, GROCERY_MANDATE).entailed
    assert entails(checker, shopper).entailed
    # And transitively against the root.
    assert entails(checker, GROCERY_MANDATE).entailed
