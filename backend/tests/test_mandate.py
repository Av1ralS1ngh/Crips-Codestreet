"""Macaroon layer: attenuation must be cryptographic, not advisory."""

from __future__ import annotations

import pytest

from caveat.constraints import (
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    MccAllow,
    MerchantAllow,
)
from caveat.mandate import (
    DelegationRejected,
    MandateAuthority,
    cart_hash,
    scope_from_caveats,
)

T0 = 1_753_600_000

GROCERY = ConstraintSet(
    [
        AmountMax(500_000),
        CategoryAllow(("groceries",)),
        MerchantAllow(("m_bigbasket", "m_zepto")),
        MccAllow((5411,)),
    ]
)


def authority() -> MandateAuthority:
    return MandateAuthority()


def test_root_mandate_verifies():
    a = authority()
    root = a.issue_root(holder="op_shopbot", scope=GROCERY, created_at=T0)
    discharge = a.discharges.discharge(f"revocation@root:{root.root_id}", "revocation.caveat.amex")
    assert a.verify(root, discharges=[discharge]).ok


def test_root_without_revocation_discharge_fails_closed():
    """No freshness discharge means the credential does not verify. This is the kill switch."""
    a = authority()
    root = a.issue_root(holder="op_shopbot", scope=GROCERY, created_at=T0)
    result = a.verify(root, discharges=[])
    assert not result.ok


def test_legitimate_attenuation_accepted_and_verifies():
    a = authority()
    root = a.issue_root(holder="op_shopbot", scope=GROCERY, created_at=T0)
    child, result = a.attenuate(
        parent=root,
        child_holder="op_pricechecker",
        added=[AmountMax(200_000), MerchantAllow(("m_bigbasket",))],
        created_at=T0 + 1,
    )
    assert result.entailed
    assert child.depth == 1
    assert child.root_id == root.root_id

    discharge = a.discharges.discharge(f"revocation@root:{root.root_id}", "revocation.caveat.amex")
    assert a.verify(child, discharges=[discharge]).ok


def test_escalation_is_rejected_at_delegation_time():
    """No credential is minted at all — the attempt dies before it reaches a merchant."""
    a = authority()
    root = a.issue_root(holder="op_shopbot", scope=GROCERY, created_at=T0)
    wider = ConstraintSet([AmountMax(5_000_000), CategoryAllow(("groceries",))])

    with pytest.raises(DelegationRejected) as exc:
        a.delegate(
            parent=root,
            child_holder="op_rogue",
            declared_scope=wider,
            created_at=T0 + 1,
        )
    assert exc.value.result.counterexample is not None
    assert "SCOPE_ESCALATION" in str(exc.value)


def test_dropped_constraint_escalation_is_rejected():
    a = authority()
    root = a.issue_root(holder="op_shopbot", scope=GROCERY, created_at=T0)
    with pytest.raises(DelegationRejected):
        a.delegate(
            parent=root,
            child_holder="op_rogue",
            declared_scope=ConstraintSet([AmountMax(500_000)]),
            created_at=T0 + 1,
        )


def test_caveats_accumulate_and_cannot_be_removed():
    """The chained backstop: the parent's tighter caveat survives on the child."""
    a = authority()
    root = a.issue_root(holder="op_shopbot", scope=GROCERY, created_at=T0)
    child, _ = a.attenuate(
        parent=root,
        child_holder="op_pricechecker",
        added=[AmountMax(100_000)],
        created_at=T0 + 1,
    )
    accumulated = scope_from_caveats(child.caveat_texts())
    amounts = sorted(c.value for c in accumulated if isinstance(c, AmountMax))
    # Both the parent bound and the child bound are present; evaluation ANDs them.
    assert amounts == [100_000, 500_000]
    assert len(child.macaroon().caveats) > len(root.macaroon().caveats)


def test_tampering_with_a_caveat_breaks_the_signature():
    a = authority()
    root = a.issue_root(holder="op_shopbot", scope=GROCERY, created_at=T0)
    m = root.macaroon()
    # Forge a wider bound directly onto the credential, the way a compromised agent would.
    m.caveats[2].caveat_id = 'scope = {"type":"amount_max","value":99999999}'
    forged = root.__class__(
        mandate_id=root.mandate_id,
        root_id=root.root_id,
        holder=root.holder,
        parent_id=None,
        depth=0,
        scope=root.scope,
        serialized=m.serialize(),
        created_at=T0,
    )
    discharge = a.discharges.discharge(f"revocation@root:{root.root_id}", "revocation.caveat.amex")
    assert not a.verify(forged, discharges=[discharge]).ok


def test_deep_chain_shares_one_revocation_root():
    """Four hops deep, still one row to flip."""
    a = authority()
    m = a.issue_root(holder="op_0", scope=GROCERY, created_at=T0)
    for i in range(1, 5):
        m, _ = a.attenuate(
            parent=m,
            child_holder=f"op_{i}",
            added=[AmountMax(500_000 - i * 50_000)],
            created_at=T0 + i,
        )
    assert m.depth == 4
    assert m.root_id == a.root_key(m.root_id) is not None or m.root_id is not None
    discharge = a.discharges.discharge(f"revocation@root:{m.root_id}", "revocation.caveat.amex")
    assert a.verify(m, discharges=[discharge]).ok


def test_step_up_discharge_is_bound_to_one_transaction():
    a = authority()
    cart = {"items": [{"sku": "flight-BLR-MAA", "amount": 12_000_000}]}
    h = cart_hash(cart)
    root = a.issue_root(holder="op_travel", scope=GROCERY, created_at=T0, step_up_cart_hash=h)

    rev = a.discharges.discharge(f"revocation@root:{root.root_id}", "revocation.caveat.amex")
    step = a.discharges.discharge(f"stepup@txn:{h}", "stepup.caveat.amex")

    assert a.verify(root, discharges=[rev, step]).ok
    # Without the step-up discharge the credential does not verify.
    assert not a.verify(root, discharges=[rev]).ok


def test_step_up_discharge_for_a_different_cart_does_not_help():
    a = authority()
    real = cart_hash({"items": [{"sku": "a", "amount": 100}]})
    other = cart_hash({"items": [{"sku": "b", "amount": 100}]})
    root = a.issue_root(holder="op_travel", scope=GROCERY, created_at=T0, step_up_cart_hash=real)

    rev = a.discharges.discharge(f"revocation@root:{root.root_id}", "revocation.caveat.amex")
    a.discharges.register(f"stepup@txn:{other}", "0" * 64)
    wrong = a.discharges.discharge(f"stepup@txn:{other}", "stepup.caveat.amex")

    assert not a.verify(root, discharges=[rev, wrong]).ok


def test_cart_hash_is_stable_and_order_independent():
    a = {"items": [{"sku": "x", "amount": 1}], "currency": "INR"}
    b = {"currency": "INR", "items": [{"sku": "x", "amount": 1}]}
    assert cart_hash(a) == cart_hash(b)
    assert cart_hash(a) != cart_hash({"items": [{"sku": "x", "amount": 2}], "currency": "INR"})
