"""Issuer-signed benefit manifests.

A manifest declares, per card product, the facts an agent needs to value a cart: earn
rates keyed by MCC or merchant, statement credits with remaining balance and reset window,
protections, and the caps that bound all of it.

What the issuer signs is deliberately narrow, and the narrowness is the point:

  SIGNED      facts — earn rates, protections, credit balances, caps, eligibility.
  NOT SIGNED  the valuation policy, the ranking, and any comparison between issuers.

The issuer never signs "we beat another card on this cart." The corpus therefore contains
no issuer-signed assertion that a competitor won, which is what keeps a disclosure rail
from becoming a churn engine pointed at its own book.

Two things are deliberately absent, and their absence is a design decision we state out
loud rather than hide:

  * No acceptance predicate. An issuer-signed field naming where its own instrument is
    refused is a machine-readable instruction to route away from that issuer, carrying the
    issuer's signature on the reason. Acceptance belongs in the agent's routing layer,
    from data the agent already holds.
  * No member-facing nudge surface. Manifests feed an agent's ranking, never a member's
    inbox. Benefit expense is recognised on use; a nudge product converts breakage into
    recognised expense, which is the opposite of what this exists to do.

Non-numeric value (service, membership, brand) is carried as CONSIDERED_BUT_UNPRICED so a
receipt can prove the agent saw it while the integer never claims to be the whole worth of
the card.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from caveat.cart import Cart, CartLine
from caveat.money import fmt_currency

MANIFEST_VERSION = "plumbline/manifest/1"

# Benefit kinds.
KIND_EARN = "earn"  # multiplier on qualifying spend
KIND_CREDIT = "credit"  # statement credit drawn against a remaining balance
KIND_PROTECTION = "protection"  # fixed-value cover attached to qualifying spend
KIND_UNPRICED = "unpriced"  # declared, considered, never scored

PRICED_KINDS = (KIND_EARN, KIND_CREDIT, KIND_PROTECTION)

# Reset windows, for display and for state overlays. The allocator only reads the
# remaining balance; it never computes calendar logic itself.
WINDOW_MONTHLY = "monthly"
WINDOW_QUARTERLY = "quarterly"
WINDOW_SEMIANNUAL = "semiannual"
WINDOW_ANNUAL = "annual"
WINDOW_NONE = "none"


class ManifestError(ValueError):
    """Raised when a manifest is structurally invalid. Never raised for low value."""


@dataclass(frozen=True)
class Eligibility:
    """Which cart lines a benefit may attach to. Empty tuple means "no restriction"."""

    mccs: tuple[int, ...] = ()
    merchants: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()

    def admits(self, line: CartLine, merchant: str) -> bool:
        if self.mccs and line.mcc not in self.mccs:
            return False
        if self.merchants and merchant not in self.merchants:
            return False
        if self.categories and line.category not in self.categories:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mccs": list(self.mccs),
            "merchants": list(self.merchants),
            "categories": list(self.categories),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Eligibility":
        d = d or {}
        return cls(
            mccs=tuple(int(x) for x in d.get("mccs", ())),
            merchants=tuple(str(x) for x in d.get("merchants", ())),
            categories=tuple(str(x) for x in d.get("categories", ())),
        )


@dataclass(frozen=True)
class Benefit:
    """One declared benefit.

    `capacity_minor` is the hard ceiling on value this benefit can still yield: a credit's
    remaining balance, or the value headroom left under an annual earn cap. None means
    uncapped. The allocator treats it as a resource and never exceeds it — that is what
    makes double-counting structurally impossible rather than merely discouraged.

    `exclusivity_group` names a set of benefits that may not both attach to the same cart
    line. Two dining credits competing for one dinner is the canonical case, and it is
    exactly what naive per-line summation gets wrong.

    Capacity for an earn benefit is denominated in VALUE, not in qualifying spend. A term
    sheet that reads "5x on the first 50,000 of travel" is converted by the manifest author
    to a value headroom of 50_000 * rate_bp // 10_000. The conversion is exact for a single
    rate, and an earn benefit is never applied fractionally, so a line whose earn exceeds
    the remaining headroom is dropped rather than clipped. That understates and is safe.
    """

    benefit_id: str
    kind: str
    label: str
    eligibility: Eligibility = field(default_factory=Eligibility)
    # earn: basis points of qualifying spend returned as value (500 bp = 5x on a 1% base).
    rate_bp: int = 0
    # credit / protection: value available, in minor units.
    capacity_minor: int | None = None
    # protection: value granted per qualifying line, independent of amount.
    flat_minor: int = 0
    exclusivity_group: str | None = None
    window: str = WINDOW_NONE
    requires_enrollment: bool = False
    enrolled: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        # Negative magnitudes are rejected at construction rather than clamped downstream.
        # A negative rate would make naive_sum smaller than the witness it is supposed to
        # bound, which would silently invalidate the overstatement demonstration.
        if self.kind not in (*PRICED_KINDS, KIND_UNPRICED):
            raise ManifestError(
                f"unknown benefit kind {self.kind!r} on {self.benefit_id!r}; "
                f"expected one of {(*PRICED_KINDS, KIND_UNPRICED)}"
            )
        if self.rate_bp < 0:
            raise ManifestError(f"{self.benefit_id!r}: rate_bp must be >= 0, got {self.rate_bp}")
        if self.flat_minor < 0:
            raise ManifestError(
                f"{self.benefit_id!r}: flat_minor must be >= 0, got {self.flat_minor}"
            )
        if self.capacity_minor is not None and self.capacity_minor < 0:
            raise ManifestError(
                f"{self.benefit_id!r}: capacity_minor must be >= 0 or None (uncapped), "
                f"got {self.capacity_minor}"
            )

    def is_priced(self) -> bool:
        return self.kind in PRICED_KINDS

    def available(self) -> bool:
        """Enrollment-gated benefits that are not enrolled yield nothing."""
        if self.requires_enrollment and not self.enrolled:
            return False
        if self.capacity_minor is not None and self.capacity_minor <= 0:
            return False
        return True

    def value_for_line(self, line: CartLine, merchant: str) -> int:
        """Uncapped value this benefit would yield on one line, in minor units.

        Capacity is applied by the allocator, not here — a benefit does not know how much
        of itself earlier lines already consumed.
        """
        if not self.is_priced() or not self.available():
            return 0
        if not self.eligibility.admits(line, merchant):
            return 0
        if self.kind == KIND_EARN:
            return (line.amount * self.rate_bp) // 10_000
        if self.kind == KIND_CREDIT:
            # A credit offsets spend up to its remaining balance.
            return line.amount
        if self.kind == KIND_PROTECTION:
            return self.flat_minor
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "benefit_id": self.benefit_id,
            "kind": self.kind,
            "label": self.label,
            "eligibility": self.eligibility.to_dict(),
            "rate_bp": self.rate_bp,
            "capacity_minor": self.capacity_minor,
            "flat_minor": self.flat_minor,
            "exclusivity_group": self.exclusivity_group,
            "window": self.window,
            "requires_enrollment": self.requires_enrollment,
            "enrolled": self.enrolled,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Benefit":
        kind = str(d.get("kind", ""))
        cap = d.get("capacity_minor")
        group = d.get("exclusivity_group")
        try:
            benefit_id = str(d["benefit_id"])
        except KeyError as exc:
            raise ManifestError(f"benefit missing required field {exc}") from exc
        return cls(
            benefit_id=benefit_id,
            kind=kind,
            label=str(d.get("label", "")),
            eligibility=Eligibility.from_dict(d.get("eligibility")),
            rate_bp=int(d.get("rate_bp", 0)),
            capacity_minor=None if cap is None else int(cap),
            flat_minor=int(d.get("flat_minor", 0)),
            exclusivity_group=None if group is None else str(group),
            window=str(d.get("window", WINDOW_NONE)),
            requires_enrollment=bool(d.get("requires_enrollment", False)),
            enrolled=bool(d.get("enrolled", True)),
            note=str(d.get("note", "")),
        )

    def describe(self, *, currency: str) -> str:
        """One line for a human. `currency` is required and keyword-only.

        A benefit does not carry a currency; the manifest that holds it does. Defaulting a
        symbol here would describe a USD credit in rupees with the integer intact, which is
        the failure mode that reads as correct.
        """
        if self.kind == KIND_EARN:
            base = f"{self.rate_bp / 100:g}% back"
        elif self.kind == KIND_CREDIT:
            base = f"credit, {fmt_currency(self.capacity_minor or 0, currency)} remaining"
        elif self.kind == KIND_PROTECTION:
            base = f"cover worth {fmt_currency(self.flat_minor, currency)}"
        else:
            base = "declared, not priced"
        return f"{self.label} ({base})"


@dataclass(frozen=True)
class Manifest:
    """One card product's declared facts, as published by its issuer."""

    manifest_id: str
    issuer: str
    product: str
    currency: str
    benefits: tuple[Benefit, ...]
    issued_at: int
    version: str = MANIFEST_VERSION
    source: str = "publicly published card terms, modelled"

    def priced(self) -> tuple[Benefit, ...]:
        return tuple(b for b in self.benefits if b.is_priced() and b.available())

    def unpriced(self) -> tuple[Benefit, ...]:
        return tuple(b for b in self.benefits if b.kind == KIND_UNPRICED)

    def body(self) -> dict[str, Any]:
        """The exact object that gets signed."""
        return {
            "version": self.version,
            "manifest_id": self.manifest_id,
            "issuer": self.issuer,
            "product": self.product,
            "currency": self.currency,
            "issued_at": self.issued_at,
            "source": self.source,
            "benefits": [b.to_dict() for b in self.benefits],
        }

    def canonical(self) -> bytes:
        return canonical_json(self.body())

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Manifest":
        try:
            return cls(
                manifest_id=str(d["manifest_id"]),
                issuer=str(d["issuer"]),
                product=str(d["product"]),
                currency=str(d.get("currency", "INR")),
                benefits=tuple(Benefit.from_dict(b) for b in d.get("benefits", [])),
                issued_at=int(d["issued_at"]),
                version=str(d.get("version", MANIFEST_VERSION)),
                source=str(d.get("source", "publicly published card terms, modelled")),
            )
        except KeyError as exc:
            raise ManifestError(f"manifest missing required field {exc}") from exc


@dataclass(frozen=True)
class SignedManifest:
    """A manifest plus a detached signature over its canonical bytes."""

    manifest: Manifest
    signature: str
    key_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "body": self.manifest.body(),
            "signature": self.signature,
            "key_id": self.key_id,
            "content_hash": self.manifest.content_hash(),
        }


def canonical_json(payload: Any) -> bytes:
    """Stable bytes for signing. Sorted keys, tight separators, UTF-8, no escaping.

    Key insertion order therefore cannot change the bytes. Array order can and must — a
    manifest that lists its benefits in a different order is a different document, and the
    signature is over the document.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign_manifest(manifest: Manifest, key: str | bytes, key_id: str = "prototype") -> SignedManifest:
    """Sign a manifest.

    HMAC-SHA256 under a shared secret in this prototype; a production deployment signs with
    the issuer's key in an HSM. Canonicalisation and the verification flow are unchanged,
    which is the part that matters for the argument.
    """
    material = key.encode("utf-8") if isinstance(key, str) else key
    signature = hmac.new(material, manifest.canonical(), hashlib.sha256).hexdigest()
    return SignedManifest(manifest=manifest, signature=signature, key_id=key_id)


def verify_manifest(signed: SignedManifest | dict[str, Any], key: str | bytes) -> bool:
    """Recompute the signature over canonical bytes. Constant-time comparison."""
    if isinstance(signed, dict):
        body = signed.get("body", {})
        signature = str(signed.get("signature", ""))
        raw = canonical_json(body)
    else:
        raw = signed.manifest.canonical()
        signature = signed.signature
    material = key.encode("utf-8") if isinstance(key, str) else key
    expected = hmac.new(material, raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def eligible_benefits(manifest: Manifest, cart: Cart) -> tuple[Benefit, ...]:
    """Priced, available benefits that admit at least one line of this cart."""
    out = []
    for b in manifest.priced():
        if any(b.eligibility.admits(line, cart.merchant) for line in cart.lines):
            out.append(b)
    return tuple(out)


def build_manifest(
    *,
    manifest_id: str,
    issuer: str,
    product: str,
    benefits: Iterable[Benefit],
    issued_at: int,
    currency: str = "INR",
    source: str = "publicly published card terms, modelled",
) -> Manifest:
    seen: set[str] = set()
    ordered: list[Benefit] = []
    for b in benefits:
        if not b.benefit_id:
            raise ManifestError("every benefit needs a non-empty benefit_id; a witness "
                                "references benefits by id and cannot address an empty one")
        if b.benefit_id in seen:
            raise ManifestError(
                f"duplicate benefit_id {b.benefit_id!r}; ids address capacity buckets, so "
                f"two benefits sharing one id would share a capacity pool by accident"
            )
        seen.add(b.benefit_id)
        ordered.append(b)
    if not ordered:
        raise ManifestError("a manifest with no benefits declares nothing")
    return Manifest(
        manifest_id=manifest_id,
        issuer=issuer,
        product=product,
        currency=currency,
        benefits=tuple(ordered),
        issued_at=issued_at,
        source=source,
    )
