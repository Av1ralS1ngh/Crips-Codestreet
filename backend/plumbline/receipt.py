"""The Decision Receipt: the signed, contestable record of an instrument choice.

An agentic checkout today records *which* instrument was used. AP2's Payment Mandate is
explicit that "the Mandate selection mechanism is outside the scope of this specification."
This module records *why*, in a form a third party can check without trusting whoever
produced it.

`evaluate.py` answers what a cart is worth on each card and in what order they rank. This
module is the envelope: it fixes that answer against a checkout session and a mandate,
records the full candidate set, states what was signed by whom, and hands the result to the
transparency log. The division matters — the valuation is a computation, the receipt is an
artifact that outlives it.

WHAT MAKES THIS A RECEIPT RATHER THAN A LOG LINE

  The full candidate set, not the winner. **Omission is the attack.** An agent that
  silently drops one issuer from consideration produces a perfectly well-formed record of a
  choice among the instruments it decided to mention, and nothing inside such a record
  reveals what is missing. So every instrument the Card Member's mandate authorised gets an
  entry — including the refused ones, which carry no number — and the set is digested into
  a single hash that moves if any member is removed.

  A witness hash per instrument, not a bare number. An asserted value is only as good as
  the concrete allocation exhibited to justify it (see witness.py). The receipt carries the
  content hash of that allocation and its verification status, so "show me the derivation"
  is answerable and "this was verified" is checkable rather than claimed.

  A stated decision criterion. A ranking with no stated criterion cannot be audited: every
  order is consistent with an unstated rule. The criterion is named and the recorded order
  is checkable against the recorded numbers.

  CONSIDERED_BUT_UNPRICED entries. Service, membership, protections whose worth is not an
  integer. Recording them is how the receipt proves the agent saw non-numeric value, and
  how the integer avoids claiming to be the whole worth of a card.

THE SIGNING BOUNDARY, ENFORCED HERE AND NOT MERELY DESCRIBED

  The issuer signs FACTS: earn rates, protections, credit balances, caps. That signature
  lives on the manifest, covers the manifest's canonical bytes, and travels inside each
  candidate record unchanged and separately verifiable.

  The RANKING and the VALUATION POLICY belong to the Card Member or their agent. Their
  hashes are recorded; they are NOT issuer-endorsed. No issuer signs a comparison between
  instruments, so the corpus contains no issuer-signed assertion that a competitor won.

  `issuer_sign_facts` is the only issuer-signing entry point, and it refuses anything that
  is not a manifest — by type, then by a recursive scan for ranking vocabulary.
  `sign_receipt` refuses the issuer role outright. Producing an issuer signature over a
  ranking is not discouraged here; there is no function that will do it.

DEFAULT POSTURE IS OBSERVE-ONLY

  The receipt obligation is a caveat on the mandate the Card Member issues to their OWN
  agent. It is not a condition anyone imposes on a platform; the platform is asked for
  nothing and never sees the check. So when a counterpart receipt is absent the transaction
  PROCEEDS and the gap is recorded as an unattested selection. Enforcement — the agent
  failing to discharge the Card Member's own delegated authority, architecturally identical
  to a spend limit denying — is a mode the Card Member elects.

COMPLIANCE SYMMETRY

  `attest_ranking` says "faithful" with the same machinery it uses to say "deviated". A
  receipt that can only catch cheating is something a platform routes around; one that can
  certify "this platform ranked instruments faithfully, over the full set the Card Member
  authorised" is something a platform wants to display.

Honest limitations:
  * Signatures are HMAC-SHA256 under prototype keys. Production signs with the issuer's HSM
    key and the agent's own key; canonicalisation and the verification flow are unchanged,
    and the signing boundary is structural rather than key-dependent.
  * The attestation is a check of the recorded ranking against the recorded numbers. It is
    not evidence that the agent reported facts honestly about instruments it alone saw.
    Detecting a doctored candidate set is the transparency log's job, not this one's.
  * `authorized_instrument_ids` derives its authority from the Card Member's mandate.
    Recorded here so omission is checkable, it is not an assertion the agent gets to make.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from caveat.cart import Cart
from caveat.money import fmt_currency

from .authoring import scan_for_acceptance_predicate
from .evaluate import (
    AUTHOR_AGENT,
    AUTHOR_CARDHOLDER,
    CRITERIA,
    CRITERION_MAX_PROTECTION_THEN_VALUE,
    STATUS_ATTESTED,
    STATUS_REFUSED,
    Evaluation,
    EvaluationError,
    InstrumentValuation,
    RankedInstrument,
    Ranking,
    ValuationPolicy,
)
from .manifest import (
    MANIFEST_VERSION,
    Benefit,
    Manifest,
    SignedManifest,
    canonical_json,
    verify_manifest,
)
from .transparency import (
    ENTRY_RECEIPT,
    ENTRY_UNATTESTED_SELECTION,
    InclusionProof,
    SignedTreeHead,
    TransparencyLog,
    hash_leaf,
    leaf_data_for,
)
from .witness import Verification, Witness

RECEIPT_VERSION = "plumbline/receipt/1"
SIGNATURE_ALG = "HMAC-SHA256"

# --------------------------------------------------------------------------------------
# Roles. The issuer role exists in this module only so that it can be refused.
# --------------------------------------------------------------------------------------

ROLE_ISSUER = "issuer"
ROLE_AGENT = AUTHOR_AGENT
ROLE_CARDHOLDER = AUTHOR_CARDHOLDER
ROLE_PLATFORM = "platform"

RECEIPT_SIGNER_ROLES = (ROLE_AGENT, ROLE_CARDHOLDER)

# Keys that must never appear inside anything an issuer signs. One of these in an
# issuer-signed body means the signature would cover a comparison between instruments.
RANKING_VOCABULARY = frozenset(
    {
        "ranking",
        "ranked",
        "rank",
        "entries",
        "ordered_instrument_ids",
        "chosen_instrument_id",
        "chosen_manifest_id",
        "chosen",
        "criterion",
        "comparison",
        "candidates",
        "candidate_set",
        "valuation_policy",
        "policy_hash",
        "policy_id",
        "baseline_minor",
        "baseline_earn_bp",
        "margin_minor",
        "margin_over_next_minor",
        "beats",
        "beat",
        "winner",
        "won",
        "wins",
        "loser",
        "best",
        "top_instrument",
        "preferred_instrument",
        "recommendation",
        "recommended",
        "preference",
        "score",
        "attestation",
    }
)

ISSUER_ENDORSEMENT_NOTE = (
    "none — the issuer signs facts only. The ranking and the valuation policy are the "
    "Card Member's or their agent's, recorded here by hash and endorsed by no issuer."
)

UNPRICED_DISCLOSURE = (
    "The asserted value is an integer over priced benefits only. Entries under "
    "considered_but_unpriced were seen and deliberately not scored; the integer does not "
    "claim to be the whole worth of any instrument."
)

# --------------------------------------------------------------------------------------
# Witness status on a candidate. A refusal is a first-class outcome, not an error.
# --------------------------------------------------------------------------------------

WITNESS_VERIFIED = "WITNESS_VERIFIED"
WITNESS_REFUSED = "VALUATION_REFUSED_NO_WITNESS_SUPPORTS_A_VALUE"
WITNESS_ABSENT = "NO_WITNESS_PRODUCED"

WITNESS_STATUSES = (WITNESS_VERIFIED, WITNESS_REFUSED, WITNESS_ABSENT)

# --------------------------------------------------------------------------------------
# Posture — the graceful-degrade path.
# --------------------------------------------------------------------------------------

POSTURE_OBSERVE_ONLY = "observe_only"
POSTURE_ENFORCE = "enforce"

POSTURES = (POSTURE_OBSERVE_ONLY, POSTURE_ENFORCE)

REASON_SELECTION_ATTESTED = "SELECTION_ATTESTED"
REASON_UNATTESTED_SELECTION = "UNATTESTED_SELECTION"
REASON_DISCLOSURE_CAVEAT_UNDISCHARGED = "DISCLOSURE_CAVEAT_UNDISCHARGED"

# --------------------------------------------------------------------------------------
# Attestation codes — compliance symmetry.
# --------------------------------------------------------------------------------------

ATTEST_FAITHFUL = "RANKING_FAITHFUL"
ATTEST_CANDIDATE_SET_INCOMPLETE = "CANDIDATE_SET_INCOMPLETE"
ATTEST_CHOSEN_NOT_AUTHORIZED = "CHOSEN_INSTRUMENT_NOT_AUTHORIZED_BY_THE_MANDATE"
ATTEST_CHOSEN_NOT_A_CANDIDATE = "CHOSEN_INSTRUMENT_NOT_IN_CANDIDATE_SET"
ATTEST_DEVIATED = "CHOICE_DEVIATES_FROM_STATED_CRITERION"
ATTEST_ORDER_INCONSISTENT = "RANKING_ORDER_INCONSISTENT_WITH_CRITERION"
ATTEST_RANKED_SET_WRONG = "RANKED_SET_DOES_NOT_MATCH_ATTESTED_CANDIDATES"
ATTEST_CHOSEN_UNVERIFIED = "CHOSEN_INSTRUMENT_WITNESS_UNVERIFIED"
ATTEST_UNKNOWN_CRITERION = "STATED_CRITERION_NOT_RECOMPUTABLE"
ATTEST_NO_RANKING = "NO_RANKING_NOTHING_WAS_ATTESTABLE"

# Faults, worst first. The headline outcome is the most serious fault present.
# ATTEST_NO_RANKING is deliberately absent: an agent that considered everything the mandate
# authorised and could not verify a witness for any of it behaved correctly. Recording that
# as a violation would punish the refusal this system exists to make possible.
ATTEST_SEVERITY = (
    ATTEST_CHOSEN_NOT_AUTHORIZED,
    ATTEST_CANDIDATE_SET_INCOMPLETE,
    ATTEST_CHOSEN_NOT_A_CANDIDATE,
    ATTEST_DEVIATED,
    ATTEST_ORDER_INCONSISTENT,
    ATTEST_RANKED_SET_WRONG,
    ATTEST_CHOSEN_UNVERIFIED,
    ATTEST_UNKNOWN_CRITERION,
)

# --------------------------------------------------------------------------------------
# Verification check names. A reviewer's report cites these.
# --------------------------------------------------------------------------------------

CHECK_STRUCTURE = "RECEIPT_STRUCTURE"
CHECK_SIGNATURE = "RECEIPT_SIGNATURE"
CHECK_SIGNER_NOT_ISSUER = "SIGNER_ROLE_IS_NOT_ISSUER"
CHECK_NO_ISSUER_SIGNED_RANKING = "NO_ISSUER_SIGNATURE_OVER_A_RANKING"
CHECK_CANDIDATE_DIGEST = "CANDIDATE_SET_DIGEST"
CHECK_CART_HASH = "CART_HASH_MATCHES"
CHECK_MANIFEST_SIGNATURES = "ISSUER_MANIFEST_SIGNATURES"
CHECK_WITNESS_HASHES = "WITNESS_CONTENT_HASHES"
CHECK_EVALUATION_AGREES = "CANDIDATES_AGREE_WITH_EMBEDDED_EVALUATION"
CHECK_RANKING = "RANKING_MATCHES_STATED_CRITERION"


class ReceiptError(ValueError):
    """Raised when a receipt cannot be built or parsed. Never raised for low value."""


class IssuerSigningBoundaryError(PermissionError):
    """Raised when an operation would place an issuer signature over a comparison.

    A dedicated type rather than a ValueError, because this is the one failure in the
    module that is a governance violation rather than a data problem.
    """


# --------------------------------------------------------------------------------------
# Hashing and signing primitives
# --------------------------------------------------------------------------------------


def content_hash(payload: Any) -> str:
    """SHA-256 over canonical JSON. The same function behind every hash on a receipt."""
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _key_bytes(key: str | bytes) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else key


def key_id(key: str | bytes) -> str:
    """A stable, non-reversible identifier for a signing key.

    Lets a receipt name the key that verifies it without carrying anything that helps forge
    a signature.
    """
    return hashlib.sha256(b"plumbline/receipt/key-id/v1" + _key_bytes(key)).hexdigest()[:16]


def _hmac_hex(body: Mapping[str, Any], key: str | bytes) -> str:
    return hmac.new(_key_bytes(key), canonical_json(body), hashlib.sha256).hexdigest()


def witness_content_hash(witness: Witness, *, currency: str) -> str:
    """The hash a receipt records in place of the allocation itself.

    `currency` is required because the serialized witness carries a rendered total, so the
    hash is only reproducible by someone who renders it the same way. Every caller holds
    either the manifest or the checkout session, both of which name it.
    """
    return content_hash(witness.to_dict(currency=currency))


# --------------------------------------------------------------------------------------
# The signing boundary
# --------------------------------------------------------------------------------------


def find_ranking_vocabulary(payload: Any, *, _path: str = "$") -> tuple[str, ...]:
    """Every path in `payload` whose key names a ranking concept.

    Recursive by design: burying a comparison three levels inside a nominally factual
    object must not get it signed.
    """
    hits: list[str] = []
    if isinstance(payload, Mapping):
        for k, v in payload.items():
            here = f"{_path}.{k}"
            if str(k).lower() in RANKING_VOCABULARY:
                hits.append(here)
            hits.extend(find_ranking_vocabulary(v, _path=here))
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            hits.extend(find_ranking_vocabulary(v, _path=f"{_path}[{i}]"))
    return tuple(hits)


def find_issuer_role_signatures(payload: Any, *, _path: str = "$") -> tuple[str, ...]:
    """Every signature block in a document that claims the issuer role.

    A receipt body contains a ranking, so an issuer-role signature over any part of it is a
    boundary violation whether or not the bytes happen to verify.
    """
    hits: list[str] = []
    if isinstance(payload, Mapping):
        if str(payload.get("role", "")) == ROLE_ISSUER and "value" in payload:
            hits.append(_path)
        for k, v in payload.items():
            hits.extend(find_issuer_role_signatures(v, _path=f"{_path}.{k}"))
    elif isinstance(payload, (list, tuple)):
        for i, v in enumerate(payload):
            hits.extend(find_issuer_role_signatures(v, _path=f"{_path}[{i}]"))
    return tuple(hits)


def issuer_signature_scope_violations(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Places in a receipt document where an issuer signature would cover a comparison.

    Two ways that can happen, both checked:

      * a candidate record carrying an issuer signature grows a ranking field, so the
        issuer's attestation of facts sits in an object that also states an order;
      * any signature block anywhere in the document claims the issuer role.
    """
    out: list[str] = []
    body = document.get("receipt", document)
    candidates = ((body.get("candidate_set") or {}) if isinstance(body, Mapping) else {}).get(
        "candidates", ()
    )
    for c in candidates:
        if not isinstance(c, Mapping):
            continue
        if not str((c.get("issuer_signature") or {}).get("value", "")):
            continue
        adjacent = {k: v for k, v in c.items() if k != "issuer_signature"}
        hits = find_ranking_vocabulary(adjacent)
        if hits:
            out.append(
                f"{c.get('instrument_id')} carries ranking content at {', '.join(hits)} "
                f"alongside an issuer signature"
            )
    out.extend(f"issuer-role signature at {p}" for p in find_issuer_role_signatures(document))
    return tuple(out)


def issuer_sign_facts(
    subject: Manifest | SignedManifest | Mapping[str, Any],
    *,
    key: str | bytes,
    key_reference: str = "issuer-prototype",
) -> SignedManifest:
    """The only issuer-signing entry point in this codebase.

    Accepts a manifest and nothing else. Two gates, both of which must pass:

      1. The subject must be a manifest — a `Manifest`, a `SignedManifest`, or a mapping
         carrying this package's manifest version marker. A receipt body, a ranking or a
         bare dict of numbers is refused by type.
      2. The manifest body is scanned recursively for ranking vocabulary. A manifest that
         somehow carries a comparison between instruments is refused even though it is
         structurally a manifest.

    There is deliberately no `force`, no `allow`, and no lower-level issuer signer to reach
    around this. An issuer signature over a comparison is not producible from this module.
    """
    if isinstance(subject, SignedManifest):
        manifest = subject.manifest
        submitted = manifest.body()
    elif isinstance(subject, Manifest):
        manifest = subject
        submitted = manifest.body()
    elif isinstance(subject, Mapping):
        version = str(subject.get("version", ""))
        if version != MANIFEST_VERSION:
            raise IssuerSigningBoundaryError(
                f"the issuer signs facts only. Refusing to sign an object whose version is "
                f"{version!r}; expected {MANIFEST_VERSION!r}. To sign a receipt or a "
                f"ranking, use sign_receipt with an agent or cardholder key — no issuer "
                f"key may cover a comparison between instruments."
            )
        submitted = dict(subject)
        manifest = Manifest.from_dict(submitted)
    else:
        raise IssuerSigningBoundaryError(
            f"the issuer signs facts only. Refusing to sign a {type(subject).__name__}; "
            f"pass a Manifest, a SignedManifest, or a manifest body."
        )

    body = manifest.body()
    # Scan what was SUBMITTED, not only what survives normalisation. Parsing a mapping into
    # a Manifest silently drops unknown keys, so scanning the normalised body alone would
    # let a caller believe a smuggled ranking had been signed when it had merely been
    # discarded. Refusing loudly is the only answer that leaves no ambiguity about scope.
    hits = tuple(dict.fromkeys(find_ranking_vocabulary(submitted) + find_ranking_vocabulary(body)))
    if hits:
        raise IssuerSigningBoundaryError(
            f"refusing to place an issuer signature over ranking content at "
            f"{', '.join(hits)}. The issuer signs earn rates, protections, balances and "
            f"caps. The ranking and the valuation policy belong to the Card Member; record "
            f"their hash on the receipt instead."
        )

    # Same argument, second forbidden category: an issuer-signed field naming where the
    # instrument is refused. `Manifest` has no such field so it could never reach the
    # signature, but silently discarding it would leave the caller believing an acceptance
    # predicate had been signed. The authoring validator screens drafts; this screens
    # anything that reached the signer without passing through it.
    refused = tuple(
        dict.fromkeys(
            f.path
            for f in scan_for_acceptance_predicate(submitted)
            + scan_for_acceptance_predicate(body)
        )
    )
    if refused:
        raise IssuerSigningBoundaryError(
            f"refusing to place an issuer signature over acceptance content at "
            f"{', '.join(refused)}. This schema has no acceptance predicate, deliberately: "
            f"an issuer-signed field naming where its own instrument is refused is a "
            f"machine-readable instruction to route away from it. Acceptance belongs in "
            f"the agent's routing layer, from data the agent already holds."
        )

    return SignedManifest(manifest=manifest, signature=_hmac_hex(body, key), key_id=key_reference)


# --------------------------------------------------------------------------------------
# Receipt components
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """Who acted, and on whose surface. Both recorded; neither vouched for here."""

    kind: str
    identifier: str
    name: str = ""
    key_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "name": self.name,
            "key_id": self.key_id,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Identity":
        return cls(
            kind=str(d["kind"]),
            identifier=str(d["identifier"]),
            name=str(d.get("name", "")),
            key_id=str(d.get("key_id", "")),
        )


@dataclass(frozen=True)
class CheckoutSession:
    """The checkout this decision belongs to, bound by hash to the cart it priced."""

    session_id: str
    merchant: str
    currency: str
    cart_hash: str
    cart_total_minor: int
    initiated_at: int

    @classmethod
    def of(cls, session_id: str, cart: Cart, initiated_at: int) -> "CheckoutSession":
        return cls(
            session_id=session_id,
            merchant=cart.merchant,
            currency=cart.currency,
            cart_hash=cart.hash(),
            cart_total_minor=cart.total(),
            initiated_at=initiated_at,
        )

    def body(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "merchant": self.merchant,
            "currency": self.currency,
            "cart_hash": self.cart_hash,
            "cart_total_minor": self.cart_total_minor,
            "initiated_at": self.initiated_at,
        }

    def session_hash(self) -> str:
        return content_hash(self.body())

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.body(),
            "session_hash": self.session_hash(),
            "cart_total_display": fmt_currency(self.cart_total_minor, self.currency),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CheckoutSession":
        return cls(
            session_id=str(d["session_id"]),
            merchant=str(d["merchant"]),
            currency=str(d.get("currency", "INR")),
            cart_hash=str(d["cart_hash"]),
            cart_total_minor=int(d["cart_total_minor"]),
            initiated_at=int(d["initiated_at"]),
        )


@dataclass(frozen=True)
class MandateBinding:
    """What the Card Member authorised, and what they asked their agent to prove.

    `authorized_instrument_ids` is the omission detector's reference set. Its authority
    comes from the mandate the Card Member issued, not from the agent's say-so — an agent
    that shortens this list is falsifying the mandate, not merely writing a thin receipt.
    """

    mandate_id: str
    authorized_instrument_ids: tuple[str, ...]
    disclosure_caveat: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mandate_id": self.mandate_id,
            "authorized_instrument_ids": sorted(self.authorized_instrument_ids),
            "disclosure_caveat": self.disclosure_caveat,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "MandateBinding":
        return cls(
            mandate_id=str(d["mandate_id"]),
            authorized_instrument_ids=tuple(
                str(i) for i in d.get("authorized_instrument_ids", ())
            ),
            disclosure_caveat=bool(d.get("disclosure_caveat", True)),
        )


@dataclass(frozen=True)
class UnpricedConsideration:
    """Value the agent saw and deliberately did not turn into an integer."""

    instrument_id: str
    benefit_id: str
    label: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "benefit_id": self.benefit_id,
            "label": self.label,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "UnpricedConsideration":
        return cls(
            instrument_id=str(d["instrument_id"]),
            benefit_id=str(d["benefit_id"]),
            label=str(d.get("label", "")),
            note=str(d.get("note", "")),
        )


@dataclass(frozen=True)
class CandidateRecord:
    """One instrument that was considered — losers and refusals included.

    `asserted_value_minor` is None exactly when nothing was asserted. That is a refusal and
    it is recorded as one: a system that declines to make a claim is more useful than one
    that emits a zero indistinguishable from a genuine zero.
    """

    instrument_id: str
    manifest_id: str
    issuer: str
    product: str
    manifest_content_hash: str
    status: str
    witness_status: str
    issuer_key_id: str = ""
    issuer_signature: str = ""
    asserted_value_minor: int | None = None
    protection_value_minor: int = 0
    witness_hash: str = ""
    realized_minor: int | None = None
    attestable_hash: str = ""
    failure_codes: tuple[str, ...] = ()
    refusal_codes: tuple[str, ...] = ()
    unpriced_benefit_ids: tuple[str, ...] = ()
    note: str = ""

    def attested(self) -> bool:
        return self.status == STATUS_ATTESTED and self.asserted_value_minor is not None

    def is_verified(self) -> bool:
        return self.witness_status == WITNESS_VERIFIED and self.attested()

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only.

        A candidate record holds an integer and a manifest id, never the manifest. The
        enclosing receipt's checkout session names the currency, and every candidate on one
        receipt is denominated in it — a candidate set spanning currencies would need an FX
        rate, which is a market price and not an issuer-signed fact.
        """
        return {
            "instrument_id": self.instrument_id,
            "manifest_id": self.manifest_id,
            "issuer": self.issuer,
            "product": self.product,
            "manifest_content_hash": self.manifest_content_hash,
            "issuer_signature": {
                "alg": SIGNATURE_ALG,
                "key_id": self.issuer_key_id,
                "value": self.issuer_signature,
                "covers": "the manifest body only — facts, never a comparison",
            },
            "status": self.status,
            "witness_status": self.witness_status,
            "asserted_value_minor": self.asserted_value_minor,
            "asserted_value_display": (
                None
                if self.asserted_value_minor is None
                else fmt_currency(self.asserted_value_minor, currency)
            ),
            "protection_value_minor": self.protection_value_minor,
            "witness_hash": self.witness_hash,
            "realized_minor": self.realized_minor,
            "attestable_hash": self.attestable_hash,
            "failure_codes": list(self.failure_codes),
            "refusal_codes": list(self.refusal_codes),
            "unpriced_benefit_ids": list(self.unpriced_benefit_ids),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CandidateRecord":
        sig = d.get("issuer_signature") or {}
        raw_value = d.get("asserted_value_minor")
        raw_realized = d.get("realized_minor")
        return cls(
            instrument_id=str(d["instrument_id"]),
            manifest_id=str(d.get("manifest_id", "")),
            issuer=str(d.get("issuer", "")),
            product=str(d.get("product", "")),
            manifest_content_hash=str(d.get("manifest_content_hash", "")),
            status=str(d["status"]),
            witness_status=str(d["witness_status"]),
            issuer_key_id=str(sig.get("key_id", "")),
            issuer_signature=str(sig.get("value", "")),
            asserted_value_minor=None if raw_value is None else int(raw_value),
            protection_value_minor=int(d.get("protection_value_minor", 0)),
            witness_hash=str(d.get("witness_hash", "")),
            realized_minor=None if raw_realized is None else int(raw_realized),
            attestable_hash=str(d.get("attestable_hash", "")),
            failure_codes=tuple(str(c) for c in d.get("failure_codes", ())),
            refusal_codes=tuple(str(c) for c in d.get("refusal_codes", ())),
            unpriced_benefit_ids=tuple(str(b) for b in d.get("unpriced_benefit_ids", ())),
            note=str(d.get("note", "")),
        )


def candidate_set_digest(candidates: Sequence[CandidateRecord]) -> str:
    """One hash over the whole candidate set.

    Dropping a candidate moves this value, which is what turns omission from an invisible
    edit into a visible one once the receipt is anchored in the transparency log.
    """
    return content_hash(
        [
            {
                "instrument_id": c.instrument_id,
                "manifest_content_hash": c.manifest_content_hash,
                "witness_hash": c.witness_hash,
                "status": c.status,
                "witness_status": c.witness_status,
                "asserted_value_minor": c.asserted_value_minor,
            }
            for c in sorted(candidates, key=lambda c: c.instrument_id)
        ]
    )


def candidate_record(
    *,
    instrument_id: str,
    signed_manifest: SignedManifest,
    witness: Witness | None,
    verification: Verification | None,
    protection_value_minor: int = 0,
    unpriced: Sequence[Benefit] = (),
    refusal_codes: Sequence[str] = (),
    note: str = "",
) -> CandidateRecord:
    """Turn one valuation outcome into a receipt entry, without going through evaluate().

    A value is asserted only when a witness verified and supports it. Anything else is
    recorded as a refusal carrying the verifier's own failure codes — the number is
    withheld, the consideration is not.
    """
    manifest = signed_manifest.manifest
    base: dict[str, Any] = dict(
        instrument_id=instrument_id,
        manifest_id=manifest.manifest_id,
        issuer=manifest.issuer,
        product=manifest.product,
        manifest_content_hash=manifest.content_hash(),
        issuer_key_id=signed_manifest.key_id,
        issuer_signature=signed_manifest.signature,
        protection_value_minor=protection_value_minor,
        unpriced_benefit_ids=tuple(b.benefit_id for b in unpriced),
        refusal_codes=tuple(refusal_codes),
        note=note,
    )

    if witness is None or verification is None:
        return CandidateRecord(
            status=STATUS_REFUSED, witness_status=WITNESS_ABSENT, **base
        )
    if verification.supports_assertion:
        return CandidateRecord(
            status=STATUS_ATTESTED,
            witness_status=WITNESS_VERIFIED,
            asserted_value_minor=verification.asserted_minor,
            witness_hash=witness_content_hash(witness, currency=manifest.currency),
            realized_minor=verification.realized_minor,
            **base,
        )
    return CandidateRecord(
        status=STATUS_REFUSED,
        witness_status=WITNESS_REFUSED,
        witness_hash=witness_content_hash(witness, currency=manifest.currency),
        realized_minor=verification.realized_minor,
        failure_codes=tuple(f.code for f in verification.failures),
        **base,
    )


def candidate_from_valuation(
    valuation: InstrumentValuation,
    *,
    instrument_id: str | None = None,
    signed_manifest: SignedManifest | None = None,
) -> CandidateRecord:
    """Project one `evaluate.InstrumentValuation` into a receipt candidate record.

    `instrument_id` defaults to the manifest id: in this prototype one manifest models one
    instrument. A deployment holding two cards on the same product gives each a distinct
    instrument id and they share a manifest content hash.

    `unpriced_benefit_ids` is populated from the manifest, not from the evaluation, because
    `InstrumentValuation.unpriced_labels` carries display labels, as its name says. Without
    a manifest the field is left empty and the note says so rather than putting labels in an
    id field.
    """
    verified = bool(valuation.verification and valuation.verification.supports_assertion)
    if valuation.witness is None:
        witness_status = WITNESS_ABSENT
    elif verified and valuation.attested:
        witness_status = WITNESS_VERIFIED
    else:
        witness_status = WITNESS_REFUSED

    return CandidateRecord(
        instrument_id=instrument_id or valuation.manifest_id,
        manifest_id=valuation.manifest_id,
        issuer=valuation.issuer,
        product=valuation.product,
        manifest_content_hash=valuation.manifest_hash,
        status=valuation.status,
        witness_status=witness_status,
        issuer_key_id=signed_manifest.key_id if signed_manifest else "",
        issuer_signature=signed_manifest.signature if signed_manifest else "",
        asserted_value_minor=valuation.asserted_minor,
        protection_value_minor=valuation.protection_value_minor,
        witness_hash=(
            witness_content_hash(valuation.witness, currency=valuation.currency)
            if valuation.witness is not None
            else ""
        ),
        realized_minor=(
            valuation.verification.realized_minor if valuation.verification else None
        ),
        attestable_hash=valuation.attestable_hash(),
        failure_codes=(
            tuple(f.code for f in valuation.verification.failures)
            if valuation.verification
            else ()
        ),
        refusal_codes=tuple(r.code for r in valuation.refusals),
        unpriced_benefit_ids=(
            tuple(b.benefit_id for b in signed_manifest.manifest.unpriced())
            if signed_manifest is not None
            else ()
        ),
        note=(
            ""
            if signed_manifest is not None or not valuation.unpriced_labels
            else (
                f"{len(valuation.unpriced_labels)} unpriced benefit(s) were considered; their ids "
                f"are absent because no manifest was supplied to the receipt builder"
            )
        ),
    )


def unpriced_considerations(
    instrument_id: str, manifest: Manifest
) -> tuple[UnpricedConsideration, ...]:
    """Every declared-but-unscored benefit on one instrument, in manifest order."""
    return tuple(
        UnpricedConsideration(
            instrument_id=instrument_id,
            benefit_id=b.benefit_id,
            label=b.label,
            note=b.note,
        )
        for b in manifest.unpriced()
    )


# --------------------------------------------------------------------------------------
# Recomputing a ranking from a receipt
# --------------------------------------------------------------------------------------


def criterion_sort_key(criterion: str, candidate: CandidateRecord) -> tuple[Any, ...]:
    """The ordering a stated criterion imposes, computed from receipt-recorded numbers.

    This exists so a third party holding only the receipt can check the order — the whole
    point of stating a criterion. It mirrors the ordering `evaluate._rank` applies, and
    `test_receipt.py` pins the two together against a live evaluation rather than leaving
    the agreement to inspection.
    """
    asserted = candidate.asserted_value_minor or 0
    if criterion == CRITERION_MAX_PROTECTION_THEN_VALUE:
        return (-candidate.protection_value_minor, -asserted, candidate.manifest_id)
    return (-asserted, candidate.manifest_id)


def ranking_from_candidates(
    candidates: Sequence[CandidateRecord],
    *,
    policy: ValuationPolicy,
    cart_total_minor: int,
) -> Ranking | None:
    """Rebuild the ranking a policy produces over a recorded candidate set.

    Returns None when nothing was attestable, which is the same answer `evaluate()` gives
    and is a legitimate receipt: the agent had nothing it was willing to put a value on.
    """
    if policy.criterion not in CRITERIA:
        raise ReceiptError(
            f"unknown criterion {policy.criterion!r}; expected one of {', '.join(CRITERIA)}"
        )
    baseline = cart_total_minor * policy.baseline_earn_bp // 10_000
    eligible = [
        c
        for c in candidates
        if c.attested()
        and (c.asserted_value_minor or 0) >= policy.min_value_to_rank_minor
    ]
    if not eligible:
        return None
    ordered = sorted(eligible, key=lambda c: criterion_sort_key(policy.criterion, c))
    entries: list[RankedInstrument] = []
    for i, c in enumerate(ordered):
        asserted = c.asserted_value_minor or 0
        nxt = ordered[i + 1].asserted_value_minor if i + 1 < len(ordered) else None
        entries.append(
            RankedInstrument(
                rank=i + 1,
                manifest_id=c.manifest_id,
                asserted_minor=asserted,
                baseline_minor=baseline,
                incremental_minor=asserted - baseline,
                protection_value_minor=c.protection_value_minor,
                margin_over_next_minor=None if nxt is None else asserted - nxt,
            )
        )
    return Ranking(
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash(),
        criterion=policy.criterion,
        baseline_minor=baseline,
        entries=tuple(entries),
    )


# --------------------------------------------------------------------------------------
# Attestation — compliance symmetry
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AttestationFinding:
    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class RankingAttestation:
    """Says "faithful" with the same machinery it uses to say "deviated"."""

    outcome: str
    findings: tuple[AttestationFinding, ...]
    expected_order: tuple[str, ...]
    expected_choice: str | None
    checked_against_mandate: bool

    @property
    def faithful(self) -> bool:
        return self.outcome == ATTEST_FAITHFUL

    def codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings)

    def headline(self) -> str:
        for f in self.findings:
            if f.code == self.outcome:
                return f.detail
        return self.findings[0].detail if self.findings else "no findings"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "faithful": self.faithful,
            "findings": [f.to_dict() for f in self.findings],
            "expected_order": list(self.expected_order),
            "expected_choice": self.expected_choice,
            "checked_against_mandate": self.checked_against_mandate,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RankingAttestation":
        return cls(
            outcome=str(d.get("outcome", ATTEST_UNKNOWN_CRITERION)),
            findings=tuple(
                AttestationFinding(str(f["code"]), str(f.get("detail", "")))
                for f in d.get("findings", ())
            ),
            expected_order=tuple(str(i) for i in d.get("expected_order", ())),
            expected_choice=d.get("expected_choice"),
            checked_against_mandate=bool(d.get("checked_against_mandate", False)),
        )


def attest_ranking(
    *,
    candidates: Sequence[CandidateRecord],
    ranking: Ranking | None,
    policy: ValuationPolicy,
    chosen_instrument_id: str | None,
    cart_total_minor: int,
    mandate: MandateBinding | None = None,
) -> RankingAttestation:
    """Check a recorded ranking against the recorded numbers, and say so either way.

    Checked, in order of seriousness:

      * every instrument the mandate authorised appears in the candidate set — omission;
      * the chosen instrument is one the mandate authorised — omission's mirror image;
      * the chosen instrument is a member of the candidate set;
      * the choice is the one the stated criterion makes;
      * the recorded order is the order the stated criterion produces;
      * exactly the attested candidates above the policy's floor are ranked;
      * the chosen instrument carries a verified witness.

    A clean pass returns ATTEST_FAITHFUL with a finding that says why, so the positive
    result is as quotable as the negative one.
    """
    findings: list[AttestationFinding] = []
    notes: list[AttestationFinding] = []
    by_instrument = {c.instrument_id: c for c in candidates}
    by_manifest = {c.manifest_id: c for c in candidates}

    if policy.criterion not in CRITERIA or (
        ranking is not None and ranking.criterion != policy.criterion
    ):
        stated = ranking.criterion if ranking is not None else policy.criterion
        return RankingAttestation(
            outcome=ATTEST_UNKNOWN_CRITERION,
            findings=(
                AttestationFinding(
                    ATTEST_UNKNOWN_CRITERION,
                    f"receipt states criterion {stated!r} against policy criterion "
                    f"{policy.criterion!r}; the order cannot be checked",
                ),
            ),
            expected_order=(),
            expected_choice=None,
            checked_against_mandate=False,
        )

    expected = ranking_from_candidates(
        candidates, policy=policy, cart_total_minor=cart_total_minor
    )
    expected_order = (
        tuple(by_manifest[e.manifest_id].instrument_id for e in expected.entries)
        if expected is not None
        else ()
    )
    expected_choice = expected_order[0] if expected_order else None

    checked_against_mandate = mandate is not None
    if mandate is not None:
        authorized = set(mandate.authorized_instrument_ids)
        missing = sorted(authorized - set(by_instrument))
        if missing:
            findings.append(
                AttestationFinding(
                    ATTEST_CANDIDATE_SET_INCOMPLETE,
                    f"the mandate authorises {', '.join(missing)} and the candidate set "
                    f"does not mention {'them' if len(missing) > 1 else 'it'}. An "
                    f"instrument that is never considered cannot be chosen.",
                )
            )
        # The mirror of omission: an agent that adds an instrument of its own and picks it.
        # Considering an extra instrument is merely noise; selecting one is a governance
        # failure, and it is cheap to catch here even though spend authority is the PDP's.
        if chosen_instrument_id is not None and chosen_instrument_id not in authorized:
            findings.append(
                AttestationFinding(
                    ATTEST_CHOSEN_NOT_AUTHORIZED,
                    f"the receipt records {chosen_instrument_id!r} as chosen, and the "
                    f"mandate authorises only {', '.join(sorted(authorized))}",
                )
            )

    if ranking is None:
        if expected is not None:
            findings.append(
                AttestationFinding(
                    ATTEST_RANKED_SET_WRONG,
                    f"the receipt carries no ranking, but {len(expected.entries)} "
                    f"candidate(s) were attested and rankable",
                )
            )
        else:
            notes.append(
                AttestationFinding(
                    ATTEST_NO_RANKING,
                    "no instrument carried a verified witness, so nothing was ranked. "
                    "The refusal is recorded rather than a number being invented.",
                )
            )
        return _conclude(
            findings=findings,
            notes=notes,
            faithful_detail=(
                "every instrument the mandate authorised was considered and none could be "
                "valued from a verified witness, so nothing was asserted"
            ),
            expected_order=expected_order,
            expected_choice=expected_choice,
            checked_against_mandate=checked_against_mandate,
        )

    recorded_order = tuple(
        by_manifest[e.manifest_id].instrument_id
        for e in ranking.entries
        if e.manifest_id in by_manifest
    )
    unknown = [e.manifest_id for e in ranking.entries if e.manifest_id not in by_manifest]
    if unknown:
        findings.append(
            AttestationFinding(
                ATTEST_RANKED_SET_WRONG,
                f"the ranking names {', '.join(unknown)}, which the candidate set does not "
                f"record",
            )
        )

    if chosen_instrument_id is not None and chosen_instrument_id not in by_instrument:
        findings.append(
            AttestationFinding(
                ATTEST_CHOSEN_NOT_A_CANDIDATE,
                f"the chosen instrument {chosen_instrument_id!r} is not in the recorded "
                f"candidate set",
            )
        )

    if chosen_instrument_id != expected_choice:
        findings.append(
            AttestationFinding(
                ATTEST_DEVIATED,
                f"{policy.criterion} selects {expected_choice!r}; the receipt records "
                f"{chosen_instrument_id!r} as chosen",
            )
        )

    if recorded_order != expected_order and not unknown:
        findings.append(
            AttestationFinding(
                ATTEST_ORDER_INCONSISTENT,
                f"recorded order {list(recorded_order)} is not the order "
                f"{policy.criterion} produces over the recorded values, which is "
                f"{list(expected_order)}",
            )
        )

    if set(recorded_order) != set(expected_order) and not unknown:
        findings.append(
            AttestationFinding(
                ATTEST_RANKED_SET_WRONG,
                f"ranked set {sorted(recorded_order)} does not match the attested "
                f"candidates above the policy floor, {sorted(expected_order)}",
            )
        )

    chosen = by_instrument.get(chosen_instrument_id or "")
    if chosen is not None and not chosen.is_verified():
        findings.append(
            AttestationFinding(
                ATTEST_CHOSEN_UNVERIFIED,
                f"the chosen instrument {chosen.instrument_id} carries status "
                f"{chosen.witness_status}; no verified allocation supports its value",
            )
        )

    considered = (
        f"all {len(mandate.authorized_instrument_ids)} instrument(s) the mandate "
        f"authorised were considered, and "
        if mandate is not None
        else ""
    )
    return _conclude(
        findings=findings,
        notes=notes,
        faithful_detail=(
            f"{considered}{chosen_instrument_id!r} is exactly what {policy.criterion} "
            f"selects over the recorded candidate set"
        ),
        expected_order=expected_order,
        expected_choice=expected_choice,
        checked_against_mandate=checked_against_mandate,
    )


def _conclude(
    *,
    findings: Sequence[AttestationFinding],
    notes: Sequence[AttestationFinding],
    faithful_detail: str,
    expected_order: tuple[str, ...],
    expected_choice: str | None,
    checked_against_mandate: bool,
) -> RankingAttestation:
    """Pick the headline outcome. Faults decide it; notes travel alongside.

    Compliance symmetry lives here: with no faults the outcome is a positive attestation
    carrying its own reason, produced by the same code path that produces the negative ones.
    """
    if findings:
        codes = {f.code for f in findings}
        outcome = next(code for code in ATTEST_SEVERITY if code in codes)
        all_findings = tuple(findings) + tuple(notes)
    else:
        outcome = ATTEST_FAITHFUL
        all_findings = (AttestationFinding(ATTEST_FAITHFUL, faithful_detail), *notes)
    return RankingAttestation(
        outcome=outcome,
        findings=all_findings,
        expected_order=expected_order,
        expected_choice=expected_choice,
        checked_against_mandate=checked_against_mandate,
    )


# --------------------------------------------------------------------------------------
# The receipt
# --------------------------------------------------------------------------------------


DEFAULT_DISCLOSURES = (
    UNPRICED_DISCLOSURE,
    "The issuer signs the benefit manifests only. The ranking and the valuation policy are "
    "the Card Member's; their hashes are recorded and are not issuer-endorsed.",
    "Each asserted value is backed by a concrete allocation whose content hash appears "
    "above. Verification is arithmetic over that allocation and requires no solver.",
    "The allocator is conservative by construction and not optimal. The gap to optimal is "
    "measured offline and quoted separately; nothing here claims optimality.",
    "Signatures are HMAC-SHA256 under prototype keys. A production deployment signs with "
    "the issuer's HSM key and the agent's own key; canonicalisation is unchanged.",
    "Manifests model publicly published card terms. No live offers feed is claimed.",
)


def stable_evaluation_body(evaluation: Evaluation) -> dict[str, Any]:
    """`Evaluation.to_dict()` with the measured latency removed and candidates ordered.

    A receipt is hashed and signed, and a replayed run must reproduce it byte for byte.
    `elapsed_ms` measures the machine rather than the decision, and it is the only field on
    an evaluation that does not replay. Candidates are ordered by manifest id so the bytes
    do not depend on the order instruments happened to be supplied in.
    """
    body = evaluation.to_dict()
    body["candidates"] = sorted(
        ({k: v for k, v in c.items() if k != "elapsed_ms"} for c in body["candidates"]),
        key=lambda c: str(c.get("manifest_id", "")),
    )
    return body


@dataclass(frozen=True)
class DecisionReceipt:
    """The full record of one instrument choice, ready to sign and anchor."""

    receipt_id: str
    issued_at: int
    posture: str
    session: CheckoutSession
    mandate: MandateBinding
    agent: Identity
    platform: Identity
    candidates: tuple[CandidateRecord, ...]
    unpriced: tuple[UnpricedConsideration, ...]
    policy: ValuationPolicy
    ranking: Ranking | None
    chosen_instrument_id: str | None
    attestation: RankingAttestation
    evaluation_body: dict[str, Any] | None = None
    version: str = RECEIPT_VERSION
    disclosures: tuple[str, ...] = ()

    def candidate(self, instrument_id: str) -> CandidateRecord | None:
        for c in self.candidates:
            if c.instrument_id == instrument_id:
                return c
        return None

    def chosen(self) -> CandidateRecord | None:
        return self.candidate(self.chosen_instrument_id or "")

    def manifest_hashes(self) -> dict[str, str]:
        return {c.instrument_id: c.manifest_content_hash for c in self.candidates}

    def ordered_instrument_ids(self) -> tuple[str, ...]:
        """Ranked instruments first, then everything considered but not ranked."""
        by_manifest = {c.manifest_id: c.instrument_id for c in self.candidates}
        ranked = (
            tuple(
                by_manifest[e.manifest_id]
                for e in self.ranking.entries
                if e.manifest_id in by_manifest
            )
            if self.ranking is not None
            else ()
        )
        rest = tuple(
            sorted(c.instrument_id for c in self.candidates if c.instrument_id not in set(ranked))
        )
        return ranked + rest

    def body(self) -> dict[str, Any]:
        """The exact object that gets signed — by an agent or a Card Member, never an issuer.

        Candidates are emitted sorted by instrument id so the same decision canonicalises
        identically regardless of the order instruments were evaluated in.
        """
        ordered = sorted(self.candidates, key=lambda c: c.instrument_id)
        return {
            "version": self.version,
            "receipt_id": self.receipt_id,
            "issued_at": self.issued_at,
            "posture": self.posture,
            "session": self.session.to_dict(),
            "mandate": self.mandate.to_dict(),
            "identities": {"agent": self.agent.to_dict(), "platform": self.platform.to_dict()},
            "candidate_set": {
                "size": len(ordered),
                "instrument_ids": [c.instrument_id for c in ordered],
                "digest": candidate_set_digest(ordered),
                "candidates": [
                    c.to_dict(currency=self.session.currency) for c in ordered
                ],
            },
            "considered_but_unpriced": [u.to_dict() for u in self.unpriced],
            "ranking": (
                None
                if self.ranking is None
                else self.ranking.to_dict(currency=self.session.currency)
            ),
            "ranked_instrument_ids": list(self.ordered_instrument_ids()),
            "selection": {
                "instrument_id": self.chosen_instrument_id,
                "manifest_id": (self.chosen().manifest_id if self.chosen() else None),
                "criterion": self.policy.criterion,
            },
            # Top-level for the corpus reader in attribution.py, which keys on this name.
            "chosen_manifest_id": (self.chosen().manifest_id if self.chosen() else None),
            "valuation_policy": {
                **self.policy.to_dict(),
                "issuer_endorsement": ISSUER_ENDORSEMENT_NOTE,
            },
            "manifest_hashes": self.manifest_hashes(),
            "evaluation": self.evaluation_body,
            "attestation": self.attestation.to_dict(),
            "disclosures": list(self.disclosures),
        }

    def receipt_hash(self) -> str:
        return content_hash(self.body())

    def to_dict(self) -> dict[str, Any]:
        return self.body()

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DecisionReceipt":
        try:
            cset = d["candidate_set"]
            raw_ranking = d.get("ranking")
            return cls(
                receipt_id=str(d["receipt_id"]),
                issued_at=int(d["issued_at"]),
                posture=str(d.get("posture", POSTURE_OBSERVE_ONLY)),
                session=CheckoutSession.from_dict(d["session"]),
                mandate=MandateBinding.from_dict(d["mandate"]),
                agent=Identity.from_dict(d["identities"]["agent"]),
                platform=Identity.from_dict(d["identities"]["platform"]),
                candidates=tuple(
                    CandidateRecord.from_dict(c) for c in cset.get("candidates", ())
                ),
                unpriced=tuple(
                    UnpricedConsideration.from_dict(u)
                    for u in d.get("considered_but_unpriced", ())
                ),
                policy=policy_from_dict(d["valuation_policy"]),
                ranking=None if raw_ranking is None else ranking_from_dict(raw_ranking),
                chosen_instrument_id=(d.get("selection") or {}).get("instrument_id"),
                attestation=RankingAttestation.from_dict(d.get("attestation") or {}),
                evaluation_body=d.get("evaluation"),
                version=str(d.get("version", RECEIPT_VERSION)),
                disclosures=tuple(str(x) for x in d.get("disclosures", ())),
            )
        except KeyError as exc:
            raise ReceiptError(f"receipt missing required field {exc}") from exc

    def render_text(self, width: int = 88) -> str:
        rule = "═" * width
        thin = "─" * width
        out = [
            rule,
            "PLUMBLINE DECISION RECEIPT".center(width),
            f"{self.receipt_id}   issued {self.issued_at}   posture {self.posture}".center(width),
            rule,
            "",
            f"SESSION       {self.session.session_id} at {self.session.merchant}",
            f"  cart        "
            f"{fmt_currency(self.session.cart_total_minor, self.session.currency)}   "
            f"hash {self.session.cart_hash[:16]}…",
            f"  agent       {self.agent.identifier} ({self.agent.name})",
            f"  platform    {self.platform.identifier} ({self.platform.name})",
            f"  mandate     {self.mandate.mandate_id}, authorising "
            f"{len(self.mandate.authorized_instrument_ids)} instrument(s)",
            "",
            thin,
            f"CANDIDATE SET — {len(self.candidates)} considered, the full set and not the winner",
            thin,
        ]
        for inst_id in self.ordered_instrument_ids():
            c = self.candidate(inst_id)
            if c is None:
                continue
            mark = "→" if inst_id == self.chosen_instrument_id else " "
            value = (
                "no value asserted"
                if c.asserted_value_minor is None
                else fmt_currency(c.asserted_value_minor, self.session.currency)
            )
            out.append(f"  {mark} {c.instrument_id:<24} {c.issuer} {c.product}")
            out.append(f"      value     {value}   [{c.status} / {c.witness_status}]")
            out.append(f"      witness   {c.witness_hash[:32] or '(none)'}")
            out.append(f"      manifest  {c.manifest_content_hash[:32]}")
            if c.failure_codes or c.refusal_codes:
                out.append(
                    f"      refused   {', '.join((*c.refusal_codes, *c.failure_codes))}"
                )
        out += [
            "",
            thin,
            "DECISION",
            thin,
            f"  criterion   {self.policy.criterion}   (stated, and recomputable above)",
            f"  chosen      {self.chosen_instrument_id or '(none — nothing was attestable)'}",
            f"  policy      {self.policy.policy_hash()[:32]}  author={self.policy.author}",
            f"  issuer      {ISSUER_ENDORSEMENT_NOTE}",
            "",
            thin,
            "CONSIDERED BUT UNPRICED",
            thin,
        ]
        if self.unpriced:
            for u in self.unpriced:
                out.append(f"  {u.instrument_id}: {u.label}" + (f" — {u.note}" if u.note else ""))
        else:
            out.append("  (none declared)")
        out += ["", thin, "ATTESTATION", thin, f"  outcome     {self.attestation.outcome}"]
        for f in self.attestation.findings:
            out.append(f"  {f.code}: {f.detail}")
        out += ["", thin, "DISCLOSURES", thin]
        for note in self.disclosures:
            out.append(f"  • {note}")
        out.append(rule)
        return "\n".join(out)


def policy_from_dict(d: Mapping[str, Any]) -> ValuationPolicy:
    """Rebuild a valuation policy from its serialized form, ignoring derived fields."""
    return ValuationPolicy(
        policy_id=str(d.get("policy_id", "default_cardholder_policy")),
        criterion=str(d["criterion"]),
        author=str(d.get("author", AUTHOR_CARDHOLDER)),
        baseline_earn_bp=int(d.get("baseline_earn_bp", 0)),
        require_signed_manifest=bool(d.get("require_signed_manifest", True)),
        max_manifest_age_s=(
            None if d.get("max_manifest_age_s") is None else int(d["max_manifest_age_s"])
        ),
        min_value_to_rank_minor=int(d.get("min_value_to_rank_minor", 0)),
    )


def ranking_from_dict(d: Mapping[str, Any]) -> Ranking:
    """Rebuild a ranking from its serialized form so the order can be re-checked."""
    return Ranking(
        policy_id=str(d.get("policy_id", "")),
        policy_hash=str(d.get("policy_hash", "")),
        criterion=str(d["criterion"]),
        baseline_minor=int(d.get("baseline_minor", 0)),
        entries=tuple(
            RankedInstrument(
                rank=int(e["rank"]),
                manifest_id=str(e["manifest_id"]),
                asserted_minor=int(e["asserted_minor"]),
                baseline_minor=int(e.get("baseline_minor", 0)),
                incremental_minor=int(e.get("incremental_minor", 0)),
                protection_value_minor=int(e.get("protection_value_minor", 0)),
                margin_over_next_minor=(
                    None
                    if e.get("margin_over_next_minor") is None
                    else int(e["margin_over_next_minor"])
                ),
            )
            for e in d.get("entries", ())
        ),
    )


@dataclass(frozen=True)
class SignedReceipt:
    """A receipt plus a detached signature, whose role is never `issuer`."""

    receipt: DecisionReceipt
    signature: str
    signing_key_id: str
    signer_role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt": self.receipt.body(),
            "signature": {
                "alg": SIGNATURE_ALG,
                "key_id": self.signing_key_id,
                "role": self.signer_role,
                "value": self.signature,
                "covers": (
                    "the whole receipt body, including the ranking. Signed by the agent or "
                    "the Card Member — no issuer key covers this document."
                ),
            },
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "SignedReceipt":
        sig = d.get("signature") or {}
        return cls(
            receipt=DecisionReceipt.from_dict(d["receipt"]),
            signature=str(sig.get("value", "")),
            signing_key_id=str(sig.get("key_id", "")),
            signer_role=str(sig.get("role", "")),
        )

    def leaf_hash(self, timestamp: int) -> str:
        """The transparency-log leaf this receipt produces at a given log timestamp."""
        return hash_leaf(
            leaf_data_for(kind=ENTRY_RECEIPT, body=self.to_dict(), timestamp=timestamp)
        )


def sign_receipt(
    receipt: DecisionReceipt,
    *,
    key: str | bytes,
    signer_role: str = ROLE_AGENT,
    key_reference: str | None = None,
) -> SignedReceipt:
    """Sign a receipt as the agent or the Card Member.

    Refuses the issuer role. The body contains a ranking by construction, so an issuer
    signature here would be an issuer signature over a comparison between instruments —
    the one thing this architecture never produces.
    """
    if signer_role == ROLE_ISSUER:
        raise IssuerSigningBoundaryError(
            "refusing to sign a decision receipt with the issuer role: the receipt body "
            "carries a ranking, and no issuer signature may cover a comparison between "
            "instruments. Sign with signer_role='agent' or 'cardholder'; the issuer's "
            "signature is already present on each candidate's manifest."
        )
    if signer_role not in RECEIPT_SIGNER_ROLES:
        raise ReceiptError(
            f"unknown signer role {signer_role!r}; expected one of "
            f"{', '.join(RECEIPT_SIGNER_ROLES)}"
        )
    return SignedReceipt(
        receipt=receipt,
        signature=_hmac_hex(receipt.body(), key),
        signing_key_id=key_reference or key_id(key),
        signer_role=signer_role,
    )


def build_receipt(
    *,
    receipt_id: str,
    issued_at: int,
    session: CheckoutSession,
    mandate: MandateBinding,
    agent: Identity,
    platform: Identity,
    candidates: Sequence[CandidateRecord],
    policy: ValuationPolicy,
    ranking: Ranking | None = None,
    chosen_instrument_id: str | None = None,
    unpriced: Sequence[UnpricedConsideration] = (),
    posture: str = POSTURE_OBSERVE_ONLY,
    disclosures: Sequence[str] = DEFAULT_DISCLOSURES,
    evaluation_body: Mapping[str, Any] | None = None,
) -> DecisionReceipt:
    """Assemble a receipt from candidate records, rank if needed, attest, and return.

    `ranking` and `chosen_instrument_id` may be supplied to record what an agent ACTUALLY
    did, including a choice the stated criterion does not support. That is not a loophole:
    a format that can only express compliant behaviour cannot evidence non-compliant
    behaviour. The attestation marks the divergence rather than the format hiding it.
    Omitted, both are computed from the candidate set and the policy.
    """
    if posture not in POSTURES:
        raise ReceiptError(f"unknown posture {posture!r}; expected one of {', '.join(POSTURES)}")
    if not candidates:
        raise ReceiptError(
            "a receipt with an empty candidate set records nothing. Record every instrument "
            "the mandate authorised, including the ones that could not be valued."
        )
    seen: set[str] = set()
    for c in candidates:
        if c.instrument_id in seen:
            raise ReceiptError(
                f"duplicate candidate {c.instrument_id!r}; each instrument appears once"
            )
        if c.witness_status not in WITNESS_STATUSES:
            raise ReceiptError(
                f"candidate {c.instrument_id!r} has unknown witness status "
                f"{c.witness_status!r}; expected one of {', '.join(WITNESS_STATUSES)}"
            )
        if c.status not in (STATUS_ATTESTED, STATUS_REFUSED):
            raise ReceiptError(
                f"candidate {c.instrument_id!r} has unknown status {c.status!r}; expected "
                f"{STATUS_ATTESTED} or {STATUS_REFUSED}"
            )
        seen.add(c.instrument_id)

    computed = (
        ranking
        if ranking is not None
        else ranking_from_candidates(
            candidates, policy=policy, cart_total_minor=session.cart_total_minor
        )
    )
    by_manifest = {c.manifest_id: c.instrument_id for c in candidates}
    if chosen_instrument_id is not None:
        chosen = chosen_instrument_id
    elif computed is not None and computed.entries:
        chosen = by_manifest.get(computed.entries[0].manifest_id)
    else:
        chosen = None

    attestation = attest_ranking(
        candidates=candidates,
        ranking=computed,
        policy=policy,
        chosen_instrument_id=chosen,
        cart_total_minor=session.cart_total_minor,
        mandate=mandate,
    )
    return DecisionReceipt(
        receipt_id=receipt_id,
        issued_at=issued_at,
        posture=posture,
        session=session,
        mandate=mandate,
        agent=agent,
        platform=platform,
        candidates=tuple(candidates),
        unpriced=tuple(unpriced),
        policy=policy,
        ranking=computed,
        chosen_instrument_id=chosen,
        attestation=attestation,
        evaluation_body=dict(evaluation_body) if evaluation_body is not None else None,
        disclosures=tuple(disclosures),
    )


def build_receipt_from_evaluation(
    *,
    receipt_id: str,
    issued_at: int,
    evaluation: Evaluation,
    session: CheckoutSession,
    mandate: MandateBinding,
    agent: Identity,
    platform: Identity,
    signed_manifests: Mapping[str, SignedManifest] | None = None,
    instrument_ids: Mapping[str, str] | None = None,
    chosen_instrument_id: str | None = None,
    posture: str = POSTURE_OBSERVE_ONLY,
    disclosures: Sequence[str] = DEFAULT_DISCLOSURES,
    embed_evaluation: bool = True,
) -> DecisionReceipt:
    """Turn an `evaluate.Evaluation` into a signed-ready receipt.

    `instrument_ids` maps manifest id -> instrument id where a wallet distinguishes the two;
    by default the instrument id is the manifest id. `signed_manifests` supplies the issuer
    signatures to carry forward, keyed by manifest id — a receipt that records a manifest
    hash without the signature over it leaves a counterparty nothing to check.
    """
    instrument_ids = dict(instrument_ids or {})
    signed_manifests = dict(signed_manifests or {})

    candidates: list[CandidateRecord] = []
    unpriced: list[UnpricedConsideration] = []
    for valuation in evaluation.candidates:
        signed_manifest = signed_manifests.get(valuation.manifest_id)
        record = candidate_from_valuation(
            valuation,
            instrument_id=instrument_ids.get(valuation.manifest_id, valuation.manifest_id),
            signed_manifest=signed_manifest,
        )
        candidates.append(record)
        if signed_manifest is not None:
            unpriced.extend(
                unpriced_considerations(record.instrument_id, signed_manifest.manifest)
            )
        else:
            # The evaluation records labels, not ids. Carry them, and say which is which
            # rather than presenting a label where an id belongs.
            unpriced.extend(
                UnpricedConsideration(
                    instrument_id=record.instrument_id,
                    benefit_id="",
                    label=label,
                    note="benefit id unavailable: no manifest was supplied to the builder",
                )
                for label in valuation.unpriced_labels
            )
    return build_receipt(
        receipt_id=receipt_id,
        issued_at=issued_at,
        session=session,
        mandate=mandate,
        agent=agent,
        platform=platform,
        candidates=candidates,
        policy=evaluation.policy,
        ranking=evaluation.ranking,
        chosen_instrument_id=chosen_instrument_id,
        unpriced=unpriced,
        posture=posture,
        disclosures=disclosures,
        evaluation_body=stable_evaluation_body(evaluation) if embed_evaluation else None,
    )


# --------------------------------------------------------------------------------------
# Graceful degrade — the default posture is observe-only
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CounterpartAssessment:
    """What the presence or absence of a counterpart receipt means.

    `proceeds` is False only under elected enforcement, and the failure in that case is the
    agent failing to discharge the Card Member's own delegated authority — the same shape
    as a spend limit denying — not any issuer refusing a platform's credential.
    """

    posture: str
    proceeds: bool
    reason_code: str
    detail: str
    coverage_eligible: bool
    record: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "posture": self.posture,
            "proceeds": self.proceeds,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "coverage_eligible": self.coverage_eligible,
            "record": self.record,
        }


def assess_counterpart(
    *,
    receipt: SignedReceipt | None,
    posture: str,
    session: CheckoutSession,
    mandate: MandateBinding,
    assessed_at: int,
) -> CounterpartAssessment:
    """Decide what a missing counterpart receipt means, given the elected posture.

    OBSERVE_ONLY, the default: the transaction proceeds and the gap is recorded as an
    unattested selection. This is architectural, not a courtesy — a credential that hard
    fails in a third-party checkout gets routed around, and routing around is exactly the
    outcome a disclosure rail exists to avoid. The platform is asked for nothing and never
    sees this check.

    ENFORCE: the Card Member elected to require the receipt their own mandate asks for, and
    their agent cannot produce it, so the delegation does not discharge.

    In both cases the same fact is recorded, and coverage — not authorization — is what the
    receipt is a condition of.
    """
    if posture not in POSTURES:
        raise ReceiptError(f"unknown posture {posture!r}; expected one of {', '.join(POSTURES)}")

    if receipt is not None:
        return CounterpartAssessment(
            posture=posture,
            proceeds=True,
            reason_code=REASON_SELECTION_ATTESTED,
            detail=(
                f"receipt {receipt.receipt.receipt_id} discharges the disclosure caveat on "
                f"mandate {mandate.mandate_id}"
            ),
            coverage_eligible=True,
            record={
                "session_hash": session.session_hash(),
                "cart_hash": session.cart_hash,
                "mandate_id": mandate.mandate_id,
                "receipt_id": receipt.receipt.receipt_id,
                "receipt_hash": receipt.receipt.receipt_hash(),
                "assessed_at": assessed_at,
                "reason_code": REASON_SELECTION_ATTESTED,
            },
        )

    record = {
        "session_hash": session.session_hash(),
        "cart_hash": session.cart_hash,
        "mandate_id": mandate.mandate_id,
        "merchant": session.merchant,
        "amount_minor": session.cart_total_minor,
        "assessed_at": assessed_at,
        "reason_code": REASON_UNATTESTED_SELECTION,
        "note": (
            "no counterpart receipt was produced for this selection; the gap is recorded, "
            "not penalised"
        ),
    }

    if posture == POSTURE_OBSERVE_ONLY:
        return CounterpartAssessment(
            posture=posture,
            proceeds=True,
            reason_code=REASON_UNATTESTED_SELECTION,
            detail=(
                "no counterpart receipt; the transaction proceeds and the selection is "
                "recorded as unattested. Agent Purchase Protection coverage is conditioned "
                "on evidence; authorization is not."
            ),
            coverage_eligible=False,
            record=record,
        )

    return CounterpartAssessment(
        posture=posture,
        proceeds=False,
        reason_code=REASON_DISCLOSURE_CAVEAT_UNDISCHARGED,
        detail=(
            f"the Card Member elected enforcement on mandate {mandate.mandate_id}. Their "
            f"agent could not produce the disclosure its own delegated authority requires, "
            f"so the delegation does not discharge."
        ),
        coverage_eligible=False,
        record={**record, "reason_code": REASON_DISCLOSURE_CAVEAT_UNDISCHARGED},
    )


# --------------------------------------------------------------------------------------
# Anchoring
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchoredReceipt:
    """A receipt, the log entry it became, and the head that proves it was there."""

    signed_receipt: SignedReceipt
    seq: int
    sth: SignedTreeHead
    inclusion_proof: InclusionProof

    def verify_anchor(self) -> bool:
        return self.inclusion_proof.verify_against(self.sth)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt": self.signed_receipt.to_dict(),
            "log": {
                "seq": self.seq,
                "sth": self.sth.to_dict(),
                "inclusion_proof": self.inclusion_proof.to_dict(),
            },
        }


def anchor_receipt(
    log: TransparencyLog,
    signed_receipt: SignedReceipt,
    *,
    timestamp: int,
    key: str | bytes | None = None,
) -> AnchoredReceipt:
    """Append a receipt to the log and return it with a head and an inclusion proof."""
    entry = log.append(kind=ENTRY_RECEIPT, body=signed_receipt.to_dict(), timestamp=timestamp)
    sth = log.signed_tree_head(timestamp=timestamp, key=key)
    return AnchoredReceipt(
        signed_receipt=signed_receipt,
        seq=entry.seq,
        sth=sth,
        inclusion_proof=log.inclusion_proof(entry.seq),
    )


def record_unattested_selection(
    log: TransparencyLog, assessment: CounterpartAssessment, *, timestamp: int
) -> int:
    """Land a missing-receipt gap in the log. Returns the entry sequence number.

    The gap is a fact about the corpus, so it is logged with the same weight as a receipt.
    A corpus with no record of its own holes cannot be used to reason about coverage.
    """
    if assessment.reason_code == REASON_SELECTION_ATTESTED:
        raise ReceiptError(
            "this selection was attested; anchor the receipt with anchor_receipt rather "
            "than recording a gap that does not exist"
        )
    return log.append(
        kind=ENTRY_UNATTESTED_SELECTION, body=assessment.to_dict(), timestamp=timestamp
    ).seq


# --------------------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class ReceiptVerification:
    ok: bool
    checks: tuple[Check, ...]

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def failed(self, name: str) -> bool:
        return any(c.name == name for c in self.failures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [c.to_dict() for c in self.checks],
            "failures": [c.name for c in self.failures],
        }

    def render_text(self) -> str:
        lines = ["VERIFIED" if self.ok else "VERIFICATION FAILED"]
        for c in self.checks:
            lines.append(f"  [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
        return "\n".join(lines)


def verify_receipt(
    payload: SignedReceipt | Mapping[str, Any],
    key: str | bytes,
    *,
    cart: Cart | None = None,
    manifests: Mapping[str, SignedManifest] | None = None,
    issuer_keys: Mapping[str, str | bytes] | None = None,
    witnesses: Mapping[str, Witness] | None = None,
) -> ReceiptVerification:
    """Re-derive everything the receipt claims, from the document alone.

    Takes a plain mapping as well as the dataclass: a counterparty receives JSON and must
    be able to check it with this function and nothing else.

    The optional arguments cover what a receipt records by hash rather than by value, which
    a holder of the underlying objects can therefore check:

      `cart`         the cart whose hash is bound into the session;
      `manifests`    instrument id -> the signed manifest whose content hash was recorded,
                     verified under `issuer_keys`, which is keyed by issuer key id;
      `witnesses`    instrument id -> the allocation whose content hash was recorded, so
                     "show me the derivation" is answerable against what was signed.
    """
    checks: list[Check] = []

    if isinstance(payload, SignedReceipt):
        payload = payload.to_dict()

    body = payload.get("receipt")
    sig = payload.get("signature") or {}
    if (
        not isinstance(body, Mapping)
        or "candidate_set" not in body
        or "valuation_policy" not in body
    ):
        checks.append(Check(CHECK_STRUCTURE, False, "receipt body is missing or malformed"))
        return ReceiptVerification(ok=False, checks=tuple(checks))
    checks.append(
        Check(
            CHECK_STRUCTURE,
            True,
            f"body carries a session, a {body['candidate_set']['size']}-instrument "
            f"candidate set, a stated criterion and a policy hash",
        )
    )

    role = str(sig.get("role", ""))
    role_ok = role in RECEIPT_SIGNER_ROLES
    checks.append(
        Check(
            CHECK_SIGNER_NOT_ISSUER,
            role_ok,
            f"signer role is {role!r}"
            + ("" if role_ok else "; a receipt carrying a ranking must not be issuer-signed"),
        )
    )

    sig_ok = hmac.compare_digest(_hmac_hex(body, key), str(sig.get("value", "")))
    checks.append(
        Check(
            CHECK_SIGNATURE,
            sig_ok,
            "HMAC-SHA256 over the canonical body recomputes"
            if sig_ok
            else "signature does not match the body; the document was altered after signing",
        )
    )

    violations = issuer_signature_scope_violations(payload)
    checks.append(
        Check(
            CHECK_NO_ISSUER_SIGNED_RANKING,
            not violations,
            f"issuer signatures cover manifest bodies only; the ranking is signed by "
            f"{role or 'nobody'}"
            if not violations
            else "issuer signature scope violation: " + "; ".join(violations),
        )
    )

    candidates = tuple(
        CandidateRecord.from_dict(c) for c in body["candidate_set"].get("candidates", ())
    )
    # Read off the document under verification, never from a caller's assumption: the
    # witness hashes below were computed under the currency this receipt was issued in, so
    # a verifier that substitutes its own default would report a mismatch that is really
    # its own.
    session_currency = str((body.get("session") or {}).get("currency", "INR"))
    digest_ok = candidate_set_digest(candidates) == str(body["candidate_set"].get("digest", ""))
    checks.append(
        Check(
            CHECK_CANDIDATE_DIGEST,
            digest_ok,
            f"digest covers all {len(candidates)} candidate(s)"
            if digest_ok
            else "candidate set digest does not match the recorded candidates; an "
            "instrument was added or removed after the digest was computed",
        )
    )

    if cart is not None:
        cart_ok = cart.hash() == str(body["session"].get("cart_hash", ""))
        checks.append(
            Check(
                CHECK_CART_HASH,
                cart_ok,
                "the cart supplied hashes to the value bound into the receipt"
                if cart_ok
                else "cart hash mismatch: this receipt does not describe the cart supplied",
            )
        )

    if manifests:
        problems: list[str] = []
        checked = 0
        for c in candidates:
            signed = manifests.get(c.instrument_id)
            if signed is None:
                continue
            checked += 1
            if signed.manifest.content_hash() != c.manifest_content_hash:
                problems.append(f"{c.instrument_id}: manifest content hash mismatch")
                continue
            if signed.signature != c.issuer_signature:
                problems.append(f"{c.instrument_id}: recorded issuer signature differs")
                continue
            issuer_key = (issuer_keys or {}).get(c.issuer_key_id)
            if issuer_key is None:
                problems.append(
                    f"{c.instrument_id}: no key supplied for issuer key id "
                    f"{c.issuer_key_id!r}, so the facts signature cannot be verified"
                )
                continue
            if not verify_manifest(signed, issuer_key):
                problems.append(f"{c.instrument_id}: issuer signature does not verify")
        checks.append(
            Check(
                CHECK_MANIFEST_SIGNATURES,
                not problems,
                f"{checked} manifest(s) hash and verify under their issuer's key"
                if not problems
                else "; ".join(problems),
            )
        )

    if witnesses:
        mismatched = [
            c.instrument_id
            for c in candidates
            if c.instrument_id in witnesses
            and witness_content_hash(
                witnesses[c.instrument_id], currency=session_currency
            )
            != c.witness_hash
        ]
        checks.append(
            Check(
                CHECK_WITNESS_HASHES,
                not mismatched,
                f"{len(witnesses)} witness(es) hash to the values recorded"
                if not mismatched
                else f"witness content hash mismatch on {', '.join(mismatched)}",
            )
        )

    evaluation = body.get("evaluation")
    if isinstance(evaluation, Mapping):
        by_manifest = {c.manifest_id: c for c in candidates}
        disagreements: list[str] = []
        for entry in evaluation.get("candidates", ()):
            if not isinstance(entry, Mapping):
                continue
            rec = by_manifest.get(str(entry.get("manifest_id", "")))
            if rec is None:
                disagreements.append(
                    f"{entry.get('manifest_id')} valued in the evaluation but absent from "
                    f"the candidate set"
                )
                continue
            if str(entry.get("status")) != rec.status:
                disagreements.append(f"{rec.instrument_id}: status differs from the evaluation")
            if entry.get("asserted_minor") != rec.asserted_value_minor:
                disagreements.append(f"{rec.instrument_id}: asserted value differs")
            if str(entry.get("manifest_hash", "")) != rec.manifest_content_hash:
                disagreements.append(f"{rec.instrument_id}: manifest hash differs")
        checks.append(
            Check(
                CHECK_EVALUATION_AGREES,
                not disagreements,
                f"{len(evaluation.get('candidates', ()))} evaluated instrument(s) agree "
                f"with the candidate set"
                if not disagreements
                else "; ".join(disagreements),
            )
        )

    try:
        policy = policy_from_dict(body["valuation_policy"])
    except (ReceiptError, EvaluationError, KeyError, ValueError) as exc:
        checks.append(Check(CHECK_RANKING, False, f"valuation policy is not usable: {exc}"))
        return ReceiptVerification(ok=False, checks=tuple(checks))

    raw_ranking = body.get("ranking")
    ranking = None if raw_ranking is None else ranking_from_dict(raw_ranking)
    attestation = attest_ranking(
        candidates=candidates,
        ranking=ranking,
        policy=policy,
        chosen_instrument_id=(body.get("selection") or {}).get("instrument_id"),
        cart_total_minor=int(body["session"]["cart_total_minor"]),
        mandate=MandateBinding.from_dict(body["mandate"]),
    )
    checks.append(Check(CHECK_RANKING, attestation.faithful, attestation.headline()))

    return ReceiptVerification(ok=all(c.ok for c in checks), checks=tuple(checks))


# --------------------------------------------------------------------------------------
# The corpus
# --------------------------------------------------------------------------------------


def receipts_from_log(log: TransparencyLog) -> tuple[SignedReceipt, ...]:
    """Every receipt in a log, in append order. The corpus, for attribution work."""
    return tuple(SignedReceipt.from_dict(e.body) for e in log.filter(ENTRY_RECEIPT))


def instruments_considered(receipts: Iterable[SignedReceipt]) -> dict[str, int]:
    """How often each instrument appeared in a candidate set. The omission counter.

    Compared against how often each was authorised, a persistent shortfall for one issuer
    is what silent exclusion looks like at corpus scale — visible across receipts even when
    every individual receipt is well-formed.
    """
    counts: dict[str, int] = {}
    for r in receipts:
        for c in r.receipt.candidates:
            counts[c.instrument_id] = counts.get(c.instrument_id, 0) + 1
    return counts


def instruments_authorized(receipts: Iterable[SignedReceipt]) -> dict[str, int]:
    """How often each instrument was authorised by the mandate behind a receipt."""
    counts: dict[str, int] = {}
    for r in receipts:
        for inst in r.receipt.mandate.authorized_instrument_ids:
            counts[inst] = counts.get(inst, 0) + 1
    return counts
