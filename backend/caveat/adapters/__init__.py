"""One endpoint, three wire formats, one canonical mandate object.

Six protocols are competing to carry agent payment consent in 2026 — AP2 (Google, donated
to FIDO in April 2026, Amex a partner), ACP (OpenAI/Stripe), TAP (Visa), UCP, MCP and A2A —
and every one of them carries a *different* consent object. Amex sits inside AP2, ACP,
EMVCo and FIDO simultaneously, so somebody has to be the translator. Three of the six are
implemented here:

    ap2.py   Intent Mandate / Cart Mandate as W3C Verifiable Credentials (JSON-LD)
    acp.py   Shared Payment Token: a delegated allowance with a merchant scope and expiry
    mcp.py   a raw `tools/call` — what a LangGraph / OpenAI-Agents / Claude agent emits

`normalize(payload)` sniffs the format and returns a `NormalizedRequest`: a `ConstraintSet`
and a `Cart`, the same two objects the entailment checker and the PDP already consume.
Nothing downstream knows or cares which protocol arrived.

**The translation is where authority leaks, so the translation is instrumented.** Every
protocol can express restrictions our DSL cannot hold — AP2 SKU allowlists and
refundability requirements, ACP checkout-session bindings, denylists of any shape — and
every one of those becomes a `TranslationWarning` with `widening=True`, meaning the
canonical scope authorizes transactions the source credential did not. Callers must gate on
`request.lossy_widening`. A constraint that disappears quietly during a format conversion
is exactly the escalation this project exists to stop; here it cannot disappear quietly.

What a normalized request does *not* do is authorize anything. `scope` is a **declared**
scope in the sense entailment.py means it — the credential chain did not cross the protocol
boundary — so it must be proved to entail the parent mandate before it authorizes a rupee:

    req = normalize(inbound_json)
    if req.lossy_widening:
        ...  # refuse, or route to a human; do not auto-accept
    outcome = engine.delegate(
        parent=parent_mandate,
        child_holder=req.holder,
        declared_scope=req.scope,
        now=now,
    )
    if outcome.accepted:
        decision = engine.authorize(
            mandate=outcome.mandate,
            intent_cart=req.intent_cart,
            executed_cart=req.executed_cart or req.intent_cart,
            now=now,
        )
"""

from __future__ import annotations

from typing import Any, Mapping

from . import acp, ap2, mcp
from .base import (
    ALL_CONSTRAINT_KINDS,
    ERR_AMBIGUOUS_MONEY_UNITS,
    ERR_AMBIGUOUS_PROTOCOL,
    ERR_CURRENCY_CONFLICT,
    ERR_FLOAT_MONEY,
    ERR_INVALID_CREDENTIAL,
    ERR_INVALID_FIELD,
    ERR_MISSING_FIELD,
    ERR_TOTAL_MISMATCH,
    ERR_UNKNOWN_PROTOCOL,
    ERR_UNSUPPORTED_TOOL,
    NO_SIGNATURE,
    PROTOCOL_ACP,
    PROTOCOL_AP2,
    PROTOCOL_MCP,
    PROTOCOL_SPEC_GAPS,
    SIG_ABSENT,
    SIG_PRESENT_UNVERIFIED,
    SIGNATURE_VERIFICATION_IMPLEMENTED,
    WARN_APPROXIMATED_CONSTRAINT,
    WARN_MISSING_LINE_DETAIL,
    WARN_NO_AMOUNT_CAP,
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
    NormalizedRequest,
    SignatureStatus,
    TranslationWarning,
    fail,
)

__all__ = [
    "normalize",
    "sniff",
    "SUPPORTED_PROTOCOLS",
    "AdapterError",
    "NormalizedRequest",
    "SignatureStatus",
    "TranslationWarning",
    "PROTOCOL_AP2",
    "PROTOCOL_ACP",
    "PROTOCOL_MCP",
    "PROTOCOL_SPEC_GAPS",
    "ALL_CONSTRAINT_KINDS",
    "SIG_ABSENT",
    "SIG_PRESENT_UNVERIFIED",
    "SIGNATURE_VERIFICATION_IMPLEMENTED",
    "NO_SIGNATURE",
    "ERR_AMBIGUOUS_MONEY_UNITS",
    "ERR_AMBIGUOUS_PROTOCOL",
    "ERR_CURRENCY_CONFLICT",
    "ERR_FLOAT_MONEY",
    "ERR_INVALID_CREDENTIAL",
    "ERR_INVALID_FIELD",
    "ERR_MISSING_FIELD",
    "ERR_TOTAL_MISMATCH",
    "ERR_UNKNOWN_PROTOCOL",
    "ERR_UNSUPPORTED_TOOL",
    "WARN_APPROXIMATED_CONSTRAINT",
    "WARN_MISSING_LINE_DETAIL",
    "WARN_NO_AMOUNT_CAP",
    "WARN_NO_CART",
    "WARN_NO_EXPIRY",
    "WARN_NO_SIGNATURE",
    "WARN_PROTOCOL_CANNOT_EXPRESS",
    "WARN_SIGNATURE_NOT_VERIFIED",
    "WARN_UNBOUNDED_SCOPE",
    "WARN_UNENFORCEABLE_INTENT",
    "WARN_UNMAPPED_FIELD",
    "WARN_UNREPRESENTABLE_CONSTRAINT",
]

_ADAPTERS = (ap2, acp, mcp)
_BY_NAME = {
    PROTOCOL_AP2: ap2,
    PROTOCOL_ACP: acp,
    PROTOCOL_MCP: mcp,
}

SUPPORTED_PROTOCOLS: tuple[str, ...] = (PROTOCOL_AP2, PROTOCOL_ACP, PROTOCOL_MCP)

# Aliases a caller might reasonably put in an explicit `protocol` field.
_ALIASES = {
    "ap2": PROTOCOL_AP2,
    "agent-payments-protocol": PROTOCOL_AP2,
    "agent_payments_protocol": PROTOCOL_AP2,
    "w3c-vc": PROTOCOL_AP2,
    "acp": PROTOCOL_ACP,
    "agentic-commerce-protocol": PROTOCOL_ACP,
    "agentic_commerce_protocol": PROTOCOL_ACP,
    "stripe-acp": PROTOCOL_ACP,
    "mcp": PROTOCOL_MCP,
    "model-context-protocol": PROTOCOL_MCP,
    "model_context_protocol": PROTOCOL_MCP,
}


def sniff(payload: Mapping[str, Any]) -> str | None:
    """Name the protocol a payload is in, or None if nothing matches.

    Raises on an *ambiguous* payload rather than picking a winner. A blob that reads as two
    protocols at once is not a formatting quirk — it is the shape a confused-deputy attack
    takes at a multi-protocol endpoint, and the right answer is to refuse it.
    """
    if not isinstance(payload, Mapping):
        fail(
            ERR_INVALID_FIELD,
            f"payload must be a JSON object, got {type(payload).__name__}",
            path="$",
        )

    declared = payload.get("protocol") or payload.get("_protocol")
    if declared is not None:
        key = str(declared).strip().lower()
        resolved = _ALIASES.get(key)
        if resolved is None:
            fail(
                ERR_UNKNOWN_PROTOCOL,
                f"{declared!r} is not a protocol this endpoint speaks "
                f"(supported: {list(SUPPORTED_PROTOCOLS)})",
                path="$.protocol",
            )
        return resolved

    matches = [module.PROTOCOL for module in _ADAPTERS if module.sniff(payload)]
    if len(matches) > 1:
        fail(
            ERR_AMBIGUOUS_PROTOCOL,
            f"payload matches more than one protocol ({matches}); set an explicit "
            "`protocol` field rather than letting the endpoint guess",
            path="$",
        )
    return matches[0] if matches else None


def normalize(payload: Mapping[str, Any]) -> NormalizedRequest:
    """Sniff the wire format and translate it into the canonical mandate objects."""
    protocol = sniff(payload)
    if protocol is None:
        fail(
            ERR_UNKNOWN_PROTOCOL,
            "no adapter recognised this payload "
            f"(top-level keys: {sorted(str(k) for k in payload)[:12]}); "
            f"supported: {list(SUPPORTED_PROTOCOLS)}",
            path="$",
        )
    return _BY_NAME[protocol].parse(payload)
