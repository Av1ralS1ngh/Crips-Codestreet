"""AP2 — Google's Agent Payments Protocol. Intent Mandates and Cart Mandates as W3C VCs.

AP2 was donated to the FIDO Alliance in April 2026 and American Express is a partner, so
this is the format an Amex-adjacent governance layer is most likely to be handed. Its two
consent objects map cleanly onto the split this kernel already makes:

  Intent Mandate   what the human authorized *before* seeing a cart — constraints. Becomes
                   a ConstraintSet.
  Cart Mandate     the exact cart that was signed. Becomes the intent Cart, which the PDP
                   later re-validates the executed cart against.

Both travel as W3C Verifiable Credentials: JSON-LD with an `@context`, a `credentialSubject`
naming the agent, and a Data Integrity `proof` block (ECDSA P-256 / SHA-256 cryptosuites).

**Signature verification is a stub.** This module validates the *structure* of the proof
block — presence, type, cryptosuite, verificationMethod, proofValue — and performs no
cryptography whatsoever. Verifying a Data Integrity proof honestly requires the issuer's
exact canonicalization (RDF canonicalization for `ecdsa-rdfc-2019`, JCS for
`ecdsa-jcs-2019`) and a resolved DID document; getting that subtly wrong would mean
claiming a verification we did not perform, which is worse than performing none. Every
NormalizedRequest from this adapter therefore reports
`signature.verified == False` and `signature.state == PRESENT_UNVERIFIED`, and carries an
ADAPTER_SIGNATURE_NOT_VERIFIED warning. Do not present this as verified AP2.

Accepted payload shapes:

  * a bare Intent Mandate VC,
  * a bare Cart Mandate VC,
  * an envelope `{"intent_mandate": VC, "cart_mandate": VC, "executed_cart": {...}}`.

`executed_cart` is deliberately *unsigned*: it is what the PSP was actually asked to
charge. The gap between it and the Cart Mandate is the post-signature mutation class the
AP2 red-team literature documents, and handing both to the PDP is what catches it.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

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
    NotBefore,
    StepUpOver,
)
from .base import (
    ERR_CURRENCY_CONFLICT,
    ERR_INVALID_CREDENTIAL,
    ERR_INVALID_FIELD,
    ERR_MISSING_FIELD,
    PROTOCOL_AP2,
    SIG_PRESENT_UNVERIFIED,
    UNKNOWN_CATEGORY,
    UNKNOWN_MCC,
    WARN_APPROXIMATED_CONSTRAINT,
    WARN_MISSING_LINE_DETAIL,
    WARN_SIGNATURE_NOT_VERIFIED,
    WARN_UNENFORCEABLE_INTENT,
    WARN_UNREPRESENTABLE_CONSTRAINT,
    NormalizedRequest,
    ScopeBuilder,
    SignatureStatus,
    WarningLog,
    fail,
    finalize_cart,
    get_any,
    has_any,
    parse_allowlist,
    parse_amount_object,
    parse_mcc_list,
    parse_timestamp,
    report_scope_health,
    report_spec_gaps,
    require_mapping,
    require_sequence,
    require_str,
    unmapped_keys,
)

PROTOCOL = PROTOCOL_AP2

W3C_VC_CONTEXTS = (
    "https://www.w3.org/ns/credentials/v2",
    "https://www.w3.org/2018/credentials/v1",
)
VC_BASE_TYPE = "VerifiableCredential"
TYPE_INTENT_MANDATE = "IntentMandate"
TYPE_CART_MANDATE = "CartMandate"

# Data Integrity cryptosuites AP2 profiles onto P-256. Listed so an unfamiliar suite is
# surfaced rather than waved through; none of them is actually executed here.
ECDSA_P256_CRYPTOSUITES = frozenset(
    {
        "ecdsa-rdfc-2019",
        "ecdsa-jcs-2019",
        "ecdsa-sd-2023",
        "EcdsaSecp256r1Signature2019",
    }
)

_INTENT_KEYS = {
    "amount": ("max_amount", "maxAmount", "maximum_amount", "spend_limit", "spendLimit"),
    "cumulative": ("cumulative_max_amount", "cumulativeMaxAmount", "total_budget", "totalBudget"),
    "merchants": ("merchants", "allowed_merchants", "allowedMerchants", "merchant_allowlist"),
    "mccs": (
        "merchant_category_codes",
        "merchantCategoryCodes",
        "allowed_mcc",
        "allowedMcc",
        "mcc_allowlist",
    ),
    "categories": ("categories", "allowed_categories", "allowedCategories", "product_categories"),
    "geo": ("geo", "allowed_countries", "allowedCountries", "countries"),
    "expiry": ("intent_expiry", "intentExpiry", "expires_at", "expiresAt"),
    "not_before": ("valid_from", "validFrom", "not_before", "notBefore"),
    "description": (
        "natural_language_description",
        "naturalLanguageDescription",
        "description",
    ),
    "skus": ("skus", "allowed_skus", "allowedSkus"),
    "refundability": (
        "required_refundability",
        "requires_refundability",
        "requiresRefundability",
    ),
    "confirmation": (
        "user_cart_confirmation_required",
        "userCartConfirmationRequired",
        "requires_user_confirmation",
    ),
}

_KNOWN_INTENT_KEYS = {k for aliases in _INTENT_KEYS.values() for k in aliases} | {
    "id",
    "type",
    "cart",
    "proposed_cart",
    "proposedCart",
}


# --------------------------------------------------------------------------------------
# Sniffing
# --------------------------------------------------------------------------------------


def sniff(payload: Mapping[str, Any]) -> bool:
    if has_any(payload, "intent_mandate", "intentMandate", "cart_mandate", "cartMandate"):
        return True
    contexts = payload.get("@context")
    if isinstance(contexts, str):
        contexts = [contexts]
    if isinstance(contexts, Sequence) and any(c in W3C_VC_CONTEXTS for c in contexts):
        return True
    return _has_mandate_type(payload)


def _has_mandate_type(doc: Mapping[str, Any]) -> bool:
    types = _types(doc)
    return TYPE_INTENT_MANDATE in types or TYPE_CART_MANDATE in types


def _types(doc: Mapping[str, Any]) -> tuple[str, ...]:
    raw = get_any(doc, "type", "@type", default=())
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Sequence):
        return tuple(str(x) for x in raw)
    return ()


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def parse(payload: Mapping[str, Any]) -> NormalizedRequest:
    warnings = WarningLog()
    intent_vc, cart_vc, executed_raw = _split_envelope(payload)

    if intent_vc is None and cart_vc is None:
        fail(
            ERR_MISSING_FIELD,
            "payload contains neither an Intent Mandate nor a Cart Mandate",
            protocol=PROTOCOL,
            path="$",
        )

    intent_cred = (
        _validate_vc(intent_vc, TYPE_INTENT_MANDATE, path="$.intent_mandate", warnings=warnings)
        if intent_vc is not None
        else None
    )
    cart_cred = (
        _validate_vc(cart_vc, TYPE_CART_MANDATE, path="$.cart_mandate", warnings=warnings)
        if cart_vc is not None
        else None
    )

    if intent_cred is not None and cart_cred is not None:
        if intent_cred.subject_id != cart_cred.subject_id:
            # A Cart Mandate issued to a different agent than the Intent Mandate is not a
            # translation problem, it is a substitution attack. Refuse to normalize it.
            fail(
                ERR_INVALID_CREDENTIAL,
                f"credentialSubject.id disagrees between the Intent Mandate "
                f"({intent_cred.subject_id!r}) and the Cart Mandate ({cart_cred.subject_id!r})",
                protocol=PROTOCOL,
                path="$.cart_mandate.credentialSubject.id",
            )

    # The Intent Mandate is the authority when both are present; the Cart Mandate is data.
    primary = intent_cred if intent_cred is not None else cart_cred
    if primary is None:  # pragma: no cover - _split_envelope guarantees one of the two
        fail(ERR_MISSING_FIELD, "no mandate to normalize", protocol=PROTOCOL, path="$")

    intent_cart: Cart | None = None
    if cart_cred is not None:
        intent_cart = _cart_from_cart_mandate(cart_cred, warnings)

    if intent_cred is not None:
        # A Cart Mandate's own expiry is folded into the scope when one came with it: a
        # signed cart that has gone stale must not be replayable just because the standing
        # intent is still live. Taking the minimum is always the safe direction.
        cart_expiry = cart_cred.extra.get("cart_expiry") if cart_cred is not None else None
        scope = _scope_from_intent(intent_cred, warnings, extra_expiry=cart_expiry)
        if intent_cart is None:
            intent_cart = _cart_from_intent(intent_cred, warnings)
    else:
        scope = _scope_from_cart(intent_cart, cart_cred, warnings)

    if intent_cart is None:
        intent_cart = finalize_cart(
            merchant="",
            currency="INR",
            lines=(),
            protocol=PROTOCOL,
            path="$.intent_mandate",
            warnings=warnings,
        )

    executed_cart = None
    if executed_raw is not None:
        executed_cart = _parse_cart_contents(
            require_mapping(executed_raw, protocol=PROTOCOL, path="$.executed_cart"),
            path="$.executed_cart",
            warnings=warnings,
        )

    report_spec_gaps(PROTOCOL, scope, warnings)
    report_scope_health(scope, warnings)

    signature = _signature_status(primary, warnings)

    metadata: dict[str, Any] = {
        "vc_types": list(primary.types),
        "has_intent_mandate": intent_cred is not None,
        "has_cart_mandate": cart_cred is not None,
        "proof_purpose": primary.proof.get("proofPurpose") or primary.proof.get("proof_purpose"),
        # Every proof in the envelope was checked for structure. None was verified.
        "proofs_structurally_validated": [
            c.path for c in (intent_cred, cart_cred) if c is not None
        ],
    }
    if cart_cred is not None:
        metadata["cart_id"] = cart_cred.extra.get("cart_id")

    return NormalizedRequest(
        protocol=PROTOCOL,
        scope=scope,
        intent_cart=intent_cart,
        executed_cart=executed_cart,
        holder=primary.subject_id,
        signature=signature,
        warnings=warnings.build(),
        credential_id=primary.credential_id,
        issuer=primary.issuer,
        metadata=metadata,
    )


# --------------------------------------------------------------------------------------
# Envelope / credential structure
# --------------------------------------------------------------------------------------


def _split_envelope(
    payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, Any]:
    intent = get_any(payload, "intent_mandate", "intentMandate", default=None)
    cart = get_any(payload, "cart_mandate", "cartMandate", default=None)
    executed = get_any(payload, "executed_cart", "executedCart", default=None)

    if intent is None and cart is None:
        types = _types(payload)
        if TYPE_INTENT_MANDATE in types:
            intent = payload
        elif TYPE_CART_MANDATE in types:
            cart = payload
        else:
            fail(
                ERR_INVALID_CREDENTIAL,
                f"AP2 credential type must include {TYPE_INTENT_MANDATE!r} or "
                f"{TYPE_CART_MANDATE!r}; got {list(types)}",
                protocol=PROTOCOL,
                path="$.type",
            )
    if intent is not None:
        intent = require_mapping(intent, protocol=PROTOCOL, path="$.intent_mandate")
    if cart is not None:
        cart = require_mapping(cart, protocol=PROTOCOL, path="$.cart_mandate")
    return intent, cart, executed


class _Credential:
    """A structurally validated VC. Structurally — see the module docstring on signatures."""

    def __init__(
        self,
        *,
        doc: Mapping[str, Any],
        types: tuple[str, ...],
        issuer: str,
        subject: Mapping[str, Any],
        subject_id: str,
        proof: Mapping[str, Any],
        credential_id: str | None,
        valid_from: int | None,
        valid_until: int | None,
        path: str,
    ) -> None:
        self.doc = doc
        self.types = types
        self.issuer = issuer
        self.subject = subject
        self.subject_id = subject_id
        self.proof = proof
        self.credential_id = credential_id
        self.valid_from = valid_from
        self.valid_until = valid_until
        self.path = path
        self.extra: dict[str, Any] = {}


def _validate_vc(
    doc: Mapping[str, Any], expected_type: str, *, path: str, warnings: WarningLog
) -> _Credential:
    contexts = doc.get("@context")
    if isinstance(contexts, str):
        contexts = [contexts]
    if not isinstance(contexts, Sequence) or not contexts:
        fail(
            ERR_INVALID_CREDENTIAL,
            "@context is required and must be a non-empty array",
            protocol=PROTOCOL,
            path=f"{path}.@context",
        )
    if str(contexts[0]) not in W3C_VC_CONTEXTS:
        # The VC data model requires the base context first. A payload that reorders it is
        # not a VC, whatever it calls itself.
        fail(
            ERR_INVALID_CREDENTIAL,
            f"the first @context entry must be one of {list(W3C_VC_CONTEXTS)}; got "
            f"{contexts[0]!r}",
            protocol=PROTOCOL,
            path=f"{path}.@context[0]",
        )

    types = _types(doc)
    if VC_BASE_TYPE not in types:
        fail(
            ERR_INVALID_CREDENTIAL,
            f"type must include {VC_BASE_TYPE!r}; got {list(types)}",
            protocol=PROTOCOL,
            path=f"{path}.type",
        )
    if expected_type not in types:
        fail(
            ERR_INVALID_CREDENTIAL,
            f"expected a {expected_type}; got {list(types)}",
            protocol=PROTOCOL,
            path=f"{path}.type",
        )

    raw_issuer = get_any(doc, "issuer", default=None)
    if isinstance(raw_issuer, Mapping):
        raw_issuer = get_any(raw_issuer, "id", default=None)
    issuer = require_str(raw_issuer, protocol=PROTOCOL, path=f"{path}.issuer")

    subject = require_mapping(
        get_any(doc, "credentialSubject", "credential_subject", default=None),
        protocol=PROTOCOL,
        path=f"{path}.credentialSubject",
    )
    subject_id = require_str(
        get_any(subject, "id", "holder", "agent", default=None),
        protocol=PROTOCOL,
        path=f"{path}.credentialSubject.id",
    )

    proof = get_any(doc, "proof", default=None)
    if proof is None:
        fail(
            ERR_INVALID_CREDENTIAL,
            "proof block is absent; an unsigned document is not a Verifiable Credential",
            protocol=PROTOCOL,
            path=f"{path}.proof",
        )
    if isinstance(proof, Sequence) and not isinstance(proof, (str, bytes, Mapping)):
        if not proof:
            fail(
                ERR_INVALID_CREDENTIAL,
                "proof array is empty",
                protocol=PROTOCOL,
                path=f"{path}.proof",
            )
        proof = proof[0]
    proof = require_mapping(proof, protocol=PROTOCOL, path=f"{path}.proof")
    _validate_proof(proof, path=f"{path}.proof", warnings=warnings)

    valid_from = get_any(doc, "validFrom", "issuanceDate", "valid_from", default=None)
    valid_until = get_any(doc, "validUntil", "expirationDate", "valid_until", default=None)

    return _Credential(
        doc=doc,
        types=types,
        issuer=issuer,
        subject=subject,
        subject_id=subject_id,
        proof=proof,
        credential_id=get_any(doc, "id", default=None),
        valid_from=(
            parse_timestamp(valid_from, protocol=PROTOCOL, path=f"{path}.validFrom")
            if valid_from is not None
            else None
        ),
        valid_until=(
            parse_timestamp(valid_until, protocol=PROTOCOL, path=f"{path}.validUntil")
            if valid_until is not None
            else None
        ),
        path=path,
    )


def _validate_proof(proof: Mapping[str, Any], *, path: str, warnings: WarningLog) -> None:
    require_str(get_any(proof, "type", default=None), protocol=PROTOCOL, path=f"{path}.type")
    require_str(
        get_any(proof, "verificationMethod", "verification_method", default=None),
        protocol=PROTOCOL,
        path=f"{path}.verificationMethod",
    )
    if not has_any(proof, "proofValue", "proof_value", "jws", "signature"):
        fail(
            ERR_INVALID_CREDENTIAL,
            "proof carries no proofValue/jws; there is nothing that could be verified",
            protocol=PROTOCOL,
            path=f"{path}.proofValue",
        )
    suite = get_any(proof, "cryptosuite", "cryptoSuite", default=None)
    if suite is not None and str(suite) not in ECDSA_P256_CRYPTOSUITES:
        warnings.add(
            WARN_SIGNATURE_NOT_VERIFIED,
            f"cryptosuite {suite!r} is outside the ECDSA P-256 profile this adapter "
            "recognises; nothing was verified either way",
            path=f"{path}.cryptosuite",
        )


def _signature_status(cred: _Credential, warnings: WarningLog) -> SignatureStatus:
    suite = get_any(cred.proof, "cryptosuite", "cryptoSuite", default=None)
    algorithm = str(suite) if suite is not None else str(get_any(cred.proof, "type", default="?"))
    key_ref = str(get_any(cred.proof, "verificationMethod", "verification_method", default=""))
    warnings.add(
        WARN_SIGNATURE_NOT_VERIFIED,
        "AP2 proof block was structurally validated only. No ECDSA verification, no "
        "canonicalization and no DID resolution were performed by this build.",
        path=f"{cred.path}.proof",
    )
    return SignatureStatus(
        state=SIG_PRESENT_UNVERIFIED,
        detail=(
            "Data Integrity proof present and structurally well formed. Signature "
            "verification is a stub in this build."
        ),
        algorithm=algorithm,
        key_reference=key_ref or None,
    )


# --------------------------------------------------------------------------------------
# Intent Mandate -> ConstraintSet
# --------------------------------------------------------------------------------------


def _intent_object(cred: _Credential) -> tuple[Mapping[str, Any], str]:
    inner = get_any(cred.subject, "intent", "intent_mandate", "intentMandate", default=None)
    if inner is not None:
        return (
            require_mapping(inner, protocol=PROTOCOL, path=f"{cred.path}.credentialSubject.intent"),
            f"{cred.path}.credentialSubject.intent",
        )
    return cred.subject, f"{cred.path}.credentialSubject"


def _scope_from_intent(
    cred: _Credential, warnings: WarningLog, *, extra_expiry: int | None = None
) -> ConstraintSet:
    intent, path = _intent_object(cred)
    scope = ScopeBuilder()

    amount = get_any(intent, *_INTENT_KEYS["amount"], default=None)
    if amount is not None:
        minor, _currency = parse_amount_object(
            amount, protocol=PROTOCOL, path=f"{path}.max_amount"
        )
        scope.add(AmountMax(minor))

    cumulative = get_any(intent, *_INTENT_KEYS["cumulative"], default=None)
    if cumulative is not None:
        minor, _currency = parse_amount_object(
            cumulative, protocol=PROTOCOL, path=f"{path}.cumulative_max_amount"
        )
        scope.add(CumulativeMax(minor))

    merchants = parse_allowlist(
        get_any(intent, *_INTENT_KEYS["merchants"], default=None),
        protocol=PROTOCOL,
        path=f"{path}.merchants",
        warnings=warnings,
    )
    if merchants is not None:
        scope.add(MerchantAllow(merchants))

    mccs = parse_mcc_list(
        get_any(intent, *_INTENT_KEYS["mccs"], default=None),
        protocol=PROTOCOL,
        path=f"{path}.merchant_category_codes",
        warnings=warnings,
    )
    if mccs is not None:
        scope.add(MccAllow(mccs))

    categories = parse_allowlist(
        get_any(intent, *_INTENT_KEYS["categories"], default=None),
        protocol=PROTOCOL,
        path=f"{path}.categories",
        warnings=warnings,
    )
    if categories is not None:
        scope.add(CategoryAllow(categories))

    geo = parse_allowlist(
        get_any(intent, *_INTENT_KEYS["geo"], default=None),
        protocol=PROTOCOL,
        path=f"{path}.geo",
        warnings=warnings,
    )
    if geo is not None:
        scope.add(GeoAllow(geo))

    # Expiry: the credential envelope and the intent body can both carry one. Take the
    # earlier of the two — the narrower reading is the safe reading.
    expiries = [
        t
        for t in (
            cred.valid_until,
            extra_expiry,
            _maybe_ts(get_any(intent, *_INTENT_KEYS["expiry"], default=None), f"{path}.intent_expiry"),
        )
        if t is not None
    ]
    if expiries:
        scope.add(ExpiresAt(min(expiries)))

    starts = [
        t
        for t in (
            cred.valid_from,
            _maybe_ts(get_any(intent, *_INTENT_KEYS["not_before"], default=None), f"{path}.valid_from"),
        )
        if t is not None
    ]
    if starts:
        scope.add(NotBefore(max(starts)))

    # `user_cart_confirmation_required` is AP2's human-in-the-loop flag. It maps exactly
    # onto a step-up threshold of zero: nothing at all is auto-authorized.
    if _is_true(get_any(intent, *_INTENT_KEYS["confirmation"], default=None)):
        scope.add(StepUpOver(0))

    description = get_any(intent, *_INTENT_KEYS["description"], default=None)
    if isinstance(description, str) and description.strip():
        warnings.add(
            WARN_UNENFORCEABLE_INTENT,
            f"the mandate's natural-language intent ({description.strip()[:80]!r}) is not "
            "machine-enforceable; only the typed constraints below bind the agent",
            path=f"{path}.natural_language_description",
            widening=True,
        )

    skus = get_any(intent, *_INTENT_KEYS["skus"], default=None)
    if skus:
        allowed = parse_allowlist(
            skus, protocol=PROTOCOL, path=f"{path}.skus", warnings=warnings
        )
        warnings.add(
            WARN_UNREPRESENTABLE_CONSTRAINT,
            f"the Intent Mandate restricts purchases to {len(allowed or ())} specific SKU(s); "
            "the canonical DSL has no SKU predicate, so that restriction was not applied and "
            "this scope is broader than what was signed",
            path=f"{path}.skus",
            widening=True,
        )

    if _is_true(get_any(intent, *_INTENT_KEYS["refundability"], default=None)):
        warnings.add(
            WARN_UNREPRESENTABLE_CONSTRAINT,
            "the Intent Mandate requires refundable purchases only; the canonical DSL has no "
            "refundability predicate, so non-refundable carts are not blocked by this scope",
            path=f"{path}.required_refundability",
            widening=True,
        )

    unmapped_keys(intent, _KNOWN_INTENT_KEYS, protocol=PROTOCOL, path=path, warnings=warnings)
    return scope.build()


def _maybe_ts(value: Any, path: str) -> int | None:
    if value is None:
        return None
    return parse_timestamp(value, protocol=PROTOCOL, path=path)


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() == "true"


def _scope_from_cart(
    cart: Cart | None, cred: _Credential | None, warnings: WarningLog
) -> ConstraintSet:
    """Derive the narrowest scope containing a signed cart, when no Intent Mandate came.

    A Cart Mandate authorizes exactly one cart. The DSL cannot say "exactly this cart", so
    the closest it gets is the smallest box around it — which is strictly broader, hence
    the widening warning.
    """
    scope = ScopeBuilder()
    if cart is None or not cart.lines:
        return scope.build()
    scope.add(AmountMax(cart.total()))
    if cart.merchant:
        scope.add(MerchantAllow((cart.merchant,)))
    mccs = tuple(m for m in cart.mccs() if m != UNKNOWN_MCC)
    if mccs:
        scope.add(MccAllow(mccs))
    categories = tuple(c for c in cart.categories() if c != UNKNOWN_CATEGORY)
    if categories:
        scope.add(CategoryAllow(categories))
    if cred is not None:
        expiry = cred.extra.get("cart_expiry") or cred.valid_until
        if expiry is not None:
            scope.add(ExpiresAt(int(expiry)))
    warnings.add(
        WARN_APPROXIMATED_CONSTRAINT,
        "no Intent Mandate accompanied this Cart Mandate, so the scope was derived from the "
        "signed cart itself: the narrowest constraint set containing that cart. It still "
        "authorizes any cart inside the same box, which is broader than the one cart signed",
        path="$.cart_mandate",
        widening=True,
    )
    return scope.build()


# --------------------------------------------------------------------------------------
# Cart Mandate -> Cart
# --------------------------------------------------------------------------------------


def _cart_from_cart_mandate(cred: _Credential, warnings: WarningLog) -> Cart:
    contents = get_any(
        cred.subject, "contents", "cart", "cart_contents", "cartContents", default=None
    )
    if contents is None:
        contents = cred.subject
        path = f"{cred.path}.credentialSubject"
    else:
        path = f"{cred.path}.credentialSubject.contents"
    contents = require_mapping(contents, protocol=PROTOCOL, path=path)

    expiry = get_any(contents, "cart_expiry", "cartExpiry", default=None)
    if expiry is not None:
        cred.extra["cart_expiry"] = parse_timestamp(
            expiry, protocol=PROTOCOL, path=f"{path}.cart_expiry"
        )
    cart_id = get_any(contents, "id", "cart_id", "cartId", default=None)
    if isinstance(cart_id, str):
        cred.extra["cart_id"] = cart_id

    return _parse_cart_contents(contents, path=path, warnings=warnings)


def _cart_from_intent(cred: _Credential, warnings: WarningLog) -> Cart:
    """An Intent Mandate may carry a proposed cart in the human-present flow."""
    intent, path = _intent_object(cred)
    proposed = get_any(intent, "cart", "proposed_cart", "proposedCart", default=None)
    if proposed is None:
        return finalize_cart(
            merchant="",
            currency="INR",
            lines=(),
            protocol=PROTOCOL,
            path=path,
            warnings=warnings,
        )
    return _parse_cart_contents(
        require_mapping(proposed, protocol=PROTOCOL, path=f"{path}.cart"),
        path=f"{path}.cart",
        warnings=warnings,
    )


def _parse_cart_contents(
    contents: Mapping[str, Any], *, path: str, warnings: WarningLog
) -> Cart:
    """Read AP2 CartContents, or the same shape unwrapped, into a canonical Cart."""
    request = get_any(contents, "payment_request", "paymentRequest", default=None)
    if request is not None:
        request = require_mapping(request, protocol=PROTOCOL, path=f"{path}.payment_request")
        details = require_mapping(
            get_any(request, "details", default=None),
            protocol=PROTOCOL,
            path=f"{path}.payment_request.details",
        )
        details_path = f"{path}.payment_request.details"
    else:
        details = contents
        details_path = path

    raw_items = get_any(
        details, "display_items", "displayItems", "line_items", "lineItems", "items", default=None
    )
    items = require_sequence(raw_items, protocol=PROTOCOL, path=f"{details_path}.display_items")

    merchant = _merchant_id(contents, details, path=path)

    lines: list[CartLine] = []
    currencies: set[str] = set()
    for i, raw in enumerate(items):
        line, currency = _parse_item(
            require_mapping(raw, protocol=PROTOCOL, path=f"{details_path}.display_items[{i}]"),
            path=f"{details_path}.display_items[{i}]",
            warnings=warnings,
        )
        lines.append(line)
        currencies.add(currency)

    if len(currencies) > 1:
        fail(
            ERR_CURRENCY_CONFLICT,
            f"cart mixes currencies {sorted(currencies)}; the canonical Cart holds one",
            protocol=PROTOCOL,
            path=f"{details_path}.display_items",
        )

    stated_total: int | None = None
    total = get_any(details, "total", default=None)
    if total is not None:
        total = require_mapping(total, protocol=PROTOCOL, path=f"{details_path}.total")
        amount = get_any(total, "amount", default=total)
        stated_total, total_currency = parse_amount_object(
            amount,
            protocol=PROTOCOL,
            path=f"{details_path}.total.amount",
            default_currency=next(iter(currencies), None),
        )
        currencies.add(total_currency)
        if len(currencies) > 1:
            fail(
                ERR_CURRENCY_CONFLICT,
                f"cart total is in {total_currency} but its line items are not",
                protocol=PROTOCOL,
                path=f"{details_path}.total.amount.currency",
            )

    currency = next(iter(currencies), "INR")
    return finalize_cart(
        merchant=merchant,
        currency=currency,
        lines=lines,
        protocol=PROTOCOL,
        path=details_path,
        warnings=warnings,
        stated_total=stated_total,
    )


def _merchant_id(contents: Mapping[str, Any], details: Mapping[str, Any], *, path: str) -> str:
    for source in (contents, details):
        merchant = get_any(source, "merchant", default=None)
        if isinstance(merchant, Mapping):
            ident = get_any(merchant, "id", "merchant_id", "name", default=None)
            if isinstance(ident, str) and ident.strip():
                return ident.strip()
        elif isinstance(merchant, str) and merchant.strip():
            return merchant.strip()
        for key in ("merchant_id", "merchantId", "merchant_name", "merchantName"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    fail(
        ERR_MISSING_FIELD,
        "cart names no merchant; merchant allowlists cannot be evaluated against it",
        protocol=PROTOCOL,
        path=f"{path}.merchant",
    )


def _parse_item(
    item: Mapping[str, Any], *, path: str, warnings: WarningLog
) -> tuple[CartLine, str]:
    label = get_any(item, "label", "name", "description", default=None)
    if not isinstance(label, str) or not label.strip():
        fail(
            ERR_INVALID_FIELD,
            "display item has no label",
            protocol=PROTOCOL,
            path=f"{path}.label",
        )
    label = label.strip()

    qty_raw = get_any(item, "quantity", "qty", default=1)
    if isinstance(qty_raw, bool) or not isinstance(qty_raw, int) or qty_raw < 1:
        fail(
            ERR_INVALID_FIELD,
            f"quantity must be a positive integer, got {qty_raw!r}",
            protocol=PROTOCOL,
            path=f"{path}.quantity",
        )
    qty = qty_raw

    unit = get_any(item, "unit_amount", "unitAmount", default=None)
    if unit is not None:
        unit_minor, currency = parse_amount_object(
            unit, protocol=PROTOCOL, path=f"{path}.unit_amount"
        )
        amount = unit_minor * qty
    else:
        amount_obj = get_any(item, "amount", "price", default=None)
        if amount_obj is None:
            fail(
                ERR_MISSING_FIELD,
                "display item carries neither `amount` nor `unit_amount`",
                protocol=PROTOCOL,
                path=f"{path}.amount",
            )
        # W3C display-item amounts are line totals, not unit prices.
        amount, currency = parse_amount_object(
            amount_obj, protocol=PROTOCOL, path=f"{path}.amount"
        )

    sku = get_any(item, "sku", "id", "item_id", "itemId", "product_id", default=None)
    if not isinstance(sku, str) or not sku.strip():
        # The cart diff keys on SKU, so a label-derived key still detects an injected line;
        # it just cannot survive the merchant renaming an item.
        sku = f"label:{label}"
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"display item {label!r} carries no SKU; the intent-versus-execution diff will "
            "key on its label instead",
            path=f"{path}.sku",
        )
    else:
        sku = sku.strip()

    mcc_raw = get_any(
        item, "merchant_category_code", "merchantCategoryCode", "mcc", default=None
    )
    if mcc_raw is None:
        mcc = UNKNOWN_MCC
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"display item {label!r} carries no merchant category code; it is scored as MCC 0, "
            "which fails closed against any MCC allowlist",
            path=f"{path}.merchant_category_code",
        )
    else:
        mcc = _as_mcc(mcc_raw, path=f"{path}.merchant_category_code")

    category = get_any(item, "category", "product_category", "productCategory", default=None)
    if not isinstance(category, str) or not category.strip():
        category = UNKNOWN_CATEGORY
        warnings.add(
            WARN_MISSING_LINE_DETAIL,
            f"display item {label!r} carries no category; it fails closed against any category "
            "allowlist",
            path=f"{path}.category",
        )
    else:
        category = category.strip()

    return (
        CartLine(
            sku=sku,
            description=label,
            amount=amount,
            mcc=mcc,
            category=category,
            qty=qty,
        ),
        currency,
    )


def _as_mcc(value: Any, *, path: str) -> int:
    if isinstance(value, bool):
        fail(ERR_INVALID_FIELD, "merchant category code is a boolean", protocol=PROTOCOL, path=path)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    fail(
        ERR_INVALID_FIELD,
        f"merchant category code must be numeric, got {value!r}",
        protocol=PROTOCOL,
        path=path,
    )
