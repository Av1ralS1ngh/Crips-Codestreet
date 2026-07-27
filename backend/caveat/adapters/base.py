"""Shared vocabulary for the cross-protocol adapters.

Three wire formats arrive at one endpoint — AP2 Verifiable Credentials, ACP delegated
payment tokens, raw MCP tool calls — and every one of them leaves here as the same two
objects: a `ConstraintSet` and a `Cart`. Nothing downstream of `normalize()` knows which
protocol the request came in on.

Translation is lossy, and the loss runs in one dangerous direction. When a source protocol
expresses a restriction our DSL cannot hold — an AP2 SKU allowlist, an ACP checkout-session
binding, a denylist of any kind — the translated scope authorizes *more* than the human
signed. Those are recorded as warnings with `widening=True`. Callers must treat a widening
warning as a reason to refuse or to escalate, never as a note. Silent loss of a constraint
is precisely the escalation this project exists to stop.

Money and clocks, the two things that ruin payment code:

  * Amounts become integer minor units here, at the edge, or they raise. A JSON float is
    rejected outright — `4000.10` does not survive a round trip through binary floating
    point, and the decision path must never see one.
  * Adapters read timestamps out of payloads and never consult the system clock. A
    normalized request is a pure function of its bytes, so any run replays.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, NoReturn, Sequence

from ..cart import Cart
from ..constraints import (
    AmountMax,
    CategoryAllow,
    Constraint,
    ConstraintSet,
    CumulativeMax,
    ExpiresAt,
    GeoAllow,
    MccAllow,
    MerchantAllow,
    NotBefore,
    StepUpOver,
    VelocityMax,
)

# --------------------------------------------------------------------------------------
# Protocol names.
# --------------------------------------------------------------------------------------

PROTOCOL_AP2 = "ap2"
PROTOCOL_ACP = "acp"
PROTOCOL_MCP = "mcp"

# --------------------------------------------------------------------------------------
# Error codes. Adapters fail loudly; a payload we cannot read is never half-translated.
# --------------------------------------------------------------------------------------

ERR_UNKNOWN_PROTOCOL = "ADAPTER_UNKNOWN_PROTOCOL"
ERR_AMBIGUOUS_PROTOCOL = "ADAPTER_AMBIGUOUS_PROTOCOL"
ERR_MISSING_FIELD = "ADAPTER_MISSING_FIELD"
ERR_INVALID_FIELD = "ADAPTER_INVALID_FIELD"
ERR_FLOAT_MONEY = "ADAPTER_FLOAT_MONEY"
ERR_AMBIGUOUS_MONEY_UNITS = "ADAPTER_AMBIGUOUS_MONEY_UNITS"
ERR_INVALID_CREDENTIAL = "ADAPTER_INVALID_CREDENTIAL"
ERR_UNSUPPORTED_TOOL = "ADAPTER_UNSUPPORTED_TOOL"
ERR_CURRENCY_CONFLICT = "ADAPTER_CURRENCY_CONFLICT"
ERR_TOTAL_MISMATCH = "ADAPTER_TOTAL_MISMATCH"

# --------------------------------------------------------------------------------------
# Warning codes. A warning is a translation fact, not a decision.
# --------------------------------------------------------------------------------------

# Understood the field, cannot hold it in the DSL. Always widening.
WARN_UNREPRESENTABLE_CONSTRAINT = "ADAPTER_CONSTRAINT_UNREPRESENTABLE"
# Did not understand a key sitting inside a constraint-bearing object. Assumed widening,
# because an unrecognised key is far more likely to be a restriction than a permission.
WARN_UNMAPPED_FIELD = "ADAPTER_UNMAPPED_FIELD"
# Held it, but not exactly. Only emitted when the approximation is the tighter one.
WARN_APPROXIMATED_CONSTRAINT = "ADAPTER_CONSTRAINT_APPROXIMATED"
# Free-text intent. Nothing in a solver enforces English.
WARN_UNENFORCEABLE_INTENT = "ADAPTER_INTENT_UNENFORCEABLE"
# The published protocol has no field for these constraint kinds at all.
WARN_PROTOCOL_CANNOT_EXPRESS = "ADAPTER_PROTOCOL_CANNOT_EXPRESS"
WARN_NO_AMOUNT_CAP = "ADAPTER_NO_AMOUNT_CAP"
WARN_NO_EXPIRY = "ADAPTER_NO_EXPIRY"
WARN_UNBOUNDED_SCOPE = "ADAPTER_UNBOUNDED_SCOPE"
WARN_NO_SIGNATURE = "ADAPTER_NO_SIGNATURE"
WARN_SIGNATURE_NOT_VERIFIED = "ADAPTER_SIGNATURE_NOT_VERIFIED"
WARN_NO_CART = "ADAPTER_NO_CART_IN_PAYLOAD"
WARN_MISSING_LINE_DETAIL = "ADAPTER_MISSING_LINE_DETAIL"

# --------------------------------------------------------------------------------------
# Signature status. No adapter in this build performs cryptography.
# --------------------------------------------------------------------------------------

SIG_ABSENT = "ABSENT"
SIG_PRESENT_UNVERIFIED = "PRESENT_UNVERIFIED"

# Set to True only by an implementation that actually verifies. Nothing here does.
SIGNATURE_VERIFICATION_IMPLEMENTED = False


@dataclass(frozen=True)
class SignatureStatus:
    """What was actually checked about the caller's signature material.

    `verified` is False in every code path of this build. Structural validation of a proof
    block is not signature verification and this type refuses to let the two be confused.
    """

    state: str
    detail: str
    algorithm: str | None = None
    key_reference: str | None = None
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "algorithm": self.algorithm,
            "key_reference": self.key_reference,
            "verified": self.verified,
            "verification_implemented": SIGNATURE_VERIFICATION_IMPLEMENTED,
        }


NO_SIGNATURE = SignatureStatus(
    state=SIG_ABSENT,
    detail="payload carries no signature material",
)


# --------------------------------------------------------------------------------------
# Errors and warnings.
# --------------------------------------------------------------------------------------


class AdapterError(ValueError):
    """A payload we refuse to translate. Carries a machine-readable code."""

    def __init__(
        self, code: str, message: str, *, protocol: str | None = None, path: str | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.protocol = protocol
        self.path = path
        where = f" at {path}" if path else ""
        who = f"[{protocol}] " if protocol else ""
        super().__init__(f"{who}{code}{where}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "protocol": self.protocol,
            "path": self.path,
        }


def fail(
    code: str, message: str, *, protocol: str | None = None, path: str | None = None
) -> NoReturn:
    raise AdapterError(code, message, protocol=protocol, path=path)


@dataclass(frozen=True)
class TranslationWarning:
    """One fact lost or approximated in translation.

    `widening=True` means the canonical scope authorizes transactions the source credential
    did not. That is an escalation in waiting, and callers are expected to gate on it.

    Authenticity is a separate axis and is reported on `SignatureStatus`, not here: an
    unsigned request has not had its scope widened, it simply asserts no authority.
    """

    code: str
    detail: str
    path: str | None = None
    widening: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "path": self.path,
            "widening": self.widening,
        }


class WarningLog:
    """Ordered, de-duplicated warning accumulator."""

    def __init__(self) -> None:
        self._items: list[TranslationWarning] = []
        self._seen: set[tuple[str, str, str | None, bool]] = set()

    def add(
        self, code: str, detail: str, *, path: str | None = None, widening: bool = False
    ) -> None:
        key = (code, detail, path, widening)
        if key in self._seen:
            return
        self._seen.add(key)
        self._items.append(TranslationWarning(code, detail, path, widening))

    def build(self) -> tuple[TranslationWarning, ...]:
        return tuple(self._items)


# --------------------------------------------------------------------------------------
# Canonical scope assembly.
# --------------------------------------------------------------------------------------

# Constraints are emitted in this order regardless of the order the wire format listed
# them, so the same authority expressed in AP2, ACP and MCP serialises to identical bytes.
# Canonical bytes are what make a mandate hashable, ledgerable and replayable.
_CANONICAL_ORDER: tuple[type[Constraint], ...] = (
    AmountMax,
    CumulativeMax,
    VelocityMax,
    MerchantAllow,
    MccAllow,
    CategoryAllow,
    GeoAllow,
    NotBefore,
    ExpiresAt,
    StepUpOver,
)

_RANK = {cls: i for i, cls in enumerate(_CANONICAL_ORDER)}

ALL_CONSTRAINT_KINDS: tuple[str, ...] = tuple(cls.kind for cls in _CANONICAL_ORDER)


class ScopeBuilder:
    def __init__(self) -> None:
        self._constraints: list[Constraint] = []

    def add(self, constraint: Constraint | None) -> None:
        if constraint is not None:
            self._constraints.append(constraint)

    def kinds(self) -> frozenset[str]:
        return frozenset(c.kind for c in self._constraints)

    def build(self) -> ConstraintSet:
        # sorted() is stable, so two constraints of the same kind keep the order the
        # payload listed them in.
        return ConstraintSet(
            sorted(self._constraints, key=lambda c: _RANK.get(type(c), len(_RANK)))
        )


# Constraint kinds each *published* protocol has no field for. Deployments extend these
# protocols with proprietary blocks, and the adapters read those extensions where they
# exist, so a gap is only reported when the payload did not in fact carry that kind.
# This table is our reading of the specs as of mid-2026, not a normative statement.
PROTOCOL_SPEC_GAPS: dict[str, frozenset[str]] = {
    PROTOCOL_AP2: frozenset({CumulativeMax.kind, VelocityMax.kind}),
    PROTOCOL_ACP: frozenset(
        {MccAllow.kind, CategoryAllow.kind, GeoAllow.kind, VelocityMax.kind, NotBefore.kind}
    ),
    PROTOCOL_MCP: frozenset(
        {
            CumulativeMax.kind,
            VelocityMax.kind,
            GeoAllow.kind,
            NotBefore.kind,
            ExpiresAt.kind,
            StepUpOver.kind,
        }
    ),
}


def report_spec_gaps(protocol: str, scope: ConstraintSet, warnings: WarningLog) -> None:
    """Name the constraint kinds this protocol could not have carried.

    Only kinds absent from the built scope are reported: if a deployment extension supplied
    one, nothing was lost. Informational rather than widening — the source credential did
    not express these either, so the translation dropped nothing. It still matters, because
    a parent mandate that *does* carry them will make the entailment check reject this
    request, and the operator deserves to know why before it happens.
    """
    present = {c.kind for c in scope}
    gaps = sorted(PROTOCOL_SPEC_GAPS.get(protocol, frozenset()) - present)
    if gaps:
        warnings.add(
            WARN_PROTOCOL_CANNOT_EXPRESS,
            f"{protocol.upper()} has no field for: {', '.join(gaps)}. "
            "A parent mandate carrying any of these will reject this request at the "
            "entailment check rather than silently accepting a broader child.",
        )


# --------------------------------------------------------------------------------------
# The canonical request.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedRequest:
    """One wire request, in the only shape the governance kernel understands.

    `scope` is the child's *declared* scope in the sense entailment.py means it: the
    credential chain did not cross the protocol boundary, so this set is a claim that must
    be proved to entail the parent mandate before it authorizes anything.
    """

    protocol: str
    scope: ConstraintSet
    intent_cart: Cart
    holder: str
    executed_cart: Cart | None = None
    signature: SignatureStatus = NO_SIGNATURE
    warnings: tuple[TranslationWarning, ...] = ()
    credential_id: str | None = None
    issuer: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def warning_codes(self) -> tuple[str, ...]:
        return tuple(w.code for w in self.warnings)

    def widening_warnings(self) -> tuple[TranslationWarning, ...]:
        """Warnings whose effect is that this scope authorizes more than the source did."""
        return tuple(w for w in self.warnings if w.widening)

    @property
    def lossy_widening(self) -> bool:
        return bool(self.widening_warnings())

    def has_warning(self, code: str) -> bool:
        return any(w.code == code for w in self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "holder": self.holder,
            "issuer": self.issuer,
            "credential_id": self.credential_id,
            "scope": self.scope.to_dicts(),
            "scope_display": self.scope.describe(),
            "intent_cart": self.intent_cart.to_dict(),
            "intent_hash": self.intent_cart.hash(),
            "executed_cart": self.executed_cart.to_dict() if self.executed_cart else None,
            "executed_hash": self.executed_cart.hash() if self.executed_cart else None,
            "signature": self.signature.to_dict(),
            "warnings": [w.to_dict() for w in self.warnings],
            "lossy_widening": self.lossy_widening,
            "metadata": dict(self.metadata),
        }


# --------------------------------------------------------------------------------------
# Field access.
# --------------------------------------------------------------------------------------

_MISSING = object()


def get_any(obj: Mapping[str, Any], *keys: str, default: Any = _MISSING) -> Any:
    """First present key among `keys`. JSON-LD in the wild mixes snake_case and camelCase."""
    for key in keys:
        if key in obj:
            return obj[key]
    return default


def has_any(obj: Mapping[str, Any], *keys: str) -> bool:
    return any(key in obj for key in keys)


def require_mapping(
    value: Any, *, protocol: str, path: str, code: str = ERR_INVALID_FIELD
) -> Mapping[str, Any]:
    if value is _MISSING or value is None:
        fail(ERR_MISSING_FIELD, f"{path} is required", protocol=protocol, path=path)
    if not isinstance(value, Mapping):
        fail(code, f"{path} must be an object, got {type(value).__name__}", protocol=protocol, path=path)
    return value


def require_sequence(value: Any, *, protocol: str, path: str) -> Sequence[Any]:
    if value is _MISSING or value is None:
        fail(ERR_MISSING_FIELD, f"{path} is required", protocol=protocol, path=path)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        fail(
            ERR_INVALID_FIELD,
            f"{path} must be an array, got {type(value).__name__}",
            protocol=protocol,
            path=path,
        )
    return value


def require_str(value: Any, *, protocol: str, path: str) -> str:
    if value is _MISSING or value is None:
        fail(ERR_MISSING_FIELD, f"{path} is required", protocol=protocol, path=path)
    if not isinstance(value, str) or not value.strip():
        fail(
            ERR_INVALID_FIELD,
            f"{path} must be a non-empty string, got {value!r}",
            protocol=protocol,
            path=path,
        )
    return value.strip()


def unmapped_keys(
    obj: Mapping[str, Any], known: Iterable[str], *, protocol: str, path: str, warnings: WarningLog
) -> None:
    """Record every key inside a constraint-bearing object we did not consume.

    Assumed widening. We cannot tell whether an unknown key restricted or permitted, and
    guessing "permitted" is how authority leaks.
    """
    known_set = set(known)
    for key in obj:
        if key in known_set or key.startswith("@") or key.startswith("_"):
            continue
        warnings.add(
            WARN_UNMAPPED_FIELD,
            f"{key!r} is not part of the canonical constraint DSL and was not applied; "
            "if it restricted this mandate, the translated scope is broader than the source",
            path=f"{path}.{key}",
            widening=True,
        )


# --------------------------------------------------------------------------------------
# Money. Integer minor units or an exception — there is no third outcome.
# --------------------------------------------------------------------------------------

# Partial ISO 4217 exponent table: entries that are not two. Anything unlisted falls back
# to two, which is right for the vast majority and wrong for a handful of zero-decimal
# currencies not enumerated here.
_MINOR_DIGITS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "BHD": 3,
    "KWD": 3,
    "OMR": 3,
    "JOD": 3,
    "TND": 3,
}

DEFAULT_MINOR_DIGITS = 2

_DECIMAL_RE = re.compile(r"^(?P<sign>-?)(?P<whole>\d+)(?:\.(?P<frac>\d+))?$")


def minor_digits(currency: str) -> int:
    return _MINOR_DIGITS.get(currency.upper(), DEFAULT_MINOR_DIGITS)


def normalize_currency(value: Any, *, protocol: str, path: str) -> str:
    code = require_str(value, protocol=protocol, path=path).upper()
    if len(code) != 3 or not code.isalpha():
        fail(
            ERR_INVALID_FIELD,
            f"{code!r} is not an ISO 4217 alphabetic code",
            protocol=protocol,
            path=path,
        )
    return code


def parse_int(value: Any, *, protocol: str, path: str) -> int:
    """A plain integer field — counts, windows, quantities. Not money."""
    if isinstance(value, bool):
        fail(ERR_INVALID_FIELD, f"{path} must be an integer, got a boolean", protocol=protocol, path=path)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    fail(
        ERR_INVALID_FIELD,
        f"{path} must be an integer, got {value!r}",
        protocol=protocol,
        path=path,
    )


def parse_minor_units(value: Any, *, protocol: str, path: str) -> int:
    """For protocols that transmit integer minor units (ACP/Stripe convention)."""
    if isinstance(value, bool):
        fail(ERR_INVALID_FIELD, f"{path} must be an integer amount, got a boolean", protocol=protocol, path=path)
    if isinstance(value, float):
        fail(
            ERR_FLOAT_MONEY,
            f"{path} arrived as a JSON float ({value!r}); floats do not round-trip money. "
            "Send integer minor units.",
            protocol=protocol,
            path=path,
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    fail(
        ERR_INVALID_FIELD,
        f"{path} must be an integer number of minor units, got {value!r}",
        protocol=protocol,
        path=path,
    )


def parse_decimal_amount(value: Any, currency: str, *, protocol: str, path: str) -> int:
    """For protocols that transmit W3C-style decimal strings ("4000.00").

    Strings only. An integer here would be unit-ambiguous — 4000 could be rupees or paise —
    and this layer refuses to guess.
    """
    if isinstance(value, float):
        fail(
            ERR_FLOAT_MONEY,
            f"{path} arrived as a JSON float ({value!r}); W3C payment amounts are strings "
            "precisely so that money never touches binary floating point",
            protocol=protocol,
            path=path,
        )
    if not isinstance(value, str):
        fail(
            ERR_AMBIGUOUS_MONEY_UNITS,
            f"{path} must be a decimal string such as \"4000.00\"; got {type(value).__name__}, "
            "which does not say whether it means major or minor units",
            protocol=protocol,
            path=path,
        )
    match = _DECIMAL_RE.match(value.strip())
    if match is None:
        fail(
            ERR_INVALID_FIELD,
            f"{path} is not a plain decimal string: {value!r}",
            protocol=protocol,
            path=path,
        )
    digits = minor_digits(currency)
    frac = match.group("frac") or ""
    if len(frac) > digits:
        fail(
            ERR_INVALID_FIELD,
            f"{path} carries {len(frac)} decimal places but {currency} has {digits}; "
            "refusing to round money",
            protocol=protocol,
            path=path,
        )
    scaled = int(match.group("whole")) * (10**digits) + int((frac.ljust(digits, "0")) or 0)
    return -scaled if match.group("sign") else scaled


def parse_amount_object(
    value: Any, *, protocol: str, path: str, default_currency: str | None = None
) -> tuple[int, str]:
    """Parse a W3C `PaymentCurrencyAmount`: {"currency": "INR", "value": "4000.00"}.

    Also accepts an explicit minor-unit form, {"currency": "INR", "minor_units": 400000},
    because a protocol extension that already speaks paise should not be forced through a
    decimal string.
    """
    obj = require_mapping(value, protocol=protocol, path=path)
    raw_currency = get_any(obj, "currency", "currencyCode", "currency_code", default=default_currency)
    if raw_currency is None or raw_currency is _MISSING:
        fail(ERR_MISSING_FIELD, f"{path}.currency is required", protocol=protocol, path=path)
    currency = normalize_currency(raw_currency, protocol=protocol, path=f"{path}.currency")

    minor = get_any(obj, "minor_units", "minorUnits", "amount_minor", "amountMinor")
    if minor is not _MISSING:
        return parse_minor_units(minor, protocol=protocol, path=f"{path}.minor_units"), currency

    raw_value = get_any(obj, "value", "amount")
    if raw_value is _MISSING:
        fail(ERR_MISSING_FIELD, f"{path}.value is required", protocol=protocol, path=path)
    return (
        parse_decimal_amount(raw_value, currency, protocol=protocol, path=f"{path}.value"),
        currency,
    )


# --------------------------------------------------------------------------------------
# Time. Read from the payload, never from the clock.
# --------------------------------------------------------------------------------------


def parse_timestamp(value: Any, *, protocol: str, path: str) -> int:
    """RFC 3339 string or epoch seconds -> epoch seconds.

    A naive datetime is rejected: an expiry without a timezone is an expiry that means
    different things in Chennai and New York.
    """
    if isinstance(value, bool):
        fail(ERR_INVALID_FIELD, f"{path} must be a timestamp, got a boolean", protocol=protocol, path=path)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        fail(
            ERR_INVALID_FIELD,
            f"{path} must be epoch seconds as an integer or an RFC 3339 string, got a float",
            protocol=protocol,
            path=path,
        )
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        try:
            parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            fail(
                ERR_INVALID_FIELD,
                f"{path} is not an RFC 3339 timestamp: {value!r}",
                protocol=protocol,
                path=path,
            )
        if parsed.tzinfo is None:
            fail(
                ERR_INVALID_FIELD,
                f"{path} has no timezone offset; an ambiguous expiry is not an expiry",
                protocol=protocol,
                path=path,
            )
        return int(parsed.timestamp())
    fail(ERR_INVALID_FIELD, f"{path} must be a timestamp, got {value!r}", protocol=protocol, path=path)


# --------------------------------------------------------------------------------------
# Allowlists.
# --------------------------------------------------------------------------------------


def parse_allowlist(
    value: Any,
    *,
    protocol: str,
    path: str,
    warnings: WarningLog,
) -> tuple[str, ...] | None:
    """Read `["a","b"]` or `{"allow": ["a","b"]}` into a tuple of strings.

    A denylist alongside the allowlist is reported as unrepresentable and widening: our DSL
    holds allowlists only, so dropping a denial grants what the source refused.
    """
    if value is _MISSING or value is None:
        return None
    if isinstance(value, Mapping):
        deny = get_any(value, "deny", "denied", "block", "blocklist", "exclude")
        if deny is not _MISSING and deny:
            warnings.add(
                WARN_UNREPRESENTABLE_CONSTRAINT,
                f"{path} carries a denylist ({_preview(deny)}); the canonical DSL holds "
                "allowlists only, so this denial was not applied",
                path=path,
                widening=True,
            )
        allow = get_any(value, "allow", "allowed", "allowlist", "values", "ids")
        if allow is _MISSING:
            fail(
                ERR_INVALID_FIELD,
                f"{path} must be an array or an object with an 'allow' array",
                protocol=protocol,
                path=path,
            )
        value = allow
    items = require_sequence(value, protocol=protocol, path=path)
    out: list[str] = []
    for i, item in enumerate(items):
        if isinstance(item, Mapping):
            ident = get_any(item, "id", "identifier", "value", "code", "name")
            if ident is _MISSING:
                fail(
                    ERR_INVALID_FIELD,
                    f"{path}[{i}] is an object with no id/value to read",
                    protocol=protocol,
                    path=f"{path}[{i}]",
                )
            item = ident
        if isinstance(item, bool):
            fail(ERR_INVALID_FIELD, f"{path}[{i}] is a boolean", protocol=protocol, path=f"{path}[{i}]")
        if isinstance(item, int):
            item = str(item)
        if not isinstance(item, str) or not item.strip():
            fail(
                ERR_INVALID_FIELD,
                f"{path}[{i}] must be a non-empty string, got {item!r}",
                protocol=protocol,
                path=f"{path}[{i}]",
            )
        out.append(item.strip())
    if not out:
        # An empty allowlist authorizes nothing (constraints.py fails closed on it). That
        # is a legal, maximally-narrow mandate, so it passes through untouched.
        return ()
    # De-duplicate while preserving order, so canonical bytes do not depend on repetition.
    seen: set[str] = set()
    deduped = [x for x in out if not (x in seen or seen.add(x))]
    return tuple(deduped)


def parse_mcc_list(
    value: Any, *, protocol: str, path: str, warnings: WarningLog
) -> tuple[int, ...] | None:
    raw = parse_allowlist(value, protocol=protocol, path=path, warnings=warnings)
    if raw is None:
        return None
    out: list[int] = []
    for i, item in enumerate(raw):
        if not re.fullmatch(r"\d{1,4}", item):
            fail(
                ERR_INVALID_FIELD,
                f"{path}[{i}] must be a numeric merchant category code, got {item!r}",
                protocol=protocol,
                path=f"{path}[{i}]",
            )
        out.append(int(item))
    return tuple(out)


def _preview(value: Any, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------------------
# Cart assembly shared by the adapters.
# --------------------------------------------------------------------------------------

# MCC 0 and category "" are sentinels for "the payload did not say". Both fail closed
# against any allowlist in constraints.py, which is the direction we want when a merchant
# omits the field that would have got the line blocked.
UNKNOWN_MCC = 0
UNKNOWN_CATEGORY = ""


def finalize_cart(
    *,
    merchant: str,
    currency: str,
    lines: Sequence[Any],
    protocol: str,
    path: str,
    warnings: WarningLog,
    stated_total: int | None = None,
) -> Cart:
    cart = Cart.of(merchant, lines, currency=currency)
    if stated_total is not None and stated_total != cart.total():
        fail(
            ERR_TOTAL_MISMATCH,
            f"{path} states a total of {stated_total} minor units but its line items sum to "
            f"{cart.total()}; a cart that does not add up is not a cart",
            protocol=protocol,
            path=path,
        )
    if not cart.lines:
        warnings.add(
            WARN_NO_CART,
            f"{path} carries no line items; cart-versus-intent re-validation has nothing to "
            "compare and any merchant allowlist will fail closed",
            path=path,
        )
    return cart


def report_scope_health(scope: ConstraintSet, warnings: WarningLog) -> None:
    """Flag scopes that are dangerous by construction rather than by translation."""
    kinds = {c.kind for c in scope}
    if not kinds:
        warnings.add(
            WARN_UNBOUNDED_SCOPE,
            "no constraints were extracted; an empty scope authorizes every transaction and "
            "will fail the entailment check against any real parent mandate",
            widening=True,
        )
        return
    if AmountMax.kind not in kinds and StepUpOver.kind not in kinds:
        warnings.add(
            WARN_NO_AMOUNT_CAP,
            "no per-transaction amount cap was expressed; only the parent mandate bounds this",
        )
    if ExpiresAt.kind not in kinds:
        warnings.add(
            WARN_NO_EXPIRY,
            "no expiry was expressed; standing authority with no end date is a liability",
        )
