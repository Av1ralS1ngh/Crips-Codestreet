"""Cross-protocol adapters: three wire formats, one canonical mandate object.

The claim these tests defend is narrow and checkable: AP2, ACP and MCP requests describing
the same authority produce byte-identical constraint sets, and every restriction the
canonical DSL cannot hold surfaces as a warning instead of vanishing.
"""

from __future__ import annotations

import copy
from unittest import mock

import pytest

from caveat.adapters import (
    ERR_AMBIGUOUS_MONEY_UNITS,
    ERR_AMBIGUOUS_PROTOCOL,
    ERR_FLOAT_MONEY,
    ERR_INVALID_CREDENTIAL,
    ERR_MISSING_FIELD,
    ERR_TOTAL_MISMATCH,
    ERR_UNKNOWN_PROTOCOL,
    ERR_UNSUPPORTED_TOOL,
    PROTOCOL_ACP,
    PROTOCOL_AP2,
    PROTOCOL_MCP,
    SIG_ABSENT,
    SIG_PRESENT_UNVERIFIED,
    WARN_APPROXIMATED_CONSTRAINT,
    WARN_MISSING_LINE_DETAIL,
    WARN_NO_CART,
    WARN_NO_EXPIRY,
    WARN_NO_SIGNATURE,
    WARN_PROTOCOL_CANNOT_EXPRESS,
    WARN_SIGNATURE_NOT_VERIFIED,
    WARN_UNBOUNDED_SCOPE,
    WARN_UNENFORCEABLE_INTENT,
    WARN_UNMAPPED_FIELD,
    WARN_UNREPRESENTABLE_CONSTRAINT,
    AdapterError,
    normalize,
    sniff,
)
from caveat.cart import diff_carts
from caveat.constraints import (
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    MccAllow,
    MerchantAllow,
    StepUpOver,
    VelocityMax,
)
from caveat.engine import CaveatEngine
from caveat.entailment import entails, naive_subset_check

T0 = 1_785_142_800  # 2026-07-27T09:00:00Z, the instant the fixtures below are minted at
T_EXPIRY = 1_785_229_200  # +24h

VALID_FROM = "2026-07-27T09:00:00Z"
VALID_UNTIL = "2026-07-28T09:00:00Z"


# ----------------------------------------------------------------------------------
# Fixtures: the same authority, expressed three ways.
#
# amount <= Rs 5,000 / merchant in {m_croma} / MCC in {5722} / category in {appliances}
# ----------------------------------------------------------------------------------


def ap2_payload() -> dict:
    return {
        "intent_mandate": {
            "@context": [
                "https://www.w3.org/ns/credentials/v2",
                "https://ap2.dev/contexts/v1",
            ],
            "id": "urn:uuid:9a1f-intent",
            "type": ["VerifiableCredential", "IntentMandate"],
            "issuer": "did:example:cardholder:9f2a",
            "credentialSubject": {
                "id": "did:example:agent:shopbot",
                "intent": {
                    "max_amount": {"currency": "INR", "value": "5000.00"},
                    "merchants": {"allow": ["m_croma"]},
                    "merchant_category_codes": ["5722"],
                    "categories": ["appliances"],
                },
            },
            "proof": {
                "type": "DataIntegrityProof",
                "cryptosuite": "ecdsa-rdfc-2019",
                "created": VALID_FROM,
                "verificationMethod": "did:example:cardholder:9f2a#key-1",
                "proofPurpose": "assertionMethod",
                "proofValue": "z3MvGcVxzRzzpKF1HkxDdCyEtBEyghwuUpTLNqBaSVy8",
            },
        },
        "cart_mandate": cart_mandate_vc(),
    }


def cart_mandate_vc() -> dict:
    return {
        "@context": [
            "https://www.w3.org/ns/credentials/v2",
            "https://ap2.dev/contexts/v1",
        ],
        "type": ["VerifiableCredential", "CartMandate"],
        "issuer": "did:example:merchant:croma",
        "credentialSubject": {
            "id": "did:example:agent:shopbot",
            "contents": {
                "id": "cart_7781",
                "merchant": {"id": "m_croma", "name": "Croma"},
                "payment_request": {
                    "method_data": [{"supported_methods": "card"}],
                    "details": {
                        "id": "pr_1",
                        "display_items": [
                            {
                                "label": "Budget espresso machine",
                                "sku": "sku_espresso_01",
                                "amount": {"currency": "INR", "value": "4000.00"},
                                "quantity": 1,
                                "merchant_category_code": 5722,
                                "category": "appliances",
                            }
                        ],
                        "total": {
                            "label": "Total",
                            "amount": {"currency": "INR", "value": "4000.00"},
                        },
                    },
                },
            },
        },
        "proof": {
            "type": "DataIntegrityProof",
            "cryptosuite": "ecdsa-jcs-2019",
            "verificationMethod": "did:example:merchant:croma#key-1",
            "proofValue": "zQmT4pAcMEBrJ8sVi9Bw2wLpS7z3Xq",
        },
    }


def acp_payload() -> dict:
    return {
        "shared_payment_token": {
            "id": "spt_1QxAbC",
            "payment_method": {"type": "card", "card_number_type": "network_token"},
            "allowance": {
                "reason": "one_time",
                "max_amount": 500_000,
                "currency": "inr",
                "merchant_id": "m_croma",
            },
            "restrictions": {
                "allowed_mcc": [5722],
                "allowed_categories": ["appliances"],
            },
            "agent": {"id": "op_shopbot"},
            "issuer": "stripe",
        },
        "checkout_session": {
            "id": "cs_test_123",
            "currency": "inr",
            "merchant": {"id": "m_croma"},
            "line_items": [
                {
                    "id": "li_1",
                    "item": {"id": "sku_espresso_01", "name": "Budget espresso machine"},
                    "base_amount": 400_000,
                    "discount": 0,
                    "subtotal": 400_000,
                    "tax": 0,
                    "total": 400_000,
                    "merchant_category_code": 5722,
                    "category": "appliances",
                }
            ],
            "totals": [{"type": "total", "amount": 400_000}],
        },
    }


def mcp_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {
            "name": "checkout",
            "arguments": {
                "merchant_id": "m_croma",
                "currency": "INR",
                "spend_limit_minor": 500_000,
                "allowed_mcc": [5722],
                "allowed_categories": ["appliances"],
                "items": [
                    {
                        "sku": "sku_espresso_01",
                        "name": "Budget espresso machine",
                        "unit_amount_minor": 400_000,
                        "quantity": 1,
                        "mcc": 5722,
                        "category": "appliances",
                    }
                ],
                "agent_id": "op_shopbot",
            },
        },
    }


EXPECTED_SCOPE = ConstraintSet(
    [
        AmountMax(500_000),
        MerchantAllow(("m_croma",)),
        MccAllow((5722,)),
        CategoryAllow(("appliances",)),
    ]
)


# ----------------------------------------------------------------------------------
# The point: one canonical object out of three wire formats.
# ----------------------------------------------------------------------------------


def test_three_protocols_produce_one_identical_constraint_set():
    """Same authority in AP2, ACP and MCP -> byte-identical canonical scope.

    Byte-identical matters: the constraint set is what gets HMAC'd into a caveat and
    hashed into the ledger, so field ordering cannot be allowed to depend on wire format.
    """
    scopes = {
        normalize(p).protocol: normalize(p).scope.to_json()
        for p in (ap2_payload(), acp_payload(), mcp_payload())
    }
    assert set(scopes) == {PROTOCOL_AP2, PROTOCOL_ACP, PROTOCOL_MCP}
    assert len(set(scopes.values())) == 1, scopes
    assert next(iter(scopes.values())) == EXPECTED_SCOPE.to_json()


def test_three_protocols_produce_one_identical_intent_cart():
    hashes = {normalize(p).protocol: normalize(p).intent_cart.hash() for p in (
        ap2_payload(),
        acp_payload(),
        mcp_payload(),
    )}
    assert len(set(hashes.values())) == 1, hashes


def test_every_protocol_names_its_agent():
    assert normalize(ap2_payload()).holder == "did:example:agent:shopbot"
    assert normalize(acp_payload()).holder == "op_shopbot"
    assert normalize(mcp_payload()).holder == "op_shopbot"


# ----------------------------------------------------------------------------------
# The sniffer.
# ----------------------------------------------------------------------------------


def test_sniffer_picks_the_right_adapter():
    assert sniff(ap2_payload()) == PROTOCOL_AP2
    assert sniff(acp_payload()) == PROTOCOL_ACP
    assert sniff(mcp_payload()) == PROTOCOL_MCP


def test_sniffer_recognises_a_bare_verifiable_credential():
    assert sniff(cart_mandate_vc()) == PROTOCOL_AP2
    assert normalize(cart_mandate_vc()).protocol == PROTOCOL_AP2


def test_sniffer_recognises_a_flattened_tool_call():
    payload = {
        "tool": "checkout",
        "arguments": mcp_payload()["params"]["arguments"],
    }
    assert sniff(payload) == PROTOCOL_MCP
    assert normalize(payload).scope.to_json() == EXPECTED_SCOPE.to_json()


def test_explicit_protocol_field_wins_over_sniffing():
    payload = acp_payload()
    payload["protocol"] = "stripe-acp"
    assert sniff(payload) == PROTOCOL_ACP
    assert normalize(payload).protocol == PROTOCOL_ACP


def test_unknown_declared_protocol_raises():
    payload = acp_payload()
    payload["protocol"] = "tap"
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_UNKNOWN_PROTOCOL


def test_unrecognised_payload_raises():
    with pytest.raises(AdapterError) as exc:
        normalize({"hello": "world"})
    assert exc.value.code == ERR_UNKNOWN_PROTOCOL


def test_payload_matching_two_protocols_is_refused_not_guessed():
    """A blob that reads as two protocols is a confused-deputy shape. Refuse it."""
    payload = {**acp_payload(), **mcp_payload()}
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_AMBIGUOUS_PROTOCOL


def test_non_object_payload_raises():
    with pytest.raises(AdapterError):
        normalize(["not", "an", "object"])  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------
# AP2.
# ----------------------------------------------------------------------------------


def test_ap2_round_trips_into_the_expected_constraint_set():
    req = normalize(ap2_payload())
    assert req.protocol == PROTOCOL_AP2
    assert req.scope.to_json() == EXPECTED_SCOPE.to_json()
    assert req.issuer == "did:example:cardholder:9f2a"
    assert req.credential_id == "urn:uuid:9a1f-intent"


def test_ap2_cart_mandate_becomes_the_intent_cart():
    req = normalize(ap2_payload())
    assert req.intent_cart.merchant == "m_croma"
    assert req.intent_cart.total() == 400_000
    assert req.intent_cart.lines[0].sku == "sku_espresso_01"
    assert req.intent_cart.lines[0].mcc == 5722


def test_ap2_validity_window_becomes_expiry_constraints():
    payload = ap2_payload()
    payload["intent_mandate"]["validFrom"] = VALID_FROM
    payload["intent_mandate"]["validUntil"] = VALID_UNTIL
    kinds = {c.kind: c for c in normalize(payload).scope}
    assert kinds["not_before"].value == T0
    assert kinds["expires_at"].value == T_EXPIRY


def test_ap2_cart_expiry_tightens_the_intent_expiry():
    """A stale signed cart must not be replayable under a still-live standing intent."""
    payload = ap2_payload()
    payload["intent_mandate"]["validUntil"] = VALID_UNTIL
    payload["cart_mandate"]["credentialSubject"]["contents"]["cart_expiry"] = T0 + 600
    kinds = {c.kind: c for c in normalize(payload).scope}
    assert kinds["expires_at"].value == T0 + 600


def test_ap2_signature_verification_is_reported_as_a_stub():
    """We validate the proof block's structure. We do not verify it. Never claim otherwise."""
    req = normalize(ap2_payload())
    assert req.signature.state == SIG_PRESENT_UNVERIFIED
    assert req.signature.verified is False
    assert req.signature.to_dict()["verification_implemented"] is False
    assert req.signature.algorithm == "ecdsa-rdfc-2019"
    assert req.has_warning(WARN_SIGNATURE_NOT_VERIFIED)


def test_ap2_missing_proof_block_raises():
    payload = ap2_payload()
    del payload["intent_mandate"]["proof"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_INVALID_CREDENTIAL
    assert "proof" in exc.value.message


def test_ap2_proof_without_a_signature_value_raises():
    payload = ap2_payload()
    del payload["intent_mandate"]["proof"]["proofValue"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_INVALID_CREDENTIAL


def test_ap2_wrong_base_context_raises():
    payload = ap2_payload()
    payload["intent_mandate"]["@context"] = ["https://example.com/not-a-vc"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_INVALID_CREDENTIAL


def test_ap2_missing_credential_subject_id_raises():
    payload = ap2_payload()
    del payload["intent_mandate"]["credentialSubject"]["id"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_MISSING_FIELD


def test_ap2_subject_mismatch_between_mandates_raises():
    """A Cart Mandate issued to a different agent is a substitution attack, not a typo."""
    payload = ap2_payload()
    payload["cart_mandate"]["credentialSubject"]["id"] = "did:example:agent:someone_else"
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_INVALID_CREDENTIAL


def test_ap2_wrong_credential_type_raises():
    payload = cart_mandate_vc()
    payload["type"] = ["VerifiableCredential", "DriversLicence"]
    with pytest.raises(AdapterError) as exc:
        normalize({"cart_mandate": payload})
    assert exc.value.code == ERR_INVALID_CREDENTIAL


def test_ap2_float_amount_is_rejected_outright():
    """A JSON float in a money field never reaches the decision path."""
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"]["max_amount"] = {
        "currency": "INR",
        "value": 5000.00,
    }
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_FLOAT_MONEY


def test_ap2_cart_total_that_does_not_add_up_raises():
    payload = ap2_payload()
    details = payload["cart_mandate"]["credentialSubject"]["contents"]["payment_request"]["details"]
    details["total"]["amount"]["value"] = "9000.00"
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_TOTAL_MISMATCH


def test_ap2_sub_paise_precision_is_refused_rather_than_rounded():
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"]["max_amount"]["value"] = "5000.005"
    with pytest.raises(AdapterError):
        normalize(payload)


def test_ap2_user_confirmation_flag_maps_to_a_zero_step_up_threshold():
    """"the human must confirm the cart" is exactly "nothing is auto-authorized"."""
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"][
        "user_cart_confirmation_required"
    ] = True
    scope = normalize(payload).scope
    assert StepUpOver(0) in list(scope)


def test_ap2_executed_cart_is_diffable_against_the_signed_cart():
    """The AP2 post-signature mutation class, end to end through the adapter."""
    payload = ap2_payload()
    payload["executed_cart"] = {
        "merchant": {"id": "m_croma"},
        "display_items": [
            {
                "label": "Budget espresso machine",
                "sku": "sku_espresso_01",
                "amount": {"currency": "INR", "value": "4000.00"},
                "merchant_category_code": 5722,
                "category": "appliances",
            },
            {
                "label": "Stored-value gift card",
                "sku": "sku_giftcard_1",
                "amount": {"currency": "INR", "value": "50000.00"},
                "merchant_category_code": 6540,
                "category": "stored_value",
            },
        ],
    }
    req = normalize(payload)
    assert req.executed_cart is not None

    diff = diff_carts(req.intent_cart, req.executed_cart)
    assert diff.diverged()
    assert len(diff.added) == 1
    assert diff.added[0].mcc == 6540
    assert diff.added_value() == 5_000_000


# -- AP2 lossy translation ---------------------------------------------------------


def test_ap2_sku_restriction_is_recorded_as_widening_not_dropped():
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"]["skus"] = ["sku_espresso_01"]
    req = normalize(payload)
    widening = [w for w in req.widening_warnings() if w.code == WARN_UNREPRESENTABLE_CONSTRAINT]
    assert widening, req.warning_codes()
    assert "SKU" in widening[0].detail
    assert req.lossy_widening


def test_ap2_refundability_requirement_is_recorded_as_widening():
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"]["required_refundability"] = True
    req = normalize(payload)
    assert any(
        w.widening and "refundab" in w.detail for w in req.warnings
    ), req.warning_codes()


def test_ap2_natural_language_intent_is_flagged_as_unenforceable():
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"][
        "natural_language_description"
    ] = "only buy the cheapest espresso machine"
    req = normalize(payload)
    assert req.has_warning(WARN_UNENFORCEABLE_INTENT)


def test_ap2_denylist_is_recorded_as_widening():
    """The DSL holds allowlists only, so a dropped denial grants what the source refused."""
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"]["merchants"] = {
        "allow": ["m_croma"],
        "deny": ["m_rogue"],
    }
    req = normalize(payload)
    assert any(w.widening and "denylist" in w.detail for w in req.warnings)


def test_ap2_unknown_constraint_key_is_never_silently_dropped():
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"]["max_altitude_metres"] = 3000
    req = normalize(payload)
    unmapped = [w for w in req.warnings if w.code == WARN_UNMAPPED_FIELD]
    assert unmapped and unmapped[0].widening
    assert "max_altitude_metres" in unmapped[0].detail


def test_ap2_cart_mandate_alone_derives_a_scope_and_says_it_approximated():
    req = normalize(cart_mandate_vc())
    kinds = {c.kind for c in req.scope}
    assert {"amount_max", "merchant_allow", "mcc_allow", "category_allow"} <= kinds
    assert req.has_warning(WARN_APPROXIMATED_CONSTRAINT)
    assert req.lossy_widening


def test_ap2_display_item_without_mcc_fails_closed_and_warns():
    payload = cart_mandate_vc()
    del payload["credentialSubject"]["contents"]["payment_request"]["details"]["display_items"][0][
        "merchant_category_code"
    ]
    req = normalize(payload)
    assert req.intent_cart.lines[0].mcc == 0
    assert req.has_warning(WARN_MISSING_LINE_DETAIL)
    # MCC 0 is in no allowlist, so it is denied by any MCC-constrained mandate.
    assert MccAllow((5722,)).check(
        _ctx_for(req.intent_cart.lines[0])
    ) is not None


def _ctx_for(line):
    from caveat.constraints import DecisionContext

    return DecisionContext(
        amount=line.amount,
        merchant="m_croma",
        mcc=line.mcc,
        category=line.category,
        geo="IN",
        ts=T0,
    )


# ----------------------------------------------------------------------------------
# ACP.
# ----------------------------------------------------------------------------------


def test_acp_round_trips_into_the_expected_constraint_set():
    req = normalize(acp_payload())
    assert req.protocol == PROTOCOL_ACP
    assert req.scope.to_json() == EXPECTED_SCOPE.to_json()
    assert req.credential_id == "spt_1QxAbC"
    assert req.issuer == "stripe"


def test_acp_amounts_stay_integer_minor_units():
    req = normalize(acp_payload())
    assert req.intent_cart.total() == 400_000
    assert all(isinstance(line.amount, int) for line in req.intent_cart.lines)


def test_acp_expiry_maps_from_rfc3339_and_from_epoch_alike():
    iso = acp_payload()
    iso["shared_payment_token"]["allowance"]["expires_at"] = "2026-07-28T09:00:00Z"
    epoch = acp_payload()
    epoch["shared_payment_token"]["allowance"]["expires_at"] = T_EXPIRY
    for payload in (iso, epoch):
        kinds = {c.kind: c for c in normalize(payload).scope}
        assert kinds["expires_at"].value == T_EXPIRY


def test_acp_naive_timestamp_is_refused():
    payload = acp_payload()
    payload["shared_payment_token"]["allowance"]["expires_at"] = "2026-07-28T09:00:00"
    with pytest.raises(AdapterError):
        normalize(payload)


def test_acp_session_binding_is_recorded_as_widening():
    """The token is good for one checkout session; the DSL has no session predicate."""
    payload = acp_payload()
    payload["shared_payment_token"]["allowance"]["checkout_session_id"] = "cs_test_123"
    req = normalize(payload)
    widening = [w for w in req.widening_warnings() if "checkout session" in w.detail]
    assert widening, req.warning_codes()
    # The binding is not thrown away — it is handed on so a caller can re-bind it.
    assert req.metadata["checkout_session_id"] == "cs_test_123"


def test_acp_recurring_allowance_is_approximated_in_the_tighter_direction():
    payload = acp_payload()
    payload["shared_payment_token"]["allowance"]["reason"] = "recurring"
    req = normalize(payload)
    kinds = {c.kind: c for c in req.scope}
    assert kinds["amount_max"].value == 500_000
    assert kinds["cumulative_max"].value == 500_000
    approximated = [w for w in req.warnings if w.code == WARN_APPROXIMATED_CONSTRAINT]
    assert approximated and not approximated[0].widening


def test_acp_names_the_constraint_kinds_its_spec_cannot_carry():
    req = normalize(acp_payload())
    gaps = [w for w in req.warnings if w.code == WARN_PROTOCOL_CANNOT_EXPRESS]
    assert gaps, req.warning_codes()
    assert "geo_allow" in gaps[0].detail
    assert "not_before" in gaps[0].detail
    # mcc and category came in through the restrictions extension, so they are not gaps.
    assert "mcc_allow" not in gaps[0].detail
    assert "category_allow" not in gaps[0].detail


def test_acp_velocity_extension_maps_to_a_velocity_constraint():
    payload = acp_payload()
    payload["shared_payment_token"]["restrictions"]["max_transactions"] = {
        "count": 3,
        "window_seconds": 3600,
    }
    assert VelocityMax(3, 3600) in list(normalize(payload).scope)


def test_acp_token_is_reported_as_unverified_opaque_bearer():
    req = normalize(acp_payload())
    assert req.signature.state == SIG_PRESENT_UNVERIFIED
    assert req.signature.verified is False
    assert req.has_warning(WARN_SIGNATURE_NOT_VERIFIED)


def test_acp_missing_allowance_raises():
    payload = acp_payload()
    del payload["shared_payment_token"]["allowance"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_MISSING_FIELD


def test_acp_allowance_without_a_cap_raises():
    payload = acp_payload()
    del payload["shared_payment_token"]["allowance"]["max_amount"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_MISSING_FIELD


def test_acp_float_allowance_is_rejected():
    payload = acp_payload()
    payload["shared_payment_token"]["allowance"]["max_amount"] = 5000.00
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_FLOAT_MONEY


def test_acp_unattributable_token_raises():
    payload = acp_payload()
    del payload["shared_payment_token"]["agent"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_MISSING_FIELD


def test_acp_checkout_session_total_that_disagrees_with_its_lines_raises():
    payload = acp_payload()
    payload["checkout_session"]["totals"] = [{"type": "total", "amount": 900_000}]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_TOTAL_MISMATCH


def test_acp_unknown_restriction_key_is_never_silently_dropped():
    payload = acp_payload()
    payload["shared_payment_token"]["restrictions"]["allowed_days_of_week"] = ["MON"]
    req = normalize(payload)
    unmapped = [w for w in req.warnings if w.code == WARN_UNMAPPED_FIELD]
    assert unmapped and unmapped[0].widening
    assert "allowed_days_of_week" in unmapped[0].detail


def test_acp_executed_order_becomes_the_executed_cart():
    payload = acp_payload()
    payload["executed_order"] = copy.deepcopy(payload["checkout_session"])
    payload["executed_order"]["line_items"].append(
        {
            "id": "li_2",
            "item": {"id": "sku_giftcard_1", "name": "Stored-value gift card"},
            "total": 5_000_000,
            "merchant_category_code": 6540,
            "category": "stored_value",
        }
    )
    payload["executed_order"]["totals"] = [{"type": "total", "amount": 5_400_000}]

    req = normalize(payload)
    assert req.executed_cart is not None
    diff = diff_carts(req.intent_cart, req.executed_cart)
    assert diff.added_value() == 5_000_000


# ----------------------------------------------------------------------------------
# MCP.
# ----------------------------------------------------------------------------------


def test_mcp_round_trips_into_the_expected_constraint_set():
    req = normalize(mcp_payload())
    assert req.protocol == PROTOCOL_MCP
    assert req.scope.to_json() == EXPECTED_SCOPE.to_json()
    assert req.metadata["tool"] == "checkout"


def test_mcp_quantity_multiplies_the_unit_amount():
    payload = mcp_payload()
    payload["params"]["arguments"]["items"][0]["quantity"] = 3
    req = normalize(payload)
    assert req.intent_cart.total() == 1_200_000
    assert req.intent_cart.lines[0].qty == 3


def test_mcp_bare_spend_limit_is_refused_rather_than_guessed():
    """Rs 5,000 and 5,000 paise differ by a factor of a hundred. Do not guess."""
    payload = mcp_payload()
    args = payload["params"]["arguments"]
    del args["spend_limit_minor"]
    args["spend_limit"] = 5000
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_AMBIGUOUS_MONEY_UNITS


def test_mcp_declared_units_are_accepted_in_either_form():
    payload = mcp_payload()
    args = payload["params"]["arguments"]
    del args["spend_limit_minor"]
    args["spend_limit"] = {"currency": "INR", "value": "5000.00"}
    assert normalize(payload).scope.to_json() == EXPECTED_SCOPE.to_json()


def test_mcp_bare_item_price_is_refused():
    payload = mcp_payload()
    item = payload["params"]["arguments"]["items"][0]
    del item["unit_amount_minor"]
    item["price"] = 4000
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_AMBIGUOUS_MONEY_UNITS


def test_mcp_float_amount_is_rejected():
    payload = mcp_payload()
    payload["params"]["arguments"]["items"][0]["unit_amount_minor"] = 400000.0
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_FLOAT_MONEY


def test_mcp_non_payment_tool_raises():
    payload = mcp_payload()
    payload["params"]["name"] = "search_products"
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_UNSUPPORTED_TOOL


def test_mcp_without_an_agent_identity_raises():
    payload = mcp_payload()
    del payload["params"]["arguments"]["agent_id"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_MISSING_FIELD


def test_mcp_agent_identity_may_arrive_in_meta():
    payload = mcp_payload()
    del payload["params"]["arguments"]["agent_id"]
    payload["params"]["_meta"] = {"caveat/agent_id": "op_shopbot"}
    assert normalize(payload).holder == "op_shopbot"


def test_mcp_is_unsigned_and_says_so():
    req = normalize(mcp_payload())
    assert req.signature.state == SIG_ABSENT
    assert req.signature.verified is False
    assert req.has_warning(WARN_NO_SIGNATURE)
    assert req.has_warning(WARN_NO_EXPIRY)


def test_mcp_names_the_constraint_kinds_it_cannot_express():
    req = normalize(mcp_payload())
    gaps = [w for w in req.warnings if w.code == WARN_PROTOCOL_CANNOT_EXPRESS]
    assert gaps
    for kind in ("expires_at", "velocity_max", "cumulative_max", "geo_allow"):
        assert kind in gaps[0].detail


def test_mcp_unknown_argument_is_never_silently_dropped():
    payload = mcp_payload()
    payload["params"]["arguments"]["only_on_weekdays"] = True
    req = normalize(payload)
    unmapped = [w for w in req.warnings if w.code == WARN_UNMAPPED_FIELD]
    assert unmapped and unmapped[0].widening


def test_mcp_missing_items_raises():
    payload = mcp_payload()
    del payload["params"]["arguments"]["items"]
    with pytest.raises(AdapterError) as exc:
        normalize(payload)
    assert exc.value.code == ERR_MISSING_FIELD


def test_mcp_explicit_empty_allowlist_authorizes_nothing():
    """An empty allowlist is the maximally narrow mandate, not the unbounded one."""
    payload = {
        "tool": "checkout",
        "arguments": {
            "items": [
                {"sku": "s1", "name": "Thing", "unit_amount_minor": 100, "mcc": 5411,
                 "category": "groceries"}
            ],
            "agent_id": "op_shopbot",
            "merchant_id": "m_croma",
            "allowed_merchants": [],
        },
    }
    req = normalize(payload)
    assert MerchantAllow(()) in list(req.scope)
    assert not req.has_warning(WARN_UNBOUNDED_SCOPE)
    assert not entails(PARENT_SCOPE, req.scope).entailed
    assert entails(req.scope, PARENT_SCOPE).entailed


def test_a_mandate_expressing_no_constraints_is_flagged_unbounded():
    """An empty scope authorizes every transaction, so it must never pass quietly."""
    payload = ap2_payload()
    payload["intent_mandate"]["credentialSubject"]["intent"] = {}
    del payload["cart_mandate"]
    req = normalize(payload)
    assert len(req.scope) == 0
    unbounded = [w for w in req.warnings if w.code == WARN_UNBOUNDED_SCOPE]
    assert unbounded and unbounded[0].widening
    # And the solver refuses it anyway: nothing proves an unbounded child narrows.
    assert not entails(req.scope, PARENT_SCOPE).entailed


def test_ap2_intent_mandate_without_a_cart_yields_an_empty_intent_cart():
    payload = ap2_payload()
    del payload["cart_mandate"]
    req = normalize(payload)
    assert req.intent_cart.lines == ()
    assert req.executed_cart is None
    assert req.has_warning(WARN_NO_CART)


# ----------------------------------------------------------------------------------
# Integration: a normalized scope is a declared scope and must be proved.
# ----------------------------------------------------------------------------------


PARENT_SCOPE = ConstraintSet(
    [
        AmountMax(1_000_000),
        CategoryAllow(("groceries", "appliances")),
        MerchantAllow(("m_croma", "m_bigbasket")),
        MccAllow((5411, 5722)),
    ]
)


def test_a_normalized_request_must_still_prove_it_narrows():
    for payload in (ap2_payload(), acp_payload(), mcp_payload()):
        req = normalize(payload)
        result = entails(req.scope, PARENT_SCOPE)
        assert result.entailed, (req.protocol, result.counterexample)


def test_a_protocol_that_cannot_carry_a_constraint_gets_caught_by_the_solver():
    """The dropped-constraint escalation, arriving over a real wire format.

    ACP's published allowance cannot express "category in {groceries}". A token that only
    caps the amount therefore declares a *broader* scope than the mandate it descends from —
    fewer constraints, more authority. The subset check waves it through; Z3 does not.
    """
    payload = acp_payload()
    del payload["shared_payment_token"]["restrictions"]
    req = normalize(payload)

    assert naive_subset_check(req.scope, PARENT_SCOPE) is True
    result = entails(req.scope, PARENT_SCOPE)
    assert not result.entailed
    assert result.counterexample is not None
    assert result.counterexample.violated


def test_an_inbound_protocol_request_can_be_delegated_and_authorized():
    """One endpoint to the engine: normalize, prove the hop, then decide on the cart."""
    engine = CaveatEngine()
    engine.register_operator("op_shopbot", "ShopBot v2.1", now=T0)
    parent = engine.grant(holder="cardholder", scope=PARENT_SCOPE, now=T0)

    req = normalize(acp_payload())
    outcome = engine.delegate(
        parent=parent,
        child_holder=req.holder,
        declared_scope=req.scope,
        now=T0 + 1,
    )
    assert outcome.accepted, outcome.entailment.to_dict()
    assert outcome.mandate is not None

    decision = engine.authorize(
        mandate=outcome.mandate,
        intent_cart=req.intent_cart,
        executed_cart=req.executed_cart or req.intent_cart,
        now=T0 + 2,
    )
    assert decision.outcome == "ALLOW", decision.to_dict()


def test_an_injected_cart_arriving_over_ap2_is_denied():
    engine = CaveatEngine()
    engine.register_operator("did:example:agent:shopbot", "ShopBot", now=T0)
    parent = engine.grant(holder="cardholder", scope=PARENT_SCOPE, now=T0)

    payload = ap2_payload()
    payload["executed_cart"] = {
        "merchant": {"id": "m_croma"},
        "display_items": [
            {
                "label": "Budget espresso machine",
                "sku": "sku_espresso_01",
                "amount": {"currency": "INR", "value": "4000.00"},
                "merchant_category_code": 5722,
                "category": "appliances",
            },
            {
                "label": "Stored-value gift card",
                "sku": "sku_giftcard_1",
                "amount": {"currency": "INR", "value": "50000.00"},
                "merchant_category_code": 6540,
                "category": "stored_value",
            },
        ],
    }
    req = normalize(payload)
    outcome = engine.delegate(
        parent=parent, child_holder=req.holder, declared_scope=req.scope, now=T0 + 1
    )
    assert outcome.accepted and outcome.mandate is not None

    decision = engine.authorize(
        mandate=outcome.mandate,
        intent_cart=req.intent_cart,
        executed_cart=req.executed_cart,
        now=T0 + 2,
    )
    assert decision.outcome == "DENY"
    assert "MANDATE_CART_DIVERGENCE" in decision.reason_codes
    assert "MCC_NOT_ALLOWED" in decision.reason_codes


# ----------------------------------------------------------------------------------
# Serialization.
# ----------------------------------------------------------------------------------


def test_normalized_request_serializes_for_the_console():
    for payload in (ap2_payload(), acp_payload(), mcp_payload()):
        d = normalize(payload).to_dict()
        assert set(
            ["protocol", "scope", "intent_cart", "signature", "warnings", "lossy_widening"]
        ) <= set(d)
        assert d["signature"]["verified"] is False
        assert isinstance(d["warnings"], list)


def test_adapters_never_consult_the_clock():
    """Normalization is a pure function of its bytes, so any run replays identically.

    Enforced rather than asserted: the clock is booby-trapped for the duration of the call.
    A decision path that reads wall time cannot be replayed, and a demo that cannot be
    replayed is a demo that fails on stage.
    """

    def _boom(*_args, **_kwargs):
        raise AssertionError("an adapter read the system clock")

    for payload in (ap2_payload(), acp_payload(), mcp_payload()):
        with mock.patch("time.time", _boom), mock.patch("time.monotonic", _boom):
            first = normalize(payload).to_dict()
        second = normalize(copy.deepcopy(payload)).to_dict()
        assert first == second
