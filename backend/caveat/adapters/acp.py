"""ACP — the Agentic Commerce Protocol (OpenAI + Stripe), delegated-payment side.

An ACP agent does not hold a card. It asks the issuer/PSP to mint a **Shared Payment
Token**: a delegated credential carrying an `allowance` — a spend cap, a currency, a
merchant, an expiry, and the checkout session it was minted for. The merchant then charges
against that token.

The shape here follows Stripe's delegated-payment request/response:

    {
      "shared_payment_token": {
        "id": "spt_...",
        "payment_method": {"type": "card", "card_number_type": "network_token"},
        "allowance": {
          "reason": "one_time",            # or "recurring"
          "max_amount": 400000,            # INTEGER MINOR UNITS
          "currency": "inr",
          "merchant_id": "m_croma",
          "checkout_session_id": "cs_...",
          "expires_at": "2026-07-27T10:00:00Z"
        },
        "agent": {"id": "op_shopbot"}
      },
      "checkout_session": { ... line items ... }
    }

Two things about ACP that matter to a governance layer:

1. **Amounts are integer minor units already.** No decimal strings, no rounding. That is
   the one place ACP is friendlier than AP2, and this adapter keeps it that way.
2. **The allowance is thin.** The published allowance object cannot express a merchant
   category allowlist, a product-category allowlist, a geography, a velocity limit or a
   not-before. A cardholder mandate that says *groceries only* has no ACP encoding at all.
   That is not a bug in this adapter — it is the reason a translator has to exist, and the
   reason the entailment check must run on the result rather than a subset test. Those
   gaps are reported as ADAPTER_PROTOCOL_CANNOT_EXPRESS.

An optional `restrictions` block is read if a deployment supplies one. It is **not part of
the published ACP spec**; it is a forward-compatible extension point, and anything read
from it is treated exactly like a spec field once parsed.

Unknown-key scanning covers `allowance` and `restrictions` only — the two constraint-bearing
objects. Keys elsewhere on the token (payment instrument, billing address, risk signals)
are payment facts rather than policy, and warning on them would bury the warnings that
matter.

Signature: a Shared Payment Token is an opaque bearer credential minted by the PSP. There
is nothing in the payload this process can verify offline, so `signature.verified` is False
and the state is PRESENT_UNVERIFIED. Trust in the token is trust in the PSP that issued it.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..cart import Cart, CartLine
from ..constraints import (
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
from .base import (
    ERR_INVALID_FIELD,
    ERR_MISSING_FIELD,
    PROTOCOL_ACP,
    SIG_PRESENT_UNVERIFIED,
    UNKNOWN_CATEGORY,
    UNKNOWN_MCC,
    WARN_APPROXIMATED_CONSTRAINT,
    WARN_MISSING_LINE_DETAIL,
    WARN_SIGNATURE_NOT_VERIFIED,
    WARN_UNREPRESENTABLE_CONSTRAINT,
    NormalizedRequest,
    ScopeBuilder,
    SignatureStatus,
    WarningLog,
    fail,
    finalize_cart,
    get_any,
    has_any,
    normalize_currency,
    parse_allowlist,
    parse_int,
    parse_mcc_list,
    parse_minor_units,
    parse_timestamp,
    report_scope_health,
    report_spec_gaps,
    require_mapping,
    require_sequence,
    unmapped_keys,
)

PROTOCOL = PROTOCOL_ACP

TOKEN_KEYS = (
    "shared_payment_token",
    "sharedPaymentToken",
    "delegated_payment_token",
    "delegatedPaymentToken",
    "delegated_payment",
)

REASON_ONE_TIME = "one_time"
REASON_RECURRING = "recurring"

_ALLOWANCE_KEYS = {
    "reason",
    "max_amount",
    "maxAmount",
    "currency",
    "merchant_id",
    "merchantId",
    "merchant",
    "checkout_session_id",
    "checkoutSessionId",
    "expires_at",
    "expiresAt",
    "expiry",
}

_RESTRICTION_KEYS = {
    "allowed_mcc",
    "allowedMcc",
    "merchant_category_codes",
    "allowed_categories",
    "allowedCategories",
    "categories",
    "allowed_countries",
    "allowedCountries",
    "countries",
    "cumulative_max_amount",
    "cumulativeMaxAmount",
    "max_transactions",
    "maxTransactions",
    "step_up_over_amount",
    "stepUpOverAmount",
    "allowed_merchants",
    "allowedMerchants",
}


# --------------------------------------------------------------------------------------
# Sniffing
# --------------------------------------------------------------------------------------


def sniff(payload: Mapping[str, Any]) -> bool:
    if has_any(payload, *TOKEN_KEYS):
        return True
    if "allowance" in payload and has_any(
        payload, "payment_method", "paymentMethod", "checkout_session", "checkoutSession"
    ):
        return True
    return False


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def parse(payload: Mapping[str, Any]) -> NormalizedRequest:
    warnings = WarningLog()

    token = get_any(payload, *TOKEN_KEYS, default=None)
    if token is None:
        if "allowance" not in payload:
            fail(
                ERR_MISSING_FIELD,
                "no shared_payment_token and no allowance; nothing delegates authority here",
                protocol=PROTOCOL,
                path="$",
            )
        token = payload
        token_path = "$"
    else:
        token_path = "$.shared_payment_token"
    token = require_mapping(token, protocol=PROTOCOL, path=token_path)

    allowance = require_mapping(
        get_any(token, "allowance", default=None),
        protocol=PROTOCOL,
        path=f"{token_path}.allowance",
    )

    currency = normalize_currency(
        get_any(allowance, "currency", default=None),
        protocol=PROTOCOL,
        path=f"{token_path}.allowance.currency",
    )

    scope, session_id = _scope_from_allowance(
        allowance, token, currency=currency, token_path=token_path, warnings=warnings
    )

    session = get_any(payload, "checkout_session", "checkoutSession", default=None)
    if session is None:
        session = get_any(token, "checkout_session", "checkoutSession", default=None)

    if session is not None:
        intent_cart = _cart_from_session(
            require_mapping(session, protocol=PROTOCOL, path="$.checkout_session"),
            path="$.checkout_session",
            fallback_currency=currency,
            fallback_merchant=_merchant_id(allowance),
            warnings=warnings,
        )
    else:
        intent_cart = finalize_cart(
            merchant=_merchant_id(allowance) or "",
            currency=currency,
            lines=(),
            protocol=PROTOCOL,
            path="$.checkout_session",
            warnings=warnings,
        )

    executed_raw = get_any(payload, "executed_order", "executedOrder", "order", default=None)
    executed_cart = None
    if executed_raw is not None:
        executed_cart = _cart_from_session(
            require_mapping(executed_raw, protocol=PROTOCOL, path="$.executed_order"),
            path="$.executed_order",
            fallback_currency=currency,
            fallback_merchant=_merchant_id(allowance),
            warnings=warnings,
        )

    report_spec_gaps(PROTOCOL, scope, warnings)
    report_scope_health(scope, warnings)

    token_id = get_any(token, "id", "token", default=None)
    signature = SignatureStatus(
        state=SIG_PRESENT_UNVERIFIED,
        detail=(
            "Shared Payment Token is an opaque bearer credential minted by the PSP; nothing "
            "in this payload is verifiable offline by this process"
        ),
        algorithm="opaque-bearer-token",
        key_reference=str(token_id) if token_id else None,
    )
    warnings.add(
        WARN_SIGNATURE_NOT_VERIFIED,
        "the delegated token was accepted on its face. Authenticity is the issuing PSP's "
        "assertion, not a check this build performed.",
        path=f"{token_path}.id",
    )

    return NormalizedRequest(
        protocol=PROTOCOL,
        scope=scope,
        intent_cart=intent_cart,
        executed_cart=executed_cart,
        holder=_agent_id(token, payload, token_path=token_path),
        signature=signature,
        warnings=warnings.build(),
        credential_id=str(token_id) if token_id else None,
        issuer=_issuer(token),
        metadata={
            "checkout_session_id": session_id,
            "allowance_reason": get_any(allowance, "reason", default=None),
            "currency": currency,
        },
    )


# --------------------------------------------------------------------------------------
# Allowance -> ConstraintSet
# --------------------------------------------------------------------------------------


def _scope_from_allowance(
    allowance: Mapping[str, Any],
    token: Mapping[str, Any],
    *,
    currency: str,
    token_path: str,
    warnings: WarningLog,
) -> tuple[ConstraintSet, str | None]:
    scope = ScopeBuilder()
    path = f"{token_path}.allowance"

    raw_max = get_any(allowance, "max_amount", "maxAmount", default=None)
    if raw_max is None:
        fail(
            ERR_MISSING_FIELD,
            "allowance.max_amount is required; an allowance without a cap is not an allowance",
            protocol=PROTOCOL,
            path=f"{path}.max_amount",
        )
    max_amount = parse_minor_units(raw_max, protocol=PROTOCOL, path=f"{path}.max_amount")
    if max_amount <= 0:
        fail(
            ERR_INVALID_FIELD,
            f"allowance.max_amount must be positive, got {max_amount}",
            protocol=PROTOCOL,
            path=f"{path}.max_amount",
        )

    reason = str(get_any(allowance, "reason", default=REASON_ONE_TIME)).strip().lower()
    if reason == REASON_RECURRING:
        # ACP's recurring allowance is per period; the DSL's cumulative cap has no period.
        # Modelling it as a lifetime cap is strictly tighter over more than one period, so
        # the approximation errs in the safe direction.
        scope.add(AmountMax(max_amount))
        scope.add(CumulativeMax(max_amount))
        warnings.add(
            WARN_APPROXIMATED_CONSTRAINT,
            "recurring allowance modelled as a lifetime cumulative cap; the canonical DSL has "
            "no billing period, so this is tighter than the token across multiple periods and "
            "identical within one",
            path=f"{path}.reason",
        )
    else:
        scope.add(AmountMax(max_amount))

    merchant = _merchant_id(allowance)
    if merchant:
        scope.add(MerchantAllow((merchant,)))

    expiry = get_any(allowance, "expires_at", "expiresAt", "expiry", default=None)
    if expiry is not None:
        scope.add(ExpiresAt(parse_timestamp(expiry, protocol=PROTOCOL, path=f"{path}.expires_at")))

    session_id = get_any(allowance, "checkout_session_id", "checkoutSessionId", default=None)
    if isinstance(session_id, str) and session_id.strip():
        session_id = session_id.strip()
        # The single most important loss in the ACP translation. The token is good for one
        # checkout session; the canonical DSL has no session predicate, so the translated
        # scope authorizes any transaction inside the limits. The session id is carried in
        # metadata so a caller can re-bind it — via the intent cart hash the PDP already
        # pins — instead of losing it.
        warnings.add(
            WARN_UNREPRESENTABLE_CONSTRAINT,
            f"the allowance is bound to checkout session {session_id!r}; the canonical DSL has "
            "no session predicate, so that binding was not applied. Re-bind it with the intent "
            "cart hash before authorizing.",
            path=f"{path}.checkout_session_id",
            widening=True,
        )
    else:
        session_id = None

    unmapped_keys(allowance, _ALLOWANCE_KEYS, protocol=PROTOCOL, path=path, warnings=warnings)

    restrictions = get_any(token, "restrictions", "constraints", default=None)
    if restrictions is not None:
        _apply_restrictions(
            require_mapping(restrictions, protocol=PROTOCOL, path=f"{token_path}.restrictions"),
            scope,
            path=f"{token_path}.restrictions",
            warnings=warnings,
        )

    return scope.build(), session_id


def _apply_restrictions(
    restrictions: Mapping[str, Any], scope: ScopeBuilder, *, path: str, warnings: WarningLog
) -> None:
    merchants = parse_allowlist(
        get_any(restrictions, "allowed_merchants", "allowedMerchants", default=None),
        protocol=PROTOCOL,
        path=f"{path}.allowed_merchants",
        warnings=warnings,
    )
    if merchants is not None:
        scope.add(MerchantAllow(merchants))

    mccs = parse_mcc_list(
        get_any(
            restrictions, "allowed_mcc", "allowedMcc", "merchant_category_codes", default=None
        ),
        protocol=PROTOCOL,
        path=f"{path}.allowed_mcc",
        warnings=warnings,
    )
    if mccs is not None:
        scope.add(MccAllow(mccs))

    categories = parse_allowlist(
        get_any(restrictions, "allowed_categories", "allowedCategories", "categories", default=None),
        protocol=PROTOCOL,
        path=f"{path}.allowed_categories",
        warnings=warnings,
    )
    if categories is not None:
        scope.add(CategoryAllow(categories))

    countries = parse_allowlist(
        get_any(restrictions, "allowed_countries", "allowedCountries", "countries", default=None),
        protocol=PROTOCOL,
        path=f"{path}.allowed_countries",
        warnings=warnings,
    )
    if countries is not None:
        scope.add(GeoAllow(countries))

    cumulative = get_any(
        restrictions, "cumulative_max_amount", "cumulativeMaxAmount", default=None
    )
    if cumulative is not None:
        scope.add(
            CumulativeMax(
                parse_minor_units(
                    cumulative, protocol=PROTOCOL, path=f"{path}.cumulative_max_amount"
                )
            )
        )

    step_up = get_any(restrictions, "step_up_over_amount", "stepUpOverAmount", default=None)
    if step_up is not None:
        scope.add(
            StepUpOver(
                parse_minor_units(
                    step_up, protocol=PROTOCOL, path=f"{path}.step_up_over_amount"
                )
            )
        )

    velocity = get_any(restrictions, "max_transactions", "maxTransactions", default=None)
    if velocity is not None:
        velocity = require_mapping(
            velocity, protocol=PROTOCOL, path=f"{path}.max_transactions"
        )
        count = parse_int(
            get_any(velocity, "count", default=None),
            protocol=PROTOCOL,
            path=f"{path}.max_transactions.count",
        )
        window = parse_int(
            get_any(velocity, "window_seconds", "windowSeconds", default=None),
            protocol=PROTOCOL,
            path=f"{path}.max_transactions.window_seconds",
        )
        if count < 0 or window <= 0:
            fail(
                ERR_INVALID_FIELD,
                f"max_transactions must have count >= 0 and window_seconds > 0, got "
                f"count={count} window_seconds={window}",
                protocol=PROTOCOL,
                path=f"{path}.max_transactions",
            )
        scope.add(VelocityMax(count, window))

    unmapped_keys(restrictions, _RESTRICTION_KEYS, protocol=PROTOCOL, path=path, warnings=warnings)


def _merchant_id(allowance: Mapping[str, Any]) -> str | None:
    merchant = get_any(allowance, "merchant", default=None)
    if isinstance(merchant, Mapping):
        ident = get_any(merchant, "id", "merchant_id", "name", default=None)
        if isinstance(ident, str) and ident.strip():
            return ident.strip()
    elif isinstance(merchant, str) and merchant.strip():
        return merchant.strip()
    for key in ("merchant_id", "merchantId"):
        value = allowance.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _agent_id(token: Mapping[str, Any], payload: Mapping[str, Any], *, token_path: str) -> str:
    for source, path in ((token, token_path), (payload, "$")):
        agent = get_any(source, "agent", default=None)
        if isinstance(agent, Mapping):
            ident = get_any(agent, "id", "agent_id", "name", default=None)
            if isinstance(ident, str) and ident.strip():
                return ident.strip()
        elif isinstance(agent, str) and agent.strip():
            return agent.strip()
        for key in ("agent_id", "agentId"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        metadata = source.get("metadata")
        if isinstance(metadata, Mapping):
            value = get_any(metadata, "agent_id", "agentId", default=None)
            if isinstance(value, str) and value.strip():
                return value.strip()
    fail(
        ERR_MISSING_FIELD,
        "no agent identity on the token; a delegated payment that names no delegate cannot be "
        "attributed, and unattributable authority is exactly what this layer refuses",
        protocol=PROTOCOL,
        path=f"{token_path}.agent.id",
    )


def _issuer(token: Mapping[str, Any]) -> str | None:
    for key in ("issuer", "psp", "provider", "issued_by", "issuedBy"):
        value = token.get(key)
        if isinstance(value, Mapping):
            value = get_any(value, "id", "name", default=None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# --------------------------------------------------------------------------------------
# Checkout session -> Cart
# --------------------------------------------------------------------------------------


def _cart_from_session(
    session: Mapping[str, Any],
    *,
    path: str,
    fallback_currency: str,
    fallback_merchant: str | None,
    warnings: WarningLog,
) -> Cart:
    raw_currency = get_any(session, "currency", default=None)
    currency = (
        normalize_currency(raw_currency, protocol=PROTOCOL, path=f"{path}.currency")
        if raw_currency is not None
        else fallback_currency
    )

    merchant = fallback_merchant or ""
    raw_merchant = get_any(session, "merchant", default=None)
    if isinstance(raw_merchant, Mapping):
        ident = get_any(raw_merchant, "id", "merchant_id", "name", default=None)
        if isinstance(ident, str) and ident.strip():
            merchant = ident.strip()
    elif isinstance(raw_merchant, str) and raw_merchant.strip():
        merchant = raw_merchant.strip()
    else:
        for key in ("merchant_id", "merchantId"):
            value = session.get(key)
            if isinstance(value, str) and value.strip():
                merchant = value.strip()
                break

    raw_items = get_any(session, "line_items", "lineItems", "items", default=None)
    items = require_sequence(raw_items, protocol=PROTOCOL, path=f"{path}.line_items")

    lines = [
        _parse_line_item(
            require_mapping(raw, protocol=PROTOCOL, path=f"{path}.line_items[{i}]"),
            path=f"{path}.line_items[{i}]",
            warnings=warnings,
        )
        for i, raw in enumerate(items)
    ]

    stated_total = _stated_total(session, path=path)
    return finalize_cart(
        merchant=merchant,
        currency=currency,
        lines=lines,
        protocol=PROTOCOL,
        path=path,
        warnings=warnings,
        stated_total=stated_total,
    )


def _stated_total(session: Mapping[str, Any], *, path: str) -> int | None:
    totals = get_any(session, "totals", default=None)
    if isinstance(totals, (list, tuple)):
        for i, entry in enumerate(totals):
            if not isinstance(entry, Mapping):
                continue
            if str(get_any(entry, "type", "kind", default="")).lower() == "total":
                return parse_minor_units(
                    get_any(entry, "amount", "value", default=None),
                    protocol=PROTOCOL,
                    path=f"{path}.totals[{i}].amount",
                )
    raw = get_any(session, "total", "amount_total", "amountTotal", default=None)
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        raw = get_any(raw, "amount", "value", default=None)
    return parse_minor_units(raw, protocol=PROTOCOL, path=f"{path}.total")


def _parse_line_item(item: Mapping[str, Any], *, path: str, warnings: WarningLog) -> CartLine:
    inner = get_any(item, "item", "product", default=None)
    inner = inner if isinstance(inner, Mapping) else {}

    name = get_any(item, "name", "description", "label", default=None)
    if not isinstance(name, str) or not name.strip():
        name = get_any(inner, "name", "title", "description", default=None)
    if not isinstance(name, str) or not name.strip():
        fail(
            ERR_INVALID_FIELD,
            "line item has no name",
            protocol=PROTOCOL,
            path=f"{path}.item.name",
        )
    name = name.strip()

    qty_raw = get_any(item, "quantity", "qty", default=None)
    if qty_raw is None:
        qty_raw = get_any(inner, "quantity", "qty", default=1)
    if isinstance(qty_raw, bool) or not isinstance(qty_raw, int) or qty_raw < 1:
        fail(
            ERR_INVALID_FIELD,
            f"quantity must be a positive integer, got {qty_raw!r}",
            protocol=PROTOCOL,
            path=f"{path}.quantity",
        )

    raw_total = get_any(item, "total", "subtotal", "amount", "base_amount", "baseAmount", default=None)
    if raw_total is None:
        fail(
            ERR_MISSING_FIELD,
            "line item carries no total/subtotal/base_amount",
            protocol=PROTOCOL,
            path=f"{path}.total",
        )
    amount = parse_minor_units(raw_total, protocol=PROTOCOL, path=f"{path}.total")

    # Prefer the product identifier over the line-item identifier: the cart diff keys on
    # SKU, and a merchant re-issuing the same product under a new line id must not read as
    # a removal plus an injection.
    sku = get_any(item, "sku", default=None)
    if not isinstance(sku, str) or not sku.strip():
        sku = get_any(inner, "sku", "id", default=None)
    if not isinstance(sku, str) or not sku.strip():
        sku = get_any(item, "item_id", "id", default=None)
    if not isinstance(sku, str) or not sku.strip():
        sku = f"label:{name}"
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"line item {name!r} carries no SKU; the intent-versus-execution diff will key on "
            "its name instead",
            path=f"{path}.sku",
        )
    else:
        sku = sku.strip()

    mcc_raw = get_any(item, "merchant_category_code", "merchantCategoryCode", "mcc", default=None)
    if mcc_raw is None:
        mcc_raw = get_any(inner, "merchant_category_code", "mcc", default=None)
    if mcc_raw is None:
        mcc = UNKNOWN_MCC
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"line item {name!r} carries no merchant category code — ACP checkout sessions do "
            "not standardise one — so it is scored as MCC 0 and fails closed against any MCC "
            "allowlist",
            path=f"{path}.merchant_category_code",
        )
    elif isinstance(mcc_raw, bool) or not isinstance(mcc_raw, (int, str)):
        fail(
            ERR_INVALID_FIELD,
            f"merchant category code must be numeric, got {mcc_raw!r}",
            protocol=PROTOCOL,
            path=f"{path}.merchant_category_code",
        )
    elif isinstance(mcc_raw, str):
        if not mcc_raw.strip().isdigit():
            fail(
                ERR_INVALID_FIELD,
                f"merchant category code must be numeric, got {mcc_raw!r}",
                protocol=PROTOCOL,
                path=f"{path}.merchant_category_code",
            )
        mcc = int(mcc_raw.strip())
    else:
        mcc = int(mcc_raw)

    category = get_any(item, "category", "product_category", default=None)
    if not isinstance(category, str) or not category.strip():
        category = get_any(inner, "category", default=None)
    if not isinstance(category, str) or not category.strip():
        category = UNKNOWN_CATEGORY
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"line item {name!r} carries no category; it fails closed against any category "
            "allowlist",
            path=f"{path}.category",
        )
    else:
        category = category.strip()

    return CartLine(
        sku=sku,
        description=name,
        amount=amount,
        mcc=mcc,
        category=category,
        qty=qty_raw,
    )
