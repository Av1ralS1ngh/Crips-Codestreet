"""The Agent Evidence Package: it must verify, and it must fail when touched.

Fixtures drive the real CaveatEngine, so every package under test was produced by the
actual decision path — real macaroons, real Z3 entailment proofs, real Merkle ledger.
"""

from __future__ import annotations

import json

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
from caveat.evidence import (
    CHECK_CHAIN_CONTINUITY,
    CHECK_EXECUTED_CART_HASH,
    CHECK_INCLUSION_PROOF,
    CHECK_LEDGER_ROOT,
    CHECK_SIGNATURE,
    PACKAGE_VERSION,
    AgentEvidencePackage,
    build_evidence_package,
    key_id,
    sign_body,
    verify_package,
)
from caveat.pdp import VERDICT_INJECTION_COMPROMISE, VERDICT_WITHIN_MANDATE
from caveat.registry import AgentRegistry

T0 = 1_753_600_000
SIGNING_KEY = "caveat-demo-evidence-key"

SCOPE = ConstraintSet(
    [
        AmountMax(1_000_000),
        CumulativeMax(500_000_000),
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
    for i in range(4)
]


@pytest.fixture()
def engine() -> CaveatEngine:
    e = CaveatEngine()
    e.register_operator("op_shopbot", "ShopBot v2.1", now=T0)
    e.register_operator("op_pricechecker", "PriceCheckerBot", now=T0)
    e.register_operator("op_fulfil", "FulfilmentBot", now=T0)
    return e


def _deep_mandate(engine: CaveatEngine):
    """Root → PriceCheckerBot → FulfilmentBot. Two real entailment proofs on the chain."""
    root = engine.grant(holder="op_shopbot", scope=SCOPE, now=T0)
    hop1 = engine.attenuate(
        parent=root, child_holder="op_pricechecker", added=[AmountMax(700_000)], now=T0 + 1
    )
    assert hop1.accepted and hop1.mandate is not None
    hop2 = engine.attenuate(
        parent=hop1.mandate,
        child_holder="op_fulfil",
        added=[MerchantAllow(("m_croma",))],
        now=T0 + 2,
    )
    assert hop2.accepted and hop2.mandate is not None
    return root, hop1.mandate, hop2.mandate


def _injection_package(engine: CaveatEngine):
    """Beat 1's blocked transaction, packaged. Returns (package, intent, executed)."""
    mandate = engine.grant(holder="op_shopbot", scope=SCOPE, now=T0)
    intent = Cart.of("m_croma", [ESPRESSO])
    executed = Cart.of("m_croma", [ESPRESSO, *GIFT_CARDS])
    decision = engine.authorize(
        mandate=mandate, intent_cart=intent, executed_cart=executed, now=T0 + 10
    )
    package = build_evidence_package(
        engine=engine,
        decision=decision,
        intent_cart=intent,
        executed_cart=executed,
        signing_key=SIGNING_KEY,
        issued_at=T0 + 60,
    )
    return package, intent, executed, decision


# ----------------------------------------------------------------------------------
# The happy path: the artifact a dispute reviewer receives.
# ----------------------------------------------------------------------------------


def test_package_verifies_end_to_end(engine: CaveatEngine):
    package, _intent, _executed, _decision = _injection_package(engine)
    result = verify_package(package, SIGNING_KEY)
    assert result.ok, result.render_text()
    assert not result.failures
    # Both independent guarantees are actually exercised, not just asserted.
    names = {c.name for c in result.checks}
    assert CHECK_SIGNATURE in names
    assert CHECK_INCLUSION_PROOF in names


def test_inclusion_proof_verifies_against_the_real_ledger_root(engine: CaveatEngine):
    package, _intent, _executed, decision = _injection_package(engine)

    proof = package.inclusion_proof()
    assert proof.index == decision.ledger_seq
    assert proof.path, "a multi-entry ledger must produce a non-empty audit path"
    assert proof.verify(), "the audit path must recompute the published root"
    assert proof.root == engine.ledger_root()

    # And the packaged root must be checkable against a root supplied out of band.
    result = verify_package(package, SIGNING_KEY, expected_root=engine.ledger_root())
    assert result.ok, result.render_text()


def test_package_survives_a_json_round_trip(engine: CaveatEngine):
    package, _intent, _executed, _decision = _injection_package(engine)
    payload = json.loads(package.to_json())
    assert payload["caveat_evidence_version"] == PACKAGE_VERSION

    # A reviewer holds JSON, not a Python object, and must verify from that alone.
    assert verify_package(payload, SIGNING_KEY).ok
    rehydrated = AgentEvidencePackage.from_dict(payload)
    assert rehydrated.package_id == package.package_id
    assert rehydrated.signature == package.signature
    assert verify_package(rehydrated, SIGNING_KEY).ok


def test_package_carries_liability_verdict_and_liable_party(engine: CaveatEngine):
    package, _intent, _executed, _decision = _injection_package(engine)
    assert package.verdict == VERDICT_INJECTION_COMPROMISE
    assert "not liable" in package.liable_party
    assert package.body["liability"]["meaning"]
    diff = package.body["cart_diff"]
    assert diff["diverged"] is True
    assert len(diff["added"]) == 4
    assert diff["added_value"] == 2_000_000


def test_package_carries_every_hop_with_scope_and_entailment(engine: CaveatEngine):
    root, mid, leaf = _deep_mandate(engine)
    cart = Cart.of("m_croma", [ESPRESSO])
    decision = engine.authorize(
        mandate=leaf, intent_cart=cart, executed_cart=cart, now=T0 + 10
    )
    assert decision.outcome == "ALLOW", decision.to_dict()
    assert decision.verdict == VERDICT_WITHIN_MANDATE

    package = build_evidence_package(
        engine=engine,
        decision=decision,
        intent_cart=cart,
        executed_cart=cart,
        signing_key=SIGNING_KEY,
        issued_at=T0 + 60,
    )
    hops = package.hops()
    assert [h.mandate_id for h in hops] == [root.mandate_id, mid.mandate_id, leaf.mandate_id]
    assert [h.depth for h in hops] == [0, 1, 2]
    assert [h.holder for h in hops] == ["op_shopbot", "op_pricechecker", "op_fulfil"]

    # The root was granted, not narrowed, so it carries no entailment proof.
    assert hops[0].entailment is None
    for hop in hops[1:]:
        assert hop.declared_scope, "each hop must publish the scope it declared"
        assert hop.entailment is not None
        assert hop.entailment["entailed"] is True
        assert hop.entailment["solver_result"] == "unsat"
        assert hop.entailment["elapsed_ms"] >= 0
        assert hop.ledger_seq is not None

    assert verify_package(package, SIGNING_KEY).ok


def test_render_text_is_a_readable_case_file(engine: CaveatEngine):
    package, _intent, _executed, _decision = _injection_package(engine)
    text = package.render_text()
    assert "AGENT EVIDENCE PACKAGE" in text
    assert "MANDATE CHAIN" in text
    assert "CART: SIGNED INTENT vs EXECUTED" in text
    assert VERDICT_INJECTION_COMPROMISE in text
    assert "ADDED" in text
    assert package.signature in text
    assert "DISCLOSURES" in text
    # The honest limitation must ride inside the artifact, not only in the docstring.
    assert "consistency proof" in text


def test_operator_risk_snapshot_rides_along(engine: CaveatEngine):
    package_engine = engine
    registry = AgentRegistry(package_engine.store)
    mandate = package_engine.grant(holder="op_shopbot", scope=SCOPE, now=T0)
    intent = Cart.of("m_croma", [ESPRESSO])
    executed = Cart.of("m_croma", [ESPRESSO, *GIFT_CARDS])
    decision = package_engine.authorize(
        mandate=mandate, intent_cart=intent, executed_cart=executed, now=T0 + 10
    )
    risk = registry.assess("op_shopbot", now=T0 + 11)

    package = build_evidence_package(
        engine=package_engine,
        decision=decision,
        intent_cart=intent,
        executed_cart=executed,
        signing_key=SIGNING_KEY,
        issued_at=T0 + 60,
        operator_risk=risk.to_dict(),
    )
    assert package.body["operator_risk"]["operator_id"] == "op_shopbot"
    assert package.body["operator_risk"]["band"] == risk.band
    assert "OPERATOR RISK AT ISSUE TIME" in package.render_text()
    assert verify_package(package, SIGNING_KEY).ok


def test_key_id_identifies_without_leaking(engine: CaveatEngine):
    package, _i, _e, _d = _injection_package(engine)
    assert package.signing_key_id == key_id(SIGNING_KEY)
    assert SIGNING_KEY not in package.to_json()
    assert package.signing_key_id != key_id("another-key")


# ----------------------------------------------------------------------------------
# Tampering. Each of these is a way someone tries to rewrite history.
# ----------------------------------------------------------------------------------


def test_tampered_amount_fails_verification(engine: CaveatEngine):
    package, _i, _e, _d = _injection_package(engine)
    payload = package.to_dict()
    payload["package"]["decision"]["amount"] = 1

    result = verify_package(payload, SIGNING_KEY)
    assert not result.ok
    assert CHECK_SIGNATURE in {c.name for c in result.failures}


def test_tampered_verdict_fails_verification(engine: CaveatEngine):
    """The dispute-relevant edit: relabel fraud as an ordinary in-mandate purchase."""
    package, _i, _e, _d = _injection_package(engine)
    payload = package.to_dict()
    payload["package"]["liability"]["verdict"] = VERDICT_WITHIN_MANDATE
    payload["package"]["liability"]["liable_party"] = "cardholder"

    result = verify_package(payload, SIGNING_KEY)
    assert not result.ok
    assert CHECK_SIGNATURE in {c.name for c in result.failures}


def test_verification_fails_under_the_wrong_key(engine: CaveatEngine):
    package, _i, _e, _d = _injection_package(engine)
    result = verify_package(package, "not-the-signing-key")
    assert not result.ok
    assert CHECK_SIGNATURE in {c.name for c in result.failures}


def test_resigned_cart_edit_is_still_caught_by_the_recorded_hash(engine: CaveatEngine):
    """An insider holding the signing key still cannot quietly shrink the cart.

    The decision record pins the executed cart's hash, so re-signing an edited body
    produces a package whose signature is valid and whose contents are self-contradictory.
    """
    package, _intent, _executed, _decision = _injection_package(engine)
    payload = package.to_dict()
    payload["package"]["executed_cart"]["lines"] = payload["package"]["executed_cart"]["lines"][:1]
    payload["signature"]["value"] = sign_body(payload["package"], SIGNING_KEY)

    result = verify_package(payload, SIGNING_KEY)
    assert not result.ok
    failed = {c.name for c in result.failures}
    assert CHECK_SIGNATURE not in failed, "the forger re-signed, so the HMAC alone is satisfied"
    assert CHECK_EXECUTED_CART_HASH in failed


def test_resigned_proof_edit_fails_the_merkle_check(engine: CaveatEngine):
    package, _i, _e, _d = _injection_package(engine)
    payload = package.to_dict()
    path = payload["package"]["ledger"]["inclusion_proof"]["path"]
    assert path, "need a non-empty audit path to tamper with"
    original = path[0]["hash"]
    path[0]["hash"] = ("0" if original[0] != "0" else "1") + original[1:]
    payload["signature"]["value"] = sign_body(payload["package"], SIGNING_KEY)

    result = verify_package(payload, SIGNING_KEY)
    assert not result.ok
    assert CHECK_INCLUSION_PROOF in {c.name for c in result.failures}


def test_resigned_chain_edit_fails_continuity(engine: CaveatEngine):
    """Drop the middle hop to hide where authority came from."""
    _root, _mid, leaf = _deep_mandate(engine)
    cart = Cart.of("m_croma", [ESPRESSO])
    decision = engine.authorize(mandate=leaf, intent_cart=cart, executed_cart=cart, now=T0 + 10)
    package = build_evidence_package(
        engine=engine,
        decision=decision,
        intent_cart=cart,
        executed_cart=cart,
        signing_key=SIGNING_KEY,
        issued_at=T0 + 60,
    )
    payload = package.to_dict()
    chain = payload["package"]["mandate_chain"]
    assert len(chain) == 3
    del chain[1]
    payload["signature"]["value"] = sign_body(payload["package"], SIGNING_KEY)

    result = verify_package(payload, SIGNING_KEY)
    assert not result.ok
    assert CHECK_CHAIN_CONTINUITY in {c.name for c in result.failures}


def test_package_from_a_stale_root_is_flagged_against_a_supplied_root(engine: CaveatEngine):
    """A root taken out of band must match; we do not implement consistency proofs."""
    package, _i, _e, _d = _injection_package(engine)
    stale_root = package.body["ledger"]["root"]

    # More decisions land, so the live root moves on.
    mandate = engine.grant(holder="op_shopbot", scope=SCOPE, now=T0 + 100)
    cart = Cart.of("m_croma", [ESPRESSO])
    engine.authorize(mandate=mandate, intent_cart=cart, executed_cart=cart, now=T0 + 101)
    assert engine.ledger_root() != stale_root

    # The package still verifies on its own terms...
    assert verify_package(package, SIGNING_KEY).ok
    # ...and is honestly reported as not matching the current root.
    result = verify_package(package, SIGNING_KEY, expected_root=engine.ledger_root())
    assert not result.ok
    assert CHECK_LEDGER_ROOT in {c.name for c in result.failures}


def test_malformed_payload_is_rejected_not_crashed():
    result = verify_package({"package": "nonsense"}, SIGNING_KEY)
    assert not result.ok
    assert result.failures
