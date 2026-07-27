"""The evaluator, exposed over MCP. A transport, and nothing else.

Amex shipped Resy into Claude over MCP on 24 April 2026, so MCP is the issuer's own chosen
transport for reaching a third-party agent. This module answers the obvious next question:
if an issuer can hand an agent a restaurant, what stops it handing the agent the *value* of
the instrument the agent is about to choose? Nothing does. Only nobody has defined the
surface.

WHAT THIS MODULE MAY AND MAY NOT DO

  It computes NOTHING. Every integer that leaves this file was produced by
  `allocate.allocate`, `witness.verify_witness`, `evaluate.evaluate` or
  `receipt.witness_content_hash`. There is no arithmetic in this module beyond `len()`,
  and `test_mcp_server.py::test_server_module_contains_no_valuation_arithmetic` reads the
  source to keep it that way. A transport that quietly re-derives a number is a second
  implementation of the hot path, and two implementations of a hot path disagree
  eventually.

  It decides nothing either. `value_cart` returns the deterministic ranking the
  cardholder's `ValuationPolicy` produces, carrying that policy's hash, and it returns the
  full candidate set including refusals. What an agent does with that is the agent's
  business and lands in the agent's receipt, not in anything an issuer signed.

THE SDK QUESTION, ANSWERED HONESTLY

  The official Python MCP SDK (`pip install mcp`) is used when it imports. It is what the
  `serve_stdio()` path runs. If it is absent — an air-gapped judge's laptop, a locked-down
  venv — `serve_stdio()` falls back to `_serve_stdio_builtin()`, a direct implementation of
  the MCP stdio wire format: newline-delimited JSON-RPC 2.0 over stdin/stdout, handling
  `initialize`, `notifications/initialized`, `tools/list`, `tools/call` and `ping`. The
  fallback exists so the demo cannot be broken by a missing dependency, not because we
  prefer it. `sdk_status()` reports which one is live, and the CLI prints it.

CLOCK

  Every tool takes `as_of` as an explicit integer and the system clock is never read. Two
  invocations with the same arguments return byte-identical JSON, which is what makes an
  agent trace replayable and a demo safe to rehearse.

Honest limitations:
  * Manifests are signed HMAC-SHA256 under a prototype key held in this file. Production
    signs with the issuer's HSM key; canonicalisation and the verification flow are
    unchanged, and `get_manifest` reports the verification result either way.
  * Remaining credit balances are SYNTHETIC member state, labelled as such on every
    manifest. No live Offers feed is claimed or modelled.
  * A candidate set is single-currency. Ranking across currencies needs an FX rate, which
    is a market price this system does not carry and will not invent.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

from caveat.cart import Cart, CartLine

from .evaluate import (
    BENEFIT_APPLIED,
    BENEFIT_ELIGIBLE_UNUSED,
    BENEFIT_EXHAUSTED,
    BENEFIT_INELIGIBLE,
    BENEFIT_NOT_ENROLLED,
    BENEFIT_UNPRICED,
    CRITERIA,
    CRITERION_MAX_INCREMENTAL,
    STATUS_ATTESTED,
    UNUSED_DISPLACED,
    UNUSED_NO_HEADROOM,
    UNUSED_ZERO_VALUE,
    Evaluation,
    EvaluationError,
    InstrumentValuation,
    ValuationPolicy,
    evaluate,
)
from .manifest import Manifest, SignedManifest, sign_manifest, verify_manifest
from .products import (
    CAT_DINING,
    CAT_DUNKIN,
    CAT_GOLD_DINING_PARTNER,
    CAT_LYFT,
    CAT_UBER,
    CAT_US_SUPERMARKET,
    USD,
    catalogue,
    fmt_currency,
    profile,
)
from .receipt import witness_content_hash
from .scenarios import (
    DEMO_CLOCK,
    INR_TRIP_CART,
    PLATINUM_TRIP_CART,
    USD_TRIP_CART,
)

SERVER_NAME = "plumbline"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"

SERVER_INSTRUCTIONS = (
    "PLUMBLINE values a shopping cart on each of a Card Member's payment instruments and "
    "shows its work. Call value_cart to get the deterministic per-instrument value with a "
    "line-item derivation, a witness content hash and a verification status. Call "
    "explain_derivation for the human-readable allocation of one instrument, including the "
    "benefits that were blocked by exclusivity or capacity. Call get_manifest for the "
    "issuer-signed facts behind those numbers. Do not compute value yourself: every number "
    "these tools return is produced by a deterministic allocator and independently "
    "verified, and a number you derive instead is not backed by a witness."
)

# The clock every tool defaults to. Chosen, never sampled.
DEFAULT_AS_OF = DEMO_CLOCK

# Prototype signing material. Production signs each issuer's manifest with that issuer's
# own HSM key; nothing about canonicalisation or verification changes.
ISSUER_KEY = "plumbline-demo-issuer-key"
ISSUER_KEY_ID = "prototype-issuer"

# --------------------------------------------------------------------------------------
# Reason codes. Module-level constants, never inline strings.
# --------------------------------------------------------------------------------------

MCP_ERR_UNKNOWN_TOOL = "PLUMBLINE_MCP_UNKNOWN_TOOL"
MCP_ERR_UNKNOWN_CART = "PLUMBLINE_MCP_UNKNOWN_CART"
MCP_ERR_UNKNOWN_INSTRUMENT = "PLUMBLINE_MCP_UNKNOWN_INSTRUMENT"
MCP_ERR_BAD_ARGUMENTS = "PLUMBLINE_MCP_BAD_ARGUMENTS"
MCP_ERR_MIXED_CURRENCY = "PLUMBLINE_MCP_CANDIDATE_SET_MIXED_CURRENCY"
MCP_ERR_UNKNOWN_CRITERION = "PLUMBLINE_MCP_UNKNOWN_CRITERION"
MCP_ERR_NOT_EVALUATED = "PLUMBLINE_MCP_INSTRUMENT_NOT_EVALUATED"

# Issuer signature status, reported on every manifest this server hands out.
SIG_VERIFIED = "ISSUER_SIGNATURE_VERIFIED"
SIG_INVALID = "ISSUER_SIGNATURE_INVALID"

# Why a declared benefit contributed nothing to an allocation. These are a re-labelling of
# what `evaluate.build_derivation` already decided; this module classifies, it never judges.
BLOCK_EXCLUSIVITY = "BLOCKED_BY_EXCLUSIVITY_GROUP"
BLOCK_CAPACITY = "BLOCKED_BY_CAPACITY_HEADROOM"
BLOCK_EXHAUSTED = "BLOCKED_BALANCE_EXHAUSTED"
BLOCK_NOT_ENROLLED = "BLOCKED_NOT_ENROLLED"
BLOCK_INELIGIBLE = "BLOCKED_ADMITS_NO_LINE"
BLOCK_ZERO_VALUE = "BLOCKED_YIELDS_NOTHING"
BLOCK_UNPRICED = "NOT_BLOCKED_CONSIDERED_BUT_UNPRICED"

WITNESS_VERIFIED = "WITNESS_VERIFIED_NO_SOLVER_REQUIRED"
WITNESS_FAILED = "WITNESS_FAILED_VERIFICATION"
WITNESS_ABSENT = "NO_WITNESS_PRODUCED"

# Said on every value_cart response, because an agent reading this over a wire has no other
# way to learn where the signature boundary sits.
RANKING_NOTE = (
    "The ranking is produced by the Card Member's valuation policy, whose hash is recorded "
    "above. No issuer endorses it and no issuer-signed artifact asserts that one instrument "
    "beat another on this cart."
)

UNPRICED_NOTE = (
    "Values cover priced benefits only. Entries under considered_but_unpriced were seen and "
    "deliberately not scored; the integer does not claim to be the whole worth of any card."
)

CONSERVATISM_NOTE = (
    "Each value is exactly what a concrete, exhibited allocation realizes, re-checked by a "
    "linear-time verifier that needs no solver. Because the allocation is achievable the "
    "value cannot exceed the true optimum. It is not proven optimal; the gap is measured "
    "offline and never implied here."
)


class ToolError(ValueError):
    """A caller-side mistake, carrying a typed reason code.

    Distinct from a `Refusal`, which is a normal, expected output describing input the
    evaluator understood perfectly and declined to put a number on.
    """

    def __init__(self, code: str, detail: str, remedy: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.remedy = remedy

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {"code": self.code, "detail": self.detail, "remedy": self.remedy},
        }


# --------------------------------------------------------------------------------------
# Demo baskets. Named so an agent can be given a shopping goal rather than a JSON cart.
# --------------------------------------------------------------------------------------

# The basket the selector demo runs on. Ordinary spend: groceries, a neighbourhood dinner,
# a delivery order, coffee, and two ride-hail lines. Nothing here favours any card's own
# travel portal, which is the point — a portal-shaped cart decides the ranking before the
# evaluator sees it.
EVERYDAY_BASKET = Cart.of(
    "m_neighbourhood",
    [
        CartLine("wk_grocery", "Weekly groceries, US supermarket", 48_600, 5411, CAT_US_SUPERMARKET),
        CartLine("wk_dinner", "Neighbourhood dinner for four", 41_200, 5812, CAT_DINING),
        CartLine("wk_grubhub", "Grubhub delivery order", 6_400, 5812, CAT_GOLD_DINING_PARTNER),
        CartLine("wk_dunkin", "Dunkin' coffee run", 1_800, 5814, CAT_DUNKIN),
        CartLine("wk_uber", "Uber rides this week", 7_200, 4121, CAT_UBER),
        CartLine("wk_lyft", "Lyft ride to the airport", 2_600, 4121, CAT_LYFT),
    ],
    currency=USD,
)

CART_EVERYDAY = "everyday_basket"
CART_USD_TRIP = "usd_trip"
CART_AMEX_TRAVEL_TRIP = "amex_travel_trip"
CART_SMARTBUY_TRIP = "smartbuy_trip"

DEMO_CARTS: Mapping[str, Cart] = {
    CART_EVERYDAY: EVERYDAY_BASKET,
    CART_USD_TRIP: USD_TRIP_CART,
    CART_AMEX_TRAVEL_TRIP: PLATINUM_TRIP_CART,
    CART_SMARTBUY_TRIP: INR_TRIP_CART,
}

CART_DESCRIPTIONS: Mapping[str, str] = {
    CART_EVERYDAY: (
        "An ordinary week: groceries, a neighbourhood dinner, a delivery order, coffee and "
        "two ride-hail trips. Channel-neutral — no card's own travel portal appears."
    ),
    CART_USD_TRIP: (
        "A trip booked direct with the airline, the property and the restaurant. Neutral "
        "between issuers by construction."
    ),
    CART_AMEX_TRAVEL_TRIP: (
        "A trip booked through amextravel.com, including a Fine Hotels + Resorts stay, a "
        "Resy dinner and airline incidentals."
    ),
    CART_SMARTBUY_TRIP: (
        "An India trip booked through HDFC SmartBuy: two accelerated hotel lines drawing on "
        "one shared monthly point pool, plus a flight, a dinner and a retail purchase."
    ),
}


# --------------------------------------------------------------------------------------
# Instruments
# --------------------------------------------------------------------------------------


def signed_catalogue(as_of: int) -> tuple[SignedManifest, ...]:
    """Every modelled product, signed under the prototype issuer key at one clock."""
    return tuple(
        sign_manifest(m, ISSUER_KEY, key_id=ISSUER_KEY_ID) for m in catalogue(as_of)
    )


def signed_by_id(as_of: int) -> dict[str, SignedManifest]:
    return {s.manifest.manifest_id: s for s in signed_catalogue(as_of)}


def resolve_instrument(token: str, as_of: int) -> SignedManifest:
    """Find one instrument by manifest id, or by a unique case-insensitive name match.

    An agent that read a product name off a page should not have to know a manifest id, but
    an ambiguous name is refused rather than resolved to whichever matched first.
    """
    if not isinstance(token, str) or not token.strip():
        raise ToolError(
            MCP_ERR_BAD_ARGUMENTS,
            "an instrument must be named by a non-empty string",
            "pass a manifest id from list_instruments, e.g. 'amex-gold-us-2026'",
        )
    needle = token.strip()
    by_id = signed_by_id(as_of)
    if needle in by_id:
        return by_id[needle]
    lowered = needle.lower()
    hits = [
        s
        for s in by_id.values()
        if lowered in s.manifest.product.lower()
        or lowered in f"{s.manifest.issuer} {s.manifest.product}".lower()
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ToolError(
            MCP_ERR_UNKNOWN_INSTRUMENT,
            f"no instrument matches {needle!r}; known ids: {', '.join(sorted(by_id))}",
            "call list_instruments and use one of the manifest ids it returns",
        )
    raise ToolError(
        MCP_ERR_UNKNOWN_INSTRUMENT,
        f"{needle!r} matches {len(hits)} instruments "
        f"({', '.join(sorted(h.manifest.manifest_id for h in hits))}); an ambiguous name is "
        f"refused rather than resolved to whichever matched first",
        "name the instrument by its manifest id",
    )


def resolve_cart(spec: Any) -> tuple[str, Cart]:
    """Accept either a named basket or an inline cart object. Returns (label, cart).

    The inline form exists so a live checkout session can be valued; the named form exists
    so an agent can be handed a shopping goal in English.
    """
    if isinstance(spec, str):
        key = spec.strip()
        if key not in DEMO_CARTS:
            raise ToolError(
                MCP_ERR_UNKNOWN_CART,
                f"no cart named {key!r}; known: {', '.join(sorted(DEMO_CARTS))}",
                "call list_carts, or pass an inline cart object instead of a name",
            )
        return key, DEMO_CARTS[key]
    if isinstance(spec, Mapping):
        return "inline", _cart_from_mapping(spec)
    raise ToolError(
        MCP_ERR_BAD_ARGUMENTS,
        f"cart must be a named basket (string) or an inline cart object, got "
        f"{type(spec).__name__}",
        "pass a cart id from list_carts, or {'merchant': ..., 'currency': ..., 'lines': [...]}",
    )


def _cart_from_mapping(spec: Mapping[str, Any]) -> Cart:
    raw_lines = spec.get("lines")
    if not isinstance(raw_lines, Sequence) or isinstance(raw_lines, (str, bytes)):
        raise ToolError(
            MCP_ERR_BAD_ARGUMENTS,
            "an inline cart needs a `lines` array",
            "each line needs sku, description, amount_minor, mcc and category",
        )
    lines: list[CartLine] = []
    for i, raw in enumerate(raw_lines):
        if not isinstance(raw, Mapping):
            raise ToolError(
                MCP_ERR_BAD_ARGUMENTS, f"lines[{i}] is not an object", "pass a line object"
            )
        try:
            amount = raw["amount_minor"]
        except KeyError:
            raise ToolError(
                MCP_ERR_BAD_ARGUMENTS,
                f"lines[{i}] carries no amount_minor",
                "money is integer minor units here; there is no float path and no guessed "
                "factor of 100",
            ) from None
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ToolError(
                MCP_ERR_BAD_ARGUMENTS,
                f"lines[{i}].amount_minor must be an integer number of minor units, got "
                f"{amount!r}",
                "send 4860 for $48.60; this layer will not read a float or guess a factor",
            )
        lines.append(
            CartLine(
                sku=str(raw.get("sku") or f"line_{i}"),
                description=str(raw.get("description") or raw.get("name") or f"line {i}"),
                amount=amount,
                mcc=int(raw.get("mcc", 0)),
                category=str(raw.get("category", "")),
                qty=int(raw.get("qty", 1)),
            )
        )
    return Cart.of(
        str(spec.get("merchant") or "inline_merchant"),
        lines,
        currency=str(spec.get("currency") or USD),
    )


def _policy(criterion: str | None) -> ValuationPolicy:
    """Build the cardholder's valuation policy. The criterion is a closed set."""
    chosen = criterion or CRITERION_MAX_INCREMENTAL
    if chosen not in CRITERIA:
        raise ToolError(
            MCP_ERR_UNKNOWN_CRITERION,
            f"unknown ranking criterion {chosen!r}; expected one of {', '.join(CRITERIA)}",
            "the criterion belongs to the Card Member and comes from a closed set; pick one",
        )
    try:
        return ValuationPolicy(criterion=chosen)
    except EvaluationError as exc:  # pragma: no cover - guarded by the CRITERIA check above
        raise ToolError(MCP_ERR_UNKNOWN_CRITERION, str(exc)) from exc


def _selected(
    instruments: Sequence[str] | None, cart: Cart, as_of: int
) -> tuple[SignedManifest, ...]:
    """The candidate set, defaulting to every instrument in the cart's currency.

    Defaulting to *everything* rather than to a caller-supplied subset matters: omission is
    the attack this system exists to make visible, and a server whose default candidate set
    was whatever the agent remembered to ask for would be helping.
    """
    if instruments:
        picked = tuple(resolve_instrument(str(t), as_of) for t in instruments)
    else:
        picked = tuple(
            s for s in signed_catalogue(as_of) if s.manifest.currency == cart.currency
        )
    currencies = {s.manifest.currency for s in picked}
    if len(currencies) > 1:
        raise ToolError(
            MCP_ERR_MIXED_CURRENCY,
            f"candidate set spans {', '.join(sorted(currencies))}; ranking across "
            f"currencies would need an FX rate this system does not carry",
            "build a single-currency candidate set",
        )
    if not picked:
        raise ToolError(
            MCP_ERR_UNKNOWN_INSTRUMENT,
            f"no modelled instrument is denominated in {cart.currency}",
            "call list_instruments to see which currencies are covered",
        )
    return picked


# --------------------------------------------------------------------------------------
# Projections. Every number below is read off an object the engine produced.
# --------------------------------------------------------------------------------------


def _witness_status(v: InstrumentValuation) -> str:
    if v.witness is None:
        return WITNESS_ABSENT
    if v.verification is not None and v.verification.ok:
        return WITNESS_VERIFIED
    return WITNESS_FAILED


def _instrument_payload(v: InstrumentValuation, currency: str) -> dict[str, Any]:
    """One candidate, as an agent sees it. Values copied, never recomputed."""
    prof = profile(v.manifest_id)
    return {
        "instrument_id": v.manifest_id,
        "issuer": v.issuer,
        "product": v.product,
        "currency": v.currency,
        "status": v.status,
        "asserted_value_minor": v.asserted_minor,
        "asserted_value_display": (
            None if v.asserted_minor is None else fmt_currency(v.asserted_minor, currency)
        ),
        "naive_per_line_sum_minor": v.naive_sum_minor,
        "naive_per_line_sum_display": fmt_currency(v.naive_sum_minor, currency),
        "overstatement_avoided_minor": v.overstatement_avoided_minor(),
        "overstatement_avoided_display": fmt_currency(
            v.overstatement_avoided_minor(), currency
        ),
        "protection_value_minor": v.protection_value_minor,
        "witness_content_hash": (
            witness_content_hash(v.witness, currency=currency)
            if v.witness is not None
            else ""
        ),
        "witness_status": _witness_status(v),
        "witness_verified": bool(v.verification and v.verification.ok),
        "verification": (
            v.verification.to_dict(currency=currency) if v.verification else None
        ),
        "manifest_content_hash": v.manifest_hash,
        "annual_fee_minor": prof.annual_fee_minor,
        "annual_fee_display": fmt_currency(prof.annual_fee_minor, currency),
        "annual_fee_note": prof.fee_note,
        "benefits_considered": v.considered_benefits,
        "benefits_applied": v.applied_benefits,
        "derivation": (
            v.derivation.to_dict(currency=currency) if v.derivation else None
        ),
        "derivation_lines": (
            v.derivation.render_lines(currency=currency)
            if v.derivation is not None
            else []
        ),
        "considered_but_unpriced": list(v.unpriced_labels),
        "refusals": [r.to_dict() for r in v.refusals],
        "disclosures": [d.to_dict() for d in v.disclosures],
    }


def _evaluation_payload(
    evaluation: Evaluation, *, cart_label: str, as_of: int
) -> dict[str, Any]:
    currency = evaluation.cart.currency
    ranking = evaluation.ranking
    return {
        "ok": True,
        "as_of": as_of,
        "cart": {
            "label": cart_label,
            "merchant": evaluation.cart.merchant,
            "currency": currency,
            "hash": evaluation.cart_hash,
            "total_minor": evaluation.cart.total(),
            "total_display": fmt_currency(evaluation.cart.total(), currency),
            "lines": [
                {
                    "sku": line.sku,
                    "description": line.description,
                    "amount_minor": line.amount,
                    "amount_display": fmt_currency(line.amount, currency),
                    "mcc": line.mcc,
                    "category": line.category,
                }
                for line in evaluation.cart.lines
            ],
        },
        "valuation_policy": evaluation.policy.to_dict(),
        "candidate_set_size": len(evaluation.candidates),
        "instruments": [_instrument_payload(v, currency) for v in evaluation.candidates],
        "ranking": (
            None
            if ranking is None
            else {
                **ranking.to_dict(currency=currency),
                "entries": [
                    {
                        **e.to_dict(currency=currency),
                        "asserted_display": fmt_currency(e.asserted_minor, currency),
                        "incremental_display": fmt_currency(e.incremental_minor, currency),
                    }
                    for e in ranking.entries
                ],
            }
        ),
        "signable": evaluation.signable,
        "refusals": [r.to_dict() for r in evaluation.all_refusals()],
        "disclosures": [d.to_dict() for d in evaluation.disclosures],
        "notes": [CONSERVATISM_NOTE, RANKING_NOTE, UNPRICED_NOTE],
    }


def _benefit_block_reason(node: Any) -> str:
    """Classify why one declared benefit yielded nothing, from what evaluate() decided.

    A re-labelling, not a judgement: the status and the detail string were both produced by
    `evaluate.build_derivation`, and this function only maps them onto a closed vocabulary
    an agent can branch on.
    """
    status = node.status
    if status == BENEFIT_UNPRICED:
        return BLOCK_UNPRICED
    if status == BENEFIT_NOT_ENROLLED:
        return BLOCK_NOT_ENROLLED
    if status == BENEFIT_EXHAUSTED:
        return BLOCK_EXHAUSTED
    if status == BENEFIT_INELIGIBLE:
        return BLOCK_INELIGIBLE
    detail = node.detail or ""
    if detail.startswith(UNUSED_DISPLACED):
        return BLOCK_EXCLUSIVITY
    if detail.startswith(UNUSED_ZERO_VALUE):
        return BLOCK_ZERO_VALUE
    if detail.startswith(UNUSED_NO_HEADROOM):
        return BLOCK_CAPACITY
    return BLOCK_CAPACITY


# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


def tool_list_instruments(*, as_of: int = DEFAULT_AS_OF, **_: Any) -> dict[str, Any]:
    """Every instrument this server can value, with its issuer signature status."""
    out = []
    for signed in signed_catalogue(as_of):
        m = signed.manifest
        prof = profile(m.manifest_id)
        out.append(
            {
                "instrument_id": m.manifest_id,
                "issuer": m.issuer,
                "product": m.product,
                "currency": m.currency,
                "annual_fee_minor": prof.annual_fee_minor,
                "annual_fee_display": fmt_currency(prof.annual_fee_minor, m.currency),
                "annual_fee_note": prof.fee_note,
                "priced_benefits": len(m.priced()),
                "unpriced_benefits": len(m.unpriced()),
                "manifest_content_hash": m.content_hash(),
                "signature_status": (
                    SIG_VERIFIED if verify_manifest(signed, ISSUER_KEY) else SIG_INVALID
                ),
            }
        )
    return {"ok": True, "as_of": as_of, "instruments": out}


def tool_list_carts(*, as_of: int = DEFAULT_AS_OF, **_: Any) -> dict[str, Any]:
    """The named baskets an agent can be pointed at."""
    return {
        "ok": True,
        "as_of": as_of,
        "carts": [
            {
                "cart_id": key,
                "description": CART_DESCRIPTIONS[key],
                "merchant": cart.merchant,
                "currency": cart.currency,
                "line_count": len(cart.lines),
                "total_minor": cart.total(),
                "total_display": fmt_currency(cart.total(), cart.currency),
                "hash": cart.hash(),
            }
            for key, cart in DEMO_CARTS.items()
        ],
    }


def tool_get_manifest(
    *, product: str, as_of: int = DEFAULT_AS_OF, **_: Any
) -> dict[str, Any]:
    """The issuer-signed facts for one product, with the signature and its status.

    The signature covers the manifest's canonical bytes and nothing else — earn rates,
    protections, credit balances, caps. It does not cover any ranking, any comparison, or
    any statement about another issuer's product, and there is no code path in this package
    that would produce one that did.
    """
    signed = resolve_instrument(product, as_of)
    m: Manifest = signed.manifest
    verified = verify_manifest(signed, ISSUER_KEY)
    prof = profile(m.manifest_id)
    return {
        "ok": True,
        "as_of": as_of,
        "instrument_id": m.manifest_id,
        "issuer": m.issuer,
        "product": m.product,
        "currency": m.currency,
        "issued_at": m.issued_at,
        "source": m.source,
        "annual_fee_minor": prof.annual_fee_minor,
        "annual_fee_display": fmt_currency(prof.annual_fee_minor, m.currency),
        "annual_fee_note": prof.fee_note,
        "manifest_content_hash": m.content_hash(),
        "signature": {
            "alg": "HMAC-SHA256",
            "key_id": signed.key_id,
            "value": signed.signature,
            "status": SIG_VERIFIED if verified else SIG_INVALID,
            "covers": (
                "the manifest body only — earn rates, protections, credit balances and "
                "caps. Never a ranking and never a comparison between instruments."
            ),
        },
        "benefits": [b.to_dict() for b in m.benefits],
        "priced_benefit_ids": [b.benefit_id for b in m.priced()],
        "considered_but_unpriced": [
            {"benefit_id": b.benefit_id, "label": b.label, "rationale": b.note}
            for b in m.unpriced()
        ],
        "notes": [
            "Remaining balances are SYNTHETIC member state for a demo persona, not live "
            "issuer data. No live Offers feed is claimed.",
            "The manifest declares no acceptance predicate. Where a card is accepted "
            "belongs in the agent's routing layer, from data the agent already holds.",
        ],
    }


def tool_value_cart(
    *,
    cart: Any,
    instruments: Sequence[str] | None = None,
    criterion: str | None = None,
    as_of: int = DEFAULT_AS_OF,
    claims: Mapping[str, int] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Value one cart on every candidate instrument, with derivations and witness hashes.

    `claims` is a value someone *proposed* — a model's guess, a merchant's marketing, a
    cached figure — keyed by instrument id. It is only ever used as a hypothesis to reject:
    an instrument whose claim the witness does not support is refused outright rather than
    quietly downgraded to the provable number. No claim ever becomes an asserted value.
    """
    label, resolved_cart = resolve_cart(cart)
    policy = _policy(criterion)
    manifests = _selected(instruments, resolved_cart, as_of)
    evaluation = evaluate(
        cart=resolved_cart,
        manifests=manifests,
        now=as_of,
        policy=policy,
        keys={ISSUER_KEY_ID: ISSUER_KEY},
        claims=dict(claims or {}),
    )
    return _evaluation_payload(evaluation, cart_label=label, as_of=as_of)


def tool_explain_derivation(
    *,
    instrument: str,
    cart: Any,
    as_of: int = DEFAULT_AS_OF,
    **_: Any,
) -> dict[str, Any]:
    """The human-readable allocation for one instrument on one cart.

    Shows which benefit attached to which line, and — the half a bare number cannot answer —
    which declared benefits were considered and yielded nothing, each with the reason:
    displaced inside an exclusivity group, out of capacity headroom, balance exhausted, not
    enrolled, or admitting no line of this cart.
    """
    label, resolved_cart = resolve_cart(cart)
    signed = resolve_instrument(instrument, as_of)
    evaluation = evaluate(
        cart=resolved_cart,
        manifests=[signed],
        now=as_of,
        keys={ISSUER_KEY_ID: ISSUER_KEY},
    )
    v = evaluation.valuation(signed.manifest.manifest_id)
    if v is None:  # pragma: no cover - evaluate always returns a record per manifest
        raise ToolError(
            MCP_ERR_NOT_EVALUATED,
            f"{signed.manifest.manifest_id} produced no valuation record",
            "this is an evaluator defect; re-run with the same clock and report it",
        )

    currency = resolved_cart.currency
    lines_by_sku = {line.sku: line for line in resolved_cart.lines}
    attached: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for node in (v.derivation.children if v.derivation is not None else ()):
        if node.status == BENEFIT_APPLIED:
            for child in node.children:
                sku = str(child.facts.get("line_sku", ""))
                line = lines_by_sku.get(sku)
                attached.append(
                    {
                        "benefit_id": str(node.facts.get("benefit_id", "")),
                        "benefit_label": node.label,
                        "benefit_kind": str(node.facts.get("benefit_kind", "")),
                        "line_sku": sku,
                        "line_description": line.description if line else sku,
                        "line_amount_minor": line.amount if line else None,
                        "value_minor": child.value_minor,
                        "value_display": fmt_currency(child.value_minor, currency),
                        "explanation": child.label,
                    }
                )
            continue
        blocked.append(
            {
                "benefit_id": str(node.facts.get("benefit_id", "")),
                "benefit_label": node.label,
                "benefit_kind": str(node.facts.get("benefit_kind", "")),
                "status": node.status,
                "reason_code": _benefit_block_reason(node),
                "detail": node.detail,
                "exclusivity_group": node.facts.get("exclusivity_group"),
                "capacity_minor": node.facts.get("capacity_minor"),
                "window": node.facts.get("window"),
            }
        )

    return {
        "ok": True,
        "as_of": as_of,
        "instrument_id": v.manifest_id,
        "issuer": v.issuer,
        "product": v.product,
        "cart": {
            "label": label,
            "hash": v.cart_hash,
            "currency": currency,
            "total_minor": resolved_cart.total(),
            "total_display": fmt_currency(resolved_cart.total(), currency),
        },
        "status": v.status,
        "asserted_value_minor": v.asserted_minor,
        "asserted_value_display": (
            None if v.asserted_minor is None else fmt_currency(v.asserted_minor, currency)
        ),
        "naive_per_line_sum_minor": v.naive_sum_minor,
        "naive_per_line_sum_display": fmt_currency(v.naive_sum_minor, currency),
        "overstatement_avoided_minor": v.overstatement_avoided_minor(),
        "witness_content_hash": (
            witness_content_hash(v.witness, currency=currency)
            if v.witness is not None
            else ""
        ),
        "witness_status": _witness_status(v),
        "witness": v.witness.to_dict(currency=currency) if v.witness else None,
        "verification": (
            v.verification.to_dict(currency=currency) if v.verification else None
        ),
        "attached": attached,
        "blocked": blocked,
        "derivation_lines": (
            v.derivation.render_lines(currency=currency) if v.derivation else []
        ),
        "considered_but_unpriced": [
            {"benefit_id": b.benefit_id, "label": b.label, "rationale": b.note}
            for b in signed.manifest.unpriced()
        ],
        "disclosures": [d.to_dict() for d in v.disclosures],
        "refusals": [r.to_dict() for r in v.refusals],
        "notes": [CONSERVATISM_NOTE, UNPRICED_NOTE],
    }


# --------------------------------------------------------------------------------------
# Tool registry. One definition drives the SDK server, the fallback server and the
# in-process client the agent uses on the replay path.
# --------------------------------------------------------------------------------------

_AS_OF_SCHEMA = {
    "type": "integer",
    "description": (
        "Evaluation clock, epoch seconds. Explicit so runs replay byte for byte; the "
        "system clock is never consulted. Defaults to the demo clock."
    ),
}

_CART_SCHEMA = {
    "description": (
        "Either the id of a named basket from list_carts, or an inline cart object "
        "{merchant, currency, lines:[{sku, description, amount_minor, mcc, category}]}. "
        "Money is integer minor units; there is no float path."
    ),
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "merchant": {"type": "string"},
                "currency": {"type": "string"},
                "lines": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["lines"],
        },
    ],
}

TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "list_instruments",
        "description": (
            "List every payment instrument this server can value, with issuer, product, "
            "currency, published annual fee, benefit counts, manifest content hash and the "
            "status of the issuer's signature over that manifest."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"as_of": _AS_OF_SCHEMA},
            "required": [],
        },
    },
    {
        "name": "list_carts",
        "description": (
            "List the named shopping baskets available, with merchant, currency, line count "
            "and total. Use the returned cart_id with value_cart or explain_derivation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"as_of": _AS_OF_SCHEMA},
            "required": [],
        },
    },
    {
        "name": "value_cart",
        "description": (
            "Value a cart on every candidate instrument. Returns, per instrument: the "
            "asserted value in integer minor units, the FULL line-item derivation, the "
            "witness content hash, the verification status, what a naive per-line summation "
            "would have overstated by, and the benefits declared but deliberately unpriced. "
            "Also returns the ranking the Card Member's valuation policy produces, with that "
            "policy's hash. Every number is computed by a deterministic allocator and "
            "independently verified — do not compute value yourself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cart": _CART_SCHEMA,
                "instruments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Instrument ids to consider. Omit to consider EVERY instrument in "
                        "the cart's currency — omission from a candidate set is the attack "
                        "this system exists to make visible, so the default is everything."
                    ),
                },
                "criterion": {
                    "type": "string",
                    "enum": list(CRITERIA),
                    "description": (
                        "The Card Member's ranking rule. A closed set: the criterion belongs "
                        "to the cardholder or their agent and is never issuer-endorsed."
                    ),
                },
                "as_of": _AS_OF_SCHEMA,
            },
            "required": ["cart"],
        },
    },
    {
        "name": "explain_derivation",
        "description": (
            "Explain one instrument's allocation on one cart in human-readable form: which "
            "benefit attached to which line and for how much, and which declared benefits "
            "were considered and yielded nothing — each with a reason code saying whether it "
            "was displaced inside an exclusivity group, out of capacity headroom, exhausted, "
            "not enrolled, or admitting no line of this cart."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instrument": {
                    "type": "string",
                    "description": "Instrument id, or a product name that matches exactly one.",
                },
                "cart": _CART_SCHEMA,
                "as_of": _AS_OF_SCHEMA,
            },
            "required": ["instrument", "cart"],
        },
    },
    {
        "name": "get_manifest",
        "description": (
            "Return the issuer-signed benefit manifest for one product: every declared "
            "benefit with its rate, remaining balance, cap, reset window and exclusivity "
            "group, plus the issuer's signature, the key that signed it and whether that "
            "signature verifies. The signature covers facts only, never a ranking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "description": "Instrument id, or a product name that matches exactly one.",
                },
                "as_of": _AS_OF_SCHEMA,
            },
            "required": ["product"],
        },
    },
)

HANDLERS: Mapping[str, Callable[..., dict[str, Any]]] = {
    "list_instruments": tool_list_instruments,
    "list_carts": tool_list_carts,
    "value_cart": tool_value_cart,
    "explain_derivation": tool_explain_derivation,
    "get_manifest": tool_get_manifest,
}

TOOL_NAMES: tuple[str, ...] = tuple(spec["name"] for spec in TOOL_SPECS)


def dispatch(name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run one tool by name. The single entry point every transport goes through.

    Never raises for a caller-side mistake: a `ToolError` is turned into a typed error
    document, because an agent on the other end of a wire can branch on a reason code and
    cannot branch on a stack trace.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolError(
            MCP_ERR_UNKNOWN_TOOL,
            f"no tool named {name!r}; this server exposes {', '.join(TOOL_NAMES)}",
            "call tools/list and use one of the names it returns",
        ).to_dict()
    try:
        return handler(**dict(arguments or {}))
    except ToolError as exc:
        return exc.to_dict()
    except TypeError as exc:
        return ToolError(MCP_ERR_BAD_ARGUMENTS, str(exc)).to_dict()
    except (EvaluationError, ValueError, KeyError) as exc:
        return ToolError(MCP_ERR_BAD_ARGUMENTS, f"{type(exc).__name__}: {exc}").to_dict()


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------


def sdk_available() -> bool:
    try:
        import mcp  # noqa: F401
        from mcp.server.lowlevel import Server  # noqa: F401
    except Exception:
        return False
    return True


def sdk_status() -> str:
    return (
        "official Python MCP SDK (`mcp`) is importable and will serve stdio"
        if sdk_available()
        else "official Python MCP SDK is NOT installed; the built-in JSON-RPC stdio "
        "implementation of the same wire format will serve instead"
    )


def _serve_stdio_sdk() -> None:
    """Serve over stdio using the official SDK's low-level Server."""
    import anyio
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    server: Any = Server(
        SERVER_NAME, version=SERVER_VERSION, instructions=SERVER_INSTRUCTIONS
    )

    @server.list_tools()
    async def _list_tools() -> list[Any]:
        return [
            types.Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in TOOL_SPECS
        ]

    # validate_input is left on: a malformed tool call should be rejected by the schema the
    # server published rather than by a Python exception inside the evaluator.
    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        result = dispatch(name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )

    anyio.run(_run)


def _serve_stdio_builtin(
    stdin: Any | None = None, stdout: Any | None = None
) -> None:
    """The MCP stdio wire format, implemented directly.

    Newline-delimited JSON-RPC 2.0 on stdin/stdout. Reached only when the official SDK is
    not importable. It exists so a missing dependency cannot break the demo, and it handles
    exactly the methods a tool-using client needs: `initialize`, the `initialized`
    notification, `tools/list`, `tools/call` and `ping`.
    """
    reader = stdin or sys.stdin
    writer = stdout or sys.stdout

    def send(message: Mapping[str, Any]) -> None:
        writer.write(json.dumps(message, ensure_ascii=False) + "\n")
        writer.flush()

    for raw in reader:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        response = handle_jsonrpc(request)
        if response is not None:
            send(response)


def handle_jsonrpc(request: Mapping[str, Any]) -> dict[str, Any] | None:
    """One JSON-RPC request in, one response out. None for a notification.

    Split out from the read loop so it is testable without a subprocess.
    """
    method = str(request.get("method", ""))
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": list(TOOL_SPECS)}}
    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        result = dispatch(name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                ],
                "isError": not result.get("ok", False),
            },
        }
    if req_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve_stdio() -> None:
    """Serve the tool surface over MCP stdio, SDK if present and built-in otherwise."""
    if sdk_available():
        _serve_stdio_sdk()
    else:
        _serve_stdio_builtin()


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def selftest_lines(as_of: int = DEFAULT_AS_OF) -> list[str]:
    """A short, deterministic proof that the surface works, with no client required."""
    out = [f"plumbline MCP server {SERVER_VERSION}  —  {sdk_status()}", ""]
    out.append(f"tools ({len(TOOL_SPECS)}):")
    for spec in TOOL_SPECS:
        out.append(f"  {spec['name']}")
    out.append("")
    valued = dispatch("value_cart", {"cart": CART_EVERYDAY, "as_of": as_of})
    ranking = valued.get("ranking") or {}
    out.append(
        f"value_cart(cart={CART_EVERYDAY!r}) -> {valued['candidate_set_size']} candidates, "
        f"cart {valued['cart']['total_display']}"
    )
    for entry in ranking.get("entries", []):
        out.append(
            f"  {entry['rank']}. {entry['manifest_id']:<30} {entry['asserted_display']:>10}"
        )
    out.append(f"  policy {ranking.get('policy_hash', '')[:16]}…  issuer_endorsed=False")
    top = (ranking.get("entries") or [{}])[0].get("manifest_id", "")
    if top:
        explained = dispatch(
            "explain_derivation", {"instrument": top, "cart": CART_EVERYDAY, "as_of": as_of}
        )
        out.append("")
        out.append(f"explain_derivation(instrument={top!r}):")
        out.append(
            f"  witness {explained['witness_content_hash'][:16]}…  "
            f"{explained['witness_status']}"
        )
        for row in explained["attached"]:
            out.append(f"  + {row['explanation']}  {row['value_display']}")
        for row in explained["blocked"][:4]:
            out.append(f"  - {row['benefit_label']}  [{row['reason_code']}]")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m plumbline.mcp_server",
        description="Serve the PLUMBLINE evaluator over MCP stdio.",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run a deterministic sample call and print it, instead of serving",
    )
    parser.add_argument(
        "--print-tools", action="store_true", help="print the tool schemas as JSON and exit"
    )
    parser.add_argument("--as-of", type=int, default=DEFAULT_AS_OF, help="evaluation clock")
    args = parser.parse_args(argv)

    if args.print_tools:
        print(json.dumps({"tools": list(TOOL_SPECS)}, indent=2, ensure_ascii=False))
        return 0
    if args.selftest:
        print("\n".join(selftest_lines(args.as_of)))
        return 0

    # stderr, never stdout: stdout is the JSON-RPC channel and one stray byte on it
    # desynchronises the client.
    print(f"plumbline MCP server on stdio — {sdk_status()}", file=sys.stderr)
    serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
