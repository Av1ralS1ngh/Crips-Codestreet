"""MCP — a raw tool call, which is what an agent framework actually emits.

LangGraph, OpenAI-Agents and Claude agents do not speak AP2 or ACP. They emit a JSON-RPC
`tools/call` at an MCP server:

    {
      "jsonrpc": "2.0", "id": 42, "method": "tools/call",
      "params": {
        "name": "checkout",
        "arguments": {
          "merchant_id": "m_bigbasket",
          "currency": "INR",
          "spend_limit_minor": 500000,
          "allowed_mcc": [5411],
          "allowed_categories": ["groceries"],
          "items": [
            {"sku": "sku_milk", "name": "Milk 1L", "unit_amount_minor": 6000,
             "quantity": 2, "mcc": 5411, "category": "groceries"}
          ],
          "agent_id": "op_shopbot"
        }
      }
    }

This is the weakest of the three formats and it is the one most agents will use. MCP has no
payment vocabulary at all: no signature, no issuer, no expiry, no delegation depth, no
standard money encoding. A tool call carries *zero* authority of its own — it is a request,
and every constraint that binds it has to come from the mandate it is evaluated against.
The adapter says so out loud with an ADAPTER_NO_SIGNATURE warning marked widening.

Two deliberate refusals, both about units:

  * A bare `"spend_limit": 5000` is rejected, not guessed. Five thousand rupees and five
    thousand paise differ by a factor of a hundred, and an agent-authored JSON blob is the
    last place to infer which was meant. Send `spend_limit_minor` (integer paise) or
    `{"currency": "INR", "value": "5000.00"}`.
  * A JSON float anywhere in a money field is rejected outright.

Accepted shapes: a JSON-RPC envelope as above, `{"params": {...}}`, or the flattened
`{"tool": "checkout", "arguments": {...}}` form that most frameworks log.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..cart import Cart, CartLine
from ..constraints import (
    AmountMax,
    CategoryAllow,
    ConstraintSet,
    MccAllow,
    MerchantAllow,
)
from .base import (
    ERR_AMBIGUOUS_MONEY_UNITS,
    ERR_INVALID_FIELD,
    ERR_MISSING_FIELD,
    ERR_UNSUPPORTED_TOOL,
    PROTOCOL_MCP,
    UNKNOWN_CATEGORY,
    UNKNOWN_MCC,
    WARN_MISSING_LINE_DETAIL,
    WARN_NO_SIGNATURE,
    NO_SIGNATURE,
    NormalizedRequest,
    ScopeBuilder,
    WarningLog,
    fail,
    finalize_cart,
    get_any,
    normalize_currency,
    parse_allowlist,
    parse_amount_object,
    parse_int,
    parse_mcc_list,
    parse_minor_units,
    report_scope_health,
    report_spec_gaps,
    require_mapping,
    require_sequence,
    unmapped_keys,
)

PROTOCOL = PROTOCOL_MCP

TOOLS_CALL_METHOD = "tools/call"

# Tool names this adapter will normalize into a payment request. Anything else is a tool we
# have no business turning into a charge, and it raises rather than being coerced.
PAYMENT_TOOLS = frozenset({"checkout", "purchase", "pay", "place_order", "create_order", "buy"})

_KNOWN_ARGUMENT_KEYS = {
    "merchant_id",
    "merchantId",
    "merchant",
    "currency",
    "spend_limit_minor",
    "spendLimitMinor",
    "spend_limit",
    "spendLimit",
    "max_amount_minor",
    "maxAmountMinor",
    "max_amount",
    "maxAmount",
    "allowed_mcc",
    "allowedMcc",
    "mcc_allowlist",
    "allowed_categories",
    "allowedCategories",
    "categories",
    "allowed_merchants",
    "allowedMerchants",
    "items",
    "line_items",
    "lineItems",
    "cart",
    "executed_items",
    "executedItems",
    "agent_id",
    "agentId",
    "agent",
    "session",
    "idempotency_key",
    "idempotencyKey",
    "notes",
}


# --------------------------------------------------------------------------------------
# Sniffing
# --------------------------------------------------------------------------------------


def sniff(payload: Mapping[str, Any]) -> bool:
    if str(payload.get("method", "")) == TOOLS_CALL_METHOD:
        return True
    params = payload.get("params")
    if isinstance(params, Mapping) and "arguments" in params and "name" in params:
        return True
    if "arguments" in payload and (
        isinstance(payload.get("tool"), str) or isinstance(payload.get("name"), str)
    ):
        return True
    return False


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def parse(payload: Mapping[str, Any]) -> NormalizedRequest:
    warnings = WarningLog()
    call, call_path = _tool_call(payload)

    raw_name = get_any(call, "name", "tool", "tool_name", default=None)
    if not isinstance(raw_name, str) or not raw_name.strip():
        fail(
            ERR_MISSING_FIELD,
            "tool call names no tool",
            protocol=PROTOCOL,
            path=f"{call_path}.name",
        )
    tool = raw_name.strip()
    if tool.lower() not in PAYMENT_TOOLS:
        fail(
            ERR_UNSUPPORTED_TOOL,
            f"{tool!r} is not a payment tool this adapter knows how to normalize "
            f"(expected one of {sorted(PAYMENT_TOOLS)}); refusing to invent a charge from it",
            protocol=PROTOCOL,
            path=f"{call_path}.name",
        )

    args = require_mapping(
        get_any(call, "arguments", "args", "input", default=None),
        protocol=PROTOCOL,
        path=f"{call_path}.arguments",
    )
    args_path = f"{call_path}.arguments"

    currency = normalize_currency(
        get_any(args, "currency", default="INR"), protocol=PROTOCOL, path=f"{args_path}.currency"
    )
    merchant = _merchant_id(args, path=args_path)

    scope = _scope_from_arguments(args, currency=currency, merchant=merchant, path=args_path, warnings=warnings)

    intent_cart = _cart_from_items(
        _items(args, "items", "line_items", "lineItems", path=args_path),
        merchant=merchant,
        currency=currency,
        path=f"{args_path}.items",
        warnings=warnings,
    )

    executed_cart = None
    raw_executed = get_any(args, "executed_items", "executedItems", default=None)
    if raw_executed is not None:
        executed_cart = _cart_from_items(
            require_sequence(raw_executed, protocol=PROTOCOL, path=f"{args_path}.executed_items"),
            merchant=merchant,
            currency=currency,
            path=f"{args_path}.executed_items",
            warnings=warnings,
        )

    report_spec_gaps(PROTOCOL, scope, warnings)
    report_scope_health(scope, warnings)

    # Not marked widening: authenticity is a different axis from scope, and it is reported
    # on `signature`. There is no source credential here whose scope we could have widened —
    # the tool call asserts no authority at all, so everything that binds it comes from the
    # mandate it is evaluated against.
    warnings.add(
        WARN_NO_SIGNATURE,
        "an MCP tool call is unsigned and unauthenticated at the protocol layer. It carries no "
        "authority of its own; only the mandate it is evaluated against constrains it.",
        path=call_path,
    )

    return NormalizedRequest(
        protocol=PROTOCOL,
        scope=scope,
        intent_cart=intent_cart,
        executed_cart=executed_cart,
        holder=_agent_id(payload, call, args, args_path=args_path),
        signature=NO_SIGNATURE,
        warnings=warnings.build(),
        credential_id=None,
        issuer=None,
        metadata={
            "tool": tool,
            "jsonrpc_id": payload.get("id"),
            "idempotency_key": get_any(args, "idempotency_key", "idempotencyKey", default=None),
        },
    )


def _tool_call(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    method = payload.get("method")
    if isinstance(method, str) and method != TOOLS_CALL_METHOD:
        fail(
            ERR_UNSUPPORTED_TOOL,
            f"MCP method {method!r} is not {TOOLS_CALL_METHOD!r}",
            protocol=PROTOCOL,
            path="$.method",
        )
    params = payload.get("params")
    if isinstance(params, Mapping):
        return params, "$.params"
    return payload, "$"


# --------------------------------------------------------------------------------------
# Arguments -> ConstraintSet
# --------------------------------------------------------------------------------------


def _scope_from_arguments(
    args: Mapping[str, Any],
    *,
    currency: str,
    merchant: str,
    path: str,
    warnings: WarningLog,
) -> ConstraintSet:
    scope = ScopeBuilder()

    limit = _spend_limit(args, currency=currency, path=path)
    if limit is not None:
        scope.add(AmountMax(limit))

    merchants = parse_allowlist(
        get_any(args, "allowed_merchants", "allowedMerchants", default=None),
        protocol=PROTOCOL,
        path=f"{path}.allowed_merchants",
        warnings=warnings,
    )
    if merchants is None and merchant:
        # The call names exactly one merchant, so bounding the declared scope to it is a
        # narrowing, never a widening. Without this an MCP tool call would declare no
        # merchant limit at all and be rejected by every merchant-scoped parent mandate.
        merchants = (merchant,)
    if merchants is not None:
        scope.add(MerchantAllow(merchants))

    mccs = parse_mcc_list(
        get_any(args, "allowed_mcc", "allowedMcc", "mcc_allowlist", default=None),
        protocol=PROTOCOL,
        path=f"{path}.allowed_mcc",
        warnings=warnings,
    )
    if mccs is not None:
        scope.add(MccAllow(mccs))

    categories = parse_allowlist(
        get_any(args, "allowed_categories", "allowedCategories", "categories", default=None),
        protocol=PROTOCOL,
        path=f"{path}.allowed_categories",
        warnings=warnings,
    )
    if categories is not None:
        scope.add(CategoryAllow(categories))

    unmapped_keys(args, _KNOWN_ARGUMENT_KEYS, protocol=PROTOCOL, path=path, warnings=warnings)
    return scope.build()


def _spend_limit(args: Mapping[str, Any], *, currency: str, path: str) -> int | None:
    """Read a spend limit only when the payload states its units unambiguously."""
    explicit = get_any(
        args,
        "spend_limit_minor",
        "spendLimitMinor",
        "max_amount_minor",
        "maxAmountMinor",
        default=None,
    )
    if explicit is not None:
        return parse_minor_units(explicit, protocol=PROTOCOL, path=f"{path}.spend_limit_minor")

    ambiguous = get_any(args, "spend_limit", "spendLimit", "max_amount", "maxAmount", default=None)
    if ambiguous is None:
        return None
    if isinstance(ambiguous, Mapping):
        minor, _currency = parse_amount_object(
            ambiguous, protocol=PROTOCOL, path=f"{path}.spend_limit", default_currency=currency
        )
        return minor
    fail(
        ERR_AMBIGUOUS_MONEY_UNITS,
        f"{path}.spend_limit is a bare number ({ambiguous!r}) with no declared units. "
        "Use `spend_limit_minor` for integer minor units, or "
        '`{"currency": "INR", "value": "5000.00"}`. This layer will not guess a factor of 100.',
        protocol=PROTOCOL,
        path=f"{path}.spend_limit",
    )


def _merchant_id(args: Mapping[str, Any], *, path: str) -> str:
    merchant = get_any(args, "merchant", default=None)
    if isinstance(merchant, Mapping):
        ident = get_any(merchant, "id", "merchant_id", "name", default=None)
        if isinstance(ident, str) and ident.strip():
            return ident.strip()
    elif isinstance(merchant, str) and merchant.strip():
        return merchant.strip()
    for key in ("merchant_id", "merchantId"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    fail(
        ERR_MISSING_FIELD,
        "the tool call names no merchant",
        protocol=PROTOCOL,
        path=f"{path}.merchant_id",
    )


def _agent_id(
    payload: Mapping[str, Any],
    call: Mapping[str, Any],
    args: Mapping[str, Any],
    *,
    args_path: str,
) -> str:
    for source in (args, call, payload):
        for key in ("agent_id", "agentId"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        agent = source.get("agent")
        if isinstance(agent, Mapping):
            ident = get_any(agent, "id", "agent_id", "name", default=None)
            if isinstance(ident, str) and ident.strip():
                return ident.strip()
        elif isinstance(agent, str) and agent.strip():
            return agent.strip()
        for container_key in ("session", "_meta", "meta", "context"):
            container = source.get(container_key)
            if isinstance(container, Mapping):
                for key in ("agent_id", "agentId", "caveat/agent_id", "agent"):
                    value = container.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    fail(
        ERR_MISSING_FIELD,
        "no agent identity anywhere in the tool call. MCP does not carry one by default, so "
        "the transport must attach it (arguments.agent_id or _meta) before a charge can be "
        "attributed to an operator",
        protocol=PROTOCOL,
        path=f"{args_path}.agent_id",
    )


# --------------------------------------------------------------------------------------
# Arguments -> Cart
# --------------------------------------------------------------------------------------


def _items(args: Mapping[str, Any], *keys: str, path: str) -> Any:
    raw = get_any(args, *keys, default=None)
    if raw is None:
        cart = get_any(args, "cart", default=None)
        if isinstance(cart, Mapping):
            raw = get_any(cart, "items", "line_items", "lineItems", default=None)
    if raw is None:
        fail(
            ERR_MISSING_FIELD,
            "the tool call carries no items; there is no cart to authorize",
            protocol=PROTOCOL,
            path=f"{path}.items",
        )
    return require_sequence(raw, protocol=PROTOCOL, path=f"{path}.items")


def _cart_from_items(
    items: Any, *, merchant: str, currency: str, path: str, warnings: WarningLog
) -> Cart:
    lines = [
        _parse_item(
            require_mapping(raw, protocol=PROTOCOL, path=f"{path}[{i}]"),
            currency=currency,
            path=f"{path}[{i}]",
            warnings=warnings,
        )
        for i, raw in enumerate(items)
    ]
    return finalize_cart(
        merchant=merchant,
        currency=currency,
        lines=lines,
        protocol=PROTOCOL,
        path=path,
        warnings=warnings,
    )


def _parse_item(
    item: Mapping[str, Any], *, currency: str, path: str, warnings: WarningLog
) -> CartLine:
    name = get_any(item, "name", "description", "title", "label", default=None)
    if not isinstance(name, str) or not name.strip():
        fail(ERR_INVALID_FIELD, "item has no name", protocol=PROTOCOL, path=f"{path}.name")
    name = name.strip()

    qty = parse_int(get_any(item, "quantity", "qty", default=1), protocol=PROTOCOL, path=f"{path}.quantity")
    if qty < 1:
        fail(
            ERR_INVALID_FIELD,
            f"quantity must be a positive integer, got {qty}",
            protocol=PROTOCOL,
            path=f"{path}.quantity",
        )

    amount = _line_amount(item, currency=currency, qty=qty, path=path)

    sku = get_any(item, "sku", "id", "product_id", "productId", default=None)
    if not isinstance(sku, str) or not sku.strip():
        sku = f"label:{name}"
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"item {name!r} carries no SKU; the intent-versus-execution diff will key on its "
            "name instead",
            path=f"{path}.sku",
        )
    else:
        sku = sku.strip()

    mcc_raw = get_any(item, "mcc", "merchant_category_code", "merchantCategoryCode", default=None)
    if mcc_raw is None:
        mcc = UNKNOWN_MCC
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"item {name!r} carries no merchant category code; it is scored as MCC 0, which "
            "fails closed against any MCC allowlist",
            path=f"{path}.mcc",
        )
    else:
        mcc = parse_int(mcc_raw, protocol=PROTOCOL, path=f"{path}.mcc")

    category = get_any(item, "category", "product_category", default=None)
    if not isinstance(category, str) or not category.strip():
        category = UNKNOWN_CATEGORY
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"item {name!r} carries no category; it fails closed against any category allowlist",
            path=f"{path}.category",
        )
    else:
        category = category.strip()

    return CartLine(sku=sku, description=name, amount=amount, mcc=mcc, category=category, qty=qty)


def _line_amount(item: Mapping[str, Any], *, currency: str, qty: int, path: str) -> int:
    unit_minor = get_any(item, "unit_amount_minor", "unitAmountMinor", default=None)
    if unit_minor is not None:
        return parse_minor_units(unit_minor, protocol=PROTOCOL, path=f"{path}.unit_amount_minor") * qty

    total_minor = get_any(item, "amount_minor", "amountMinor", "total_minor", "totalMinor", default=None)
    if total_minor is not None:
        return parse_minor_units(total_minor, protocol=PROTOCOL, path=f"{path}.amount_minor")

    for key in ("unit_amount", "unitAmount", "price", "amount", "total"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            minor, _currency = parse_amount_object(
                value, protocol=PROTOCOL, path=f"{path}.{key}", default_currency=currency
            )
            return minor * qty if key in ("unit_amount", "unitAmount", "price") else minor
        fail(
            ERR_AMBIGUOUS_MONEY_UNITS,
            f"{path}.{key} is a bare number ({value!r}) with no declared units. Use "
            f'`unit_amount_minor` for integer minor units, or `{{"currency": "{currency}", '
            '"value": "60.00"}`.',
            protocol=PROTOCOL,
            path=f"{path}.{key}",
        )
    fail(
        ERR_MISSING_FIELD,
        "item carries no amount",
        protocol=PROTOCOL,
        path=f"{path}.unit_amount_minor",
    )
