"""Manifest authoring: the deterministic half.

Onboarding a card product today means a human reading a forty-page terms document and
hand-writing a manifest. That is the adoption bottleneck for the whole disclosure rail — a
schema nobody can populate is a schema nobody adopts. So we let a model read the terms and
draft the manifest, and then we refuse to trust a single number it produced.

    The LLM proposes; the deterministic engine disposes.

Everything in this module is the disposing half. It takes a DRAFT — from a model, a human,
a CSV, a fax — and either ACCEPTS it or REJECTS it with typed reason codes. It never
repairs a draft silently. A draft that fails comes back with codes and the caller revises
it; nothing in here edits a number and carries on, because a validator that quietly fixes
its input is a validator whose output nobody can reason about.

What is checked
---------------
  structure        required fields, known kinds and windows, unique ids, strict unknown-key
                   rejection, no empty manifests
  numbers          every declared magnitude is a non-negative Python ``int`` in minor units.
                   Floats are rejected outright rather than coerced — a float in a decision
                   path is how a rounding error becomes a signed assertion
  earn capacity    an earn cap is denominated in VALUE, and must be consistent with the
                   qualifying-spend figure it was derived from and the declared rate
  exclusivity      groups have at least two members, name only priced benefits, and every
                   member can actually collide with another member
  reachability     no benefit that can never yield value by construction, and no eligibility
                   selector that can never match a line
  acceptance       no acceptance predicate, in any spelling
  provenance       every benefit names where its terms came from

What is NOT checked, and this is the important paragraph
--------------------------------------------------------
**This validator cannot tell you whether a rate is the real rate.** It checks that a draft
is internally consistent and structurally sound. It has no access to the issuer's published
terms, no corpus to diff against, and no way to know that "4x on dining" should have been
"3x on dining". A draft asserting a completely fabricated card passes every check in this
file, provided the fabrication is arithmetically tidy.

Only a human reading the source document — or the issuer signing with their own key —
establishes that a manifest describes a real product. Accepted drafts therefore carry
``UNVERIFIED_AGAINST_SOURCE`` as a standing advisory, and the marker is appended to the
manifest ``source`` string so it lands *inside the signed bytes* rather than in a log line
somebody can drop. If you find yourself describing this module as "automated verification
of card terms", you have described something that does not exist.

The signing gate
----------------
There is no public path in this module from a raw dict to a signature. ``sign_accepted``
takes an :class:`AcceptedDraft`, which cannot be constructed outside ``validate_draft``,
and re-runs the full validation over the sealed payload before it signs. Forging the seal
raises; mutating the draft after acceptance changes the payload hash and raises. The gate
is structural, not a code review convention.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    PRICED_KINDS,
    WINDOW_ANNUAL,
    WINDOW_MONTHLY,
    WINDOW_NONE,
    WINDOW_QUARTERLY,
    WINDOW_SEMIANNUAL,
    Benefit,
    Eligibility,
    Manifest,
    ManifestError,
    SignedManifest,
    build_manifest,
    canonical_json,
    sign_manifest,
)

DRAFT_VERSION = "plumbline/manifest-draft/1"

VERDICT_ACCEPTED = "ACCEPTED"
VERDICT_REJECTED = "REJECTED"

SEVERITY_ERROR = "error"  # blocks acceptance
SEVERITY_ADVISORY = "advisory"  # never blocks; always surfaced

# --------------------------------------------------------------------------------------
# Reason codes. Module-level constants, never inline strings — a caller branches on these
# and a drafting agent is handed them verbatim, so their spelling is part of the contract.
# --------------------------------------------------------------------------------------

# structure
DRAFT_NOT_AN_OBJECT = "DRAFT_NOT_AN_OBJECT"
DRAFT_MISSING_FIELD = "DRAFT_MISSING_FIELD"
DRAFT_UNKNOWN_FIELD = "DRAFT_UNKNOWN_FIELD"
DRAFT_WRONG_TYPE = "DRAFT_WRONG_TYPE"
DRAFT_EMPTY_MANIFEST = "DRAFT_EMPTY_MANIFEST"
DRAFT_DUPLICATE_BENEFIT_ID = "DRAFT_DUPLICATE_BENEFIT_ID"
DRAFT_EMPTY_BENEFIT_ID = "DRAFT_EMPTY_BENEFIT_ID"
DRAFT_UNKNOWN_KIND = "DRAFT_UNKNOWN_KIND"
DRAFT_UNKNOWN_WINDOW = "DRAFT_UNKNOWN_WINDOW"
DRAFT_UNKNOWN_CURRENCY = "DRAFT_UNKNOWN_CURRENCY"
DRAFT_ISSUED_AT_INVALID = "DRAFT_ISSUED_AT_INVALID"
DRAFT_VERSION_UNKNOWN = "DRAFT_VERSION_UNKNOWN"
DRAFT_MATERIALIZATION_FAILED = "DRAFT_MATERIALIZATION_FAILED"

# numbers
NUMBER_NOT_AN_INTEGER = "NUMBER_NOT_AN_INTEGER"
NUMBER_IS_A_FLOAT = "NUMBER_IS_A_FLOAT"
NUMBER_IS_A_BOOL = "NUMBER_IS_A_BOOL"
NUMBER_NEGATIVE = "NUMBER_NEGATIVE"
RATE_IMPLAUSIBLE = "RATE_IMPLAUSIBLE"

# per-kind coherence
EARN_RATE_MISSING = "EARN_RATE_MISSING"
EARN_CAP_NOT_VALUE_DENOMINATED = "EARN_CAP_NOT_VALUE_DENOMINATED"
EARN_CAP_INCONSISTENT_WITH_RATE = "EARN_CAP_INCONSISTENT_WITH_RATE"
EARN_CARRIES_FLAT_VALUE = "EARN_CARRIES_FLAT_VALUE"
CREDIT_HAS_NO_BALANCE = "CREDIT_HAS_NO_BALANCE"
CREDIT_CARRIES_RATE = "CREDIT_CARRIES_RATE"
CREDIT_CARRIES_FLAT_VALUE = "CREDIT_CARRIES_FLAT_VALUE"
PROTECTION_HAS_NO_VALUE = "PROTECTION_HAS_NO_VALUE"
PROTECTION_CARRIES_RATE = "PROTECTION_CARRIES_RATE"
UNPRICED_CARRIES_NUMBERS = "UNPRICED_CARRIES_NUMBERS"
UNPRICED_IN_EXCLUSIVITY_GROUP = "UNPRICED_IN_EXCLUSIVITY_GROUP"
SPEND_CAP_ON_NON_EARN = "SPEND_CAP_ON_NON_EARN"

# exclusivity
EXCLUSIVITY_GROUP_EMPTY_NAME = "EXCLUSIVITY_GROUP_EMPTY_NAME"
EXCLUSIVITY_GROUP_OF_ONE = "EXCLUSIVITY_GROUP_OF_ONE"
EXCLUSIVITY_GROUP_INERT = "EXCLUSIVITY_GROUP_INERT"

# reachability
UNREACHABLE_ZERO_VALUE = "UNREACHABLE_ZERO_VALUE"
UNREACHABLE_INVALID_MCC = "UNREACHABLE_INVALID_MCC"
UNREACHABLE_EMPTY_SELECTOR = "UNREACHABLE_EMPTY_SELECTOR"

# the deleted field
ACCEPTANCE_PREDICATE_PRESENT = "ACCEPTANCE_PREDICATE_PRESENT"

# provenance
PROVENANCE_MISSING = "PROVENANCE_MISSING"
PROVENANCE_PLACEHOLDER = "PROVENANCE_PLACEHOLDER"
MANIFEST_SOURCE_MISSING = "MANIFEST_SOURCE_MISSING"

# signing gate
SEAL_FORGED = "SEAL_FORGED"
SEAL_PAYLOAD_MUTATED = "SEAL_PAYLOAD_MUTATED"
SEAL_REVALIDATION_FAILED = "SEAL_REVALIDATION_FAILED"

# advisories
UNVERIFIED_AGAINST_SOURCE = "UNVERIFIED_AGAINST_SOURCE"
ADVISORY_BALANCE_EXHAUSTED = "ADVISORY_BALANCE_EXHAUSTED"
ADVISORY_NOT_ENROLLED = "ADVISORY_NOT_ENROLLED"
ADVISORY_UNCAPPED_EARN = "ADVISORY_UNCAPPED_EARN"
ADVISORY_UNCAPPED_PROTECTION = "ADVISORY_UNCAPPED_PROTECTION"
ADVISORY_UNRESTRICTED_CREDIT = "ADVISORY_UNRESTRICTED_CREDIT"

# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------

MANIFEST_REQUIRED_FIELDS = ("manifest_id", "issuer", "product", "currency", "issued_at", "benefits")
MANIFEST_OPTIONAL_FIELDS = ("version", "source")
MANIFEST_FIELDS = (*MANIFEST_REQUIRED_FIELDS, *MANIFEST_OPTIONAL_FIELDS)

BENEFIT_REQUIRED_FIELDS = ("benefit_id", "kind", "label", "provenance")
BENEFIT_OPTIONAL_FIELDS = (
    "eligibility",
    "rate_bp",
    "cap_qualifying_spend_minor",
    "capacity_minor",
    "flat_minor",
    "exclusivity_group",
    "window",
    "requires_enrollment",
    "enrolled",
    "note",
)
BENEFIT_FIELDS = (*BENEFIT_REQUIRED_FIELDS, *BENEFIT_OPTIONAL_FIELDS)

ELIGIBILITY_FIELDS = ("mccs", "merchants", "categories")

KNOWN_KINDS = (*PRICED_KINDS, KIND_UNPRICED)
KNOWN_WINDOWS = (
    WINDOW_MONTHLY,
    WINDOW_QUARTERLY,
    WINDOW_SEMIANNUAL,
    WINDOW_ANNUAL,
    WINDOW_NONE,
)
KNOWN_CURRENCIES = ("USD", "INR")

# Numeric fields, and the kinds that may declare each one as non-zero.
NUMERIC_FIELDS = ("rate_bp", "capacity_minor", "flat_minor", "cap_qualifying_spend_minor")

# 10_000 bp is 100% of qualifying spend returned as value. Anything above that is a
# decimal-shift typo, not a card. This is a sanity bound on the draft's internal
# arithmetic; it says nothing about whether the rate matches a real published term.
MAX_RATE_BP = 10_000

# MCCs are four-digit ISO 18245 codes. A selector outside the range admits no line that
# any real cart could contain, which makes the benefit it guards unreachable.
MCC_MIN = 1
MCC_MAX = 9_999

MIN_PROVENANCE_CHARS = 12

PROVENANCE_PLACEHOLDERS = frozenset(
    {
        "",
        "-",
        "?",
        "n/a",
        "na",
        "none",
        "null",
        "tbd",
        "todo",
        "unknown",
        "source",
        "card terms",
        "the terms",
        "terms",
        "see terms",
        "from terms",
        "issuer",
        "issuer terms",
        "public terms",
        "published terms",
        "test",
        "example",
        "placeholder",
    }
)

# --------------------------------------------------------------------------------------
# The deleted field
#
# CLAUDE.md removes the acceptance predicate and asks that the removal be said out loud. An
# issuer-signed field naming where its own instrument is refused is a machine-readable
# instruction to route away from that issuer, carrying the issuer's signature on the
# reason. Nobody signs that field, so nothing may draft it either.
#
# The screen below is a keyword screen over key names and free text. It is NOT semantic
# analysis and will not catch every phrasing. The structural guarantee is separate and
# stronger: `Benefit` has no acceptance field, and unknown keys are rejected outright, so
# an acceptance predicate cannot survive materialization even if this screen misses it.
# The screen exists to return the *right reason code* to a drafting agent, not to be the
# barrier.
# --------------------------------------------------------------------------------------

ACCEPTANCE_KEY_TOKENS = (
    "acceptance",
    "accepted_at",
    "accepted_merchants",
    "not_accepted",
    "unaccepted",
    "decline",
    "declined",
    "refused_at",
    "refuses",
    "route_away",
    "routes_away",
    "unsupported_merchants",
    "unsupported_mccs",
    "network_coverage",
    "coverage_network",
    "surcharge",
    "prefer_alternative",
)

ACCEPTANCE_VALUE_PHRASES = (
    "not accepted at",
    "not accepted by",
    "does not accept",
    "do not accept",
    "doesn't accept",
    "will be declined",
    "card is declined",
    "acceptance is limited",
    "limited acceptance",
    "route away from",
    "use a different card",
    "prefer another card",
    "no amex",
    "amex not accepted",
)

# The marker that lands inside the signed bytes. Idempotent: appended only when absent.
UNVERIFIED_SUFFIX = (
    " [validated for internal consistency and structure only; no automated check was made "
    "against the issuer's published terms — a human or the issuer's own signature "
    "establishes that this describes a real product]"
)

# --------------------------------------------------------------------------------------
# Guidance. Deterministic text, keyed by reason code, handed verbatim to whoever revises.
# A drafting agent must never be left to guess what a rejection meant.
# --------------------------------------------------------------------------------------

GUIDANCE: Mapping[str, str] = {
    DRAFT_NOT_AN_OBJECT: "The draft must be a JSON object with a 'benefits' array.",
    DRAFT_MISSING_FIELD: "Add the named field. Required manifest fields: "
    + ", ".join(MANIFEST_REQUIRED_FIELDS)
    + ". Required benefit fields: "
    + ", ".join(BENEFIT_REQUIRED_FIELDS)
    + ".",
    DRAFT_UNKNOWN_FIELD: "Remove the field. The schema is closed; invented fields never "
    "reach a signed manifest, so a field that is not in the schema is silently lost value.",
    DRAFT_WRONG_TYPE: "Correct the type named in the message.",
    DRAFT_EMPTY_MANIFEST: "A manifest with no benefits declares nothing. Add at least one.",
    DRAFT_DUPLICATE_BENEFIT_ID: "Give each benefit a distinct benefit_id. Ids address "
    "capacity buckets, so two benefits sharing an id would share one pool by accident.",
    DRAFT_EMPTY_BENEFIT_ID: "benefit_id must be a non-empty string; a witness references "
    "benefits by id and cannot address an empty one.",
    DRAFT_UNKNOWN_KIND: f"kind must be one of {list(KNOWN_KINDS)}.",
    DRAFT_UNKNOWN_WINDOW: f"window must be one of {list(KNOWN_WINDOWS)}.",
    DRAFT_UNKNOWN_CURRENCY: f"currency must be one of {list(KNOWN_CURRENCIES)}.",
    DRAFT_ISSUED_AT_INVALID: "issued_at is a positive integer Unix timestamp, passed in "
    "explicitly — never sampled from the clock inside a decision path.",
    DRAFT_VERSION_UNKNOWN: f"version, if present, must be {DRAFT_VERSION!r}.",
    DRAFT_MATERIALIZATION_FAILED: "The draft passed field checks but the manifest "
    "constructor rejected it; see the message.",
    NUMBER_NOT_AN_INTEGER: "Every magnitude is an integer count of MINOR units "
    "(cents/paise). Write 2500 for $25.00, not '2500' and not 25.",
    NUMBER_IS_A_FLOAT: "Floats are rejected, including exact ones like 2500.0. Money in "
    "this system is integer minor units end to end; a float in a decision path is how a "
    "rounding error becomes a signed assertion.",
    NUMBER_IS_A_BOOL: "A boolean was given where a number belongs.",
    NUMBER_NEGATIVE: "Magnitudes are non-negative. A negative rate would make the naive "
    "sum smaller than the witness that is supposed to bound it.",
    RATE_IMPLAUSIBLE: f"rate_bp is basis points of qualifying spend returned as value; "
    f"500 means 5%. It must be in 1..{MAX_RATE_BP}. A larger figure is a decimal shift.",
    EARN_RATE_MISSING: "An earn benefit needs rate_bp > 0.",
    EARN_CAP_NOT_VALUE_DENOMINATED: "capacity_minor on an earn benefit is the VALUE "
    "headroom left, not the qualifying-spend cap from the term sheet. Declare the spend "
    "figure as cap_qualifying_spend_minor and the validator will check the conversion. "
    "Term sheets write the cap in spend; putting a spend figure in a value slot overstates "
    "the benefit by a factor of 10000/rate_bp.",
    EARN_CAP_INCONSISTENT_WITH_RATE: "capacity_minor must equal "
    "cap_qualifying_spend_minor * rate_bp // 10000. See the expected figure in the message.",
    EARN_CARRIES_FLAT_VALUE: "flat_minor belongs to protections. An earn benefit's value "
    "is rate_bp against the line.",
    CREDIT_HAS_NO_BALANCE: "A credit declares capacity_minor: its remaining balance in "
    "minor units. An uncapped credit is not a credit.",
    CREDIT_CARRIES_RATE: "A credit offsets spend up to its balance; it has no rate_bp.",
    CREDIT_CARRIES_FLAT_VALUE: "A credit's value is its balance, not a flat_minor.",
    PROTECTION_HAS_NO_VALUE: "A protection declares flat_minor > 0, the value granted per "
    "qualifying line. If the cover cannot be priced without a claims-probability "
    "assumption, declare it as kind 'unpriced' instead — that is the honest option and the "
    "receipt still proves the agent saw it.",
    PROTECTION_CARRIES_RATE: "A protection's value is flat_minor, not a rate.",
    UNPRICED_CARRIES_NUMBERS: "An unpriced benefit is declared and considered, never "
    "scored. It carries no magnitudes at all.",
    UNPRICED_IN_EXCLUSIVITY_GROUP: "Exclusivity groups constrain allocation. An unpriced "
    "benefit is never allocated, so it cannot be in one.",
    SPEND_CAP_ON_NON_EARN: "cap_qualifying_spend_minor only applies to earn benefits.",
    EXCLUSIVITY_GROUP_EMPTY_NAME: "exclusivity_group is either null or a non-empty name.",
    EXCLUSIVITY_GROUP_OF_ONE: "A group with one member constrains nothing. This is almost "
    "always a typo that split a real pair — and the split silently permits both benefits "
    "on one line, which is exactly the double-count this system exists to prevent.",
    EXCLUSIVITY_GROUP_INERT: "The named member's eligibility cannot overlap any other "
    "member of its group, so the two can never compete for a line and the group is inert. "
    "Either widen the eligibility or drop the member from the group.",
    UNREACHABLE_ZERO_VALUE: "This benefit can never yield value by construction, so it "
    "would occupy a manifest slot and score zero forever. Give it a value or declare it as "
    "kind 'unpriced'.",
    UNREACHABLE_INVALID_MCC: f"MCCs are four-digit ISO 18245 codes in "
    f"{MCC_MIN}..{MCC_MAX}. A selector outside that range matches no line any cart can hold.",
    UNREACHABLE_EMPTY_SELECTOR: "An empty string in an eligibility selector matches "
    "nothing and narrows the benefit to unreachable. Remove the entry, or drop the whole "
    "selector list to mean 'no restriction'.",
    ACCEPTANCE_PREDICATE_PRESENT: "This system has no acceptance predicate, deliberately. "
    "An issuer-signed field naming where its own instrument is refused is a "
    "machine-readable instruction to route away from that issuer, with the issuer's "
    "signature on the reason. Acceptance belongs in the agent's routing layer, from data "
    "the agent already holds. Remove the field or the phrasing.",
    PROVENANCE_MISSING: "Every benefit carries a provenance string naming where its terms "
    "were read from — the section, page or clause. A number nobody can trace back to a "
    "document is a number nobody can contest.",
    PROVENANCE_PLACEHOLDER: f"provenance must be a real citation of at least "
    f"{MIN_PROVENANCE_CHARS} characters, not a placeholder.",
    MANIFEST_SOURCE_MISSING: "The manifest carries a 'source' naming the document these "
    "terms were modelled from.",
    SEAL_FORGED: "An AcceptedDraft can only be produced by validate_draft. Signing is "
    "gated on that object, structurally.",
    SEAL_PAYLOAD_MUTATED: "The sealed draft no longer hashes to what was validated.",
    SEAL_REVALIDATION_FAILED: "The sealed draft failed re-validation at signing time.",
    UNVERIFIED_AGAINST_SOURCE: "Structure and internal consistency are checked. Whether "
    "these figures match the issuer's real published terms is NOT and CANNOT be checked "
    "here. A human must read the source document.",
    ADVISORY_BALANCE_EXHAUSTED: "capacity_minor is 0: the balance is spent. This is member "
    "state, not a structural defect, and declaring it lets a receipt prove the agent saw a "
    "benefit and scored it at zero.",
    ADVISORY_NOT_ENROLLED: "Enrollment is required and not held, so this scores zero until "
    "the member enrolls. Declaring it is deliberate.",
    ADVISORY_UNCAPPED_EARN: "This earn benefit declares no cap. Uncapped is possible but "
    "rare; confirm the term sheet has no annual spend ceiling.",
    ADVISORY_UNCAPPED_PROTECTION: "This protection declares no capacity_minor, and a "
    "protection is granted once per qualifying line. Its asserted value is therefore "
    "flat_minor multiplied by the number of eligible lines, with no ceiling — an uncapped "
    "earn is still bounded by the spend it rates against, and an uncapped protection is "
    "bounded by nothing. Set capacity_minor to the most this can pay out in the window; "
    "for a benefit granted once, that is flat_minor itself.",
    ADVISORY_UNRESTRICTED_CREDIT: "This credit admits every line of every cart. Confirm "
    "the terms really place no merchant, MCC or category restriction on it.",
}


class AuthoringError(RuntimeError):
    """Raised when the signing gate is bypassed or a seal fails to verify.

    Never raised for a rejected draft — a rejection is a typed return value, not an
    exception. This is raised only when a caller tries to sign something the validator
    did not accept.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --------------------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One thing the validator noticed, at one path in the draft."""

    code: str
    path: str
    message: str
    severity: str = SEVERITY_ERROR

    @property
    def blocking(self) -> bool:
        return self.severity == SEVERITY_ERROR

    def guidance(self) -> str:
        return GUIDANCE.get(self.code, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
            "guidance": self.guidance(),
        }

    def display(self) -> str:
        return f"[{self.severity}] {self.code} at {self.path}: {self.message}"


@dataclass(frozen=True)
class ValidationReport:
    """The verdict on one draft, plus every finding that produced it."""

    verdict: str
    findings: tuple[Finding, ...]
    draft_hash: str
    manifest_hash: str | None = None
    benefit_count: int = 0
    priced_count: int = 0

    @property
    def accepted(self) -> bool:
        return self.verdict == VERDICT_ACCEPTED

    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    def advisories(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.blocking)

    def reason_codes(self) -> tuple[str, ...]:
        """Blocking codes, deduplicated, in first-seen order."""
        out: list[str] = []
        for f in self.errors():
            if f.code not in out:
                out.append(f.code)
        return tuple(out)

    def advisory_codes(self) -> tuple[str, ...]:
        out: list[str] = []
        for f in self.advisories():
            if f.code not in out:
                out.append(f.code)
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "accepted": self.accepted,
            "draft_hash": self.draft_hash,
            "manifest_hash": self.manifest_hash,
            "benefit_count": self.benefit_count,
            "priced_count": self.priced_count,
            "reason_codes": list(self.reason_codes()),
            "advisory_codes": list(self.advisory_codes()),
            "findings": [f.to_dict() for f in self.findings],
            "limitation": GUIDANCE[UNVERIFIED_AGAINST_SOURCE],
        }

    def display(self) -> str:
        head = f"{self.verdict}  ({len(self.errors())} error(s), {len(self.advisories())} advisory)"
        lines = [head]
        for f in self.findings:
            lines.append("  " + f.display())
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The signing gate
# --------------------------------------------------------------------------------------

# Module-private capability token. Only `validate_draft` holds a reference, so an
# AcceptedDraft cannot be constructed anywhere else — including by a caller who imports
# the class and tries. This is what makes "only a validated draft may be signed" a
# structural property rather than a convention in a docstring.
_GRANT = object()


@dataclass(frozen=True)
class AcceptedDraft:
    """A draft that passed validation, sealed against the payload that passed.

    Constructing one outside :func:`validate_draft` raises ``AuthoringError(SEAL_FORGED)``.
    :func:`sign_accepted` re-validates the sealed payload and re-checks the hash before it
    signs, so neither a forged seal nor a post-acceptance mutation reaches a signature.
    """

    manifest: Manifest
    report: ValidationReport
    payload_json: str
    manifest_hash: str
    grant: Any = None

    def __post_init__(self) -> None:
        if self.grant is not _GRANT:
            raise AuthoringError(
                SEAL_FORGED,
                "AcceptedDraft is produced by validate_draft and nowhere else; signing is "
                "gated on it structurally, so it cannot be constructed directly",
            )

    def payload(self) -> dict[str, Any]:
        """The exact normalized draft that was validated."""
        return json.loads(self.payload_json)

    def payload_hash(self) -> str:
        return hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest.manifest_id,
            "manifest_hash": self.manifest_hash,
            "payload_hash": self.payload_hash(),
            "report": self.report.to_dict(),
        }


# --------------------------------------------------------------------------------------
# Type helpers
# --------------------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number_findings(path: str, value: Any) -> list[Finding]:
    """Reject anything that is not a non-negative Python int.

    Order matters: bool is a subclass of int and 2500.0 == 2500, so a permissive check
    would let both through. Both are rejected with their own code rather than coerced,
    because coercion is the failure mode this whole module exists to prevent.
    """
    if isinstance(value, bool):
        return [Finding(NUMBER_IS_A_BOOL, path, f"expected an integer, got {value!r}")]
    if isinstance(value, float):
        return [
            Finding(
                NUMBER_IS_A_FLOAT,
                path,
                f"expected an integer count of minor units, got the float {value!r}",
            )
        ]
    if not isinstance(value, int):
        return [
            Finding(
                NUMBER_NOT_AN_INTEGER,
                path,
                f"expected an integer count of minor units, got {type(value).__name__} {value!r}",
            )
        ]
    if value < 0:
        return [Finding(NUMBER_NEGATIVE, path, f"expected a value >= 0, got {value}")]
    return []


def _string_findings(path: str, value: Any, *, required: bool) -> list[Finding]:
    if not isinstance(value, str):
        return [Finding(DRAFT_WRONG_TYPE, path, f"expected a string, got {type(value).__name__}")]
    if required and not value.strip():
        return [Finding(DRAFT_WRONG_TYPE, path, "expected a non-empty string")]
    return []


# --------------------------------------------------------------------------------------
# Acceptance-predicate screen
# --------------------------------------------------------------------------------------


def _walk(node: Any, path: str) -> Iterable[tuple[str, Any, Any]]:
    """Yield (path, key, value) for every mapping entry anywhere in the draft."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            yield here, key, value
            yield from _walk(value, here)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            yield from _walk(item, f"{path}[{i}]")


def scan_for_acceptance_predicate(draft: Any) -> tuple[Finding, ...]:
    """Keyword screen for acceptance predicates in keys and free text.

    Honest about what it is: a substring screen, not semantic analysis. It exists to return
    the right reason code to a drafting agent. The barrier is structural — `Benefit` has no
    acceptance field and unknown keys are rejected — so a phrasing this screen misses still
    cannot reach a signature.
    """
    found: list[Finding] = []
    for path, key, value in _walk(draft, ""):
        lowered_key = str(key).lower()
        for token in ACCEPTANCE_KEY_TOKENS:
            if token in lowered_key:
                found.append(
                    Finding(
                        ACCEPTANCE_PREDICATE_PRESENT,
                        path,
                        f"field name {str(key)!r} names where the instrument is or is not "
                        f"accepted; this schema has no such field, by design",
                    )
                )
                break
        if isinstance(value, str):
            lowered = value.lower()
            for phrase in ACCEPTANCE_VALUE_PHRASES:
                if phrase in lowered:
                    found.append(
                        Finding(
                            ACCEPTANCE_PREDICATE_PRESENT,
                            path,
                            f"text asserts where the instrument is refused ({phrase!r}); "
                            f"acceptance belongs in the agent's routing layer, never in an "
                            f"issuer-signed fact",
                        )
                    )
                    break
    return tuple(found)


# --------------------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Selectors:
    """Parsed eligibility, kept as sets so overlap questions are answerable."""

    mccs: frozenset[int] = frozenset()
    merchants: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()

    def restricted(self) -> bool:
        return bool(self.mccs or self.merchants or self.categories)


def _dimension_compatible(a: frozenset[Any], b: frozenset[Any]) -> bool:
    """Two selectors on one dimension can co-admit iff either is open or they intersect."""
    if not a or not b:
        return True
    return bool(a & b)


def _can_co_admit(a: _Selectors, b: _Selectors) -> bool:
    """Whether some single cart line could satisfy both eligibilities at once."""
    return (
        _dimension_compatible(a.mccs, b.mccs)
        and _dimension_compatible(a.merchants, b.merchants)
        and _dimension_compatible(a.categories, b.categories)
    )


def _validate_eligibility(raw: Any, path: str) -> tuple[list[Finding], _Selectors]:
    findings: list[Finding] = []
    if raw is None:
        return findings, _Selectors()
    if not isinstance(raw, Mapping):
        return [Finding(DRAFT_WRONG_TYPE, path, "eligibility must be an object")], _Selectors()

    for key in raw:
        if key not in ELIGIBILITY_FIELDS:
            findings.append(
                Finding(
                    DRAFT_UNKNOWN_FIELD,
                    f"{path}.{key}",
                    f"unknown eligibility selector {str(key)!r}; known selectors are "
                    f"{list(ELIGIBILITY_FIELDS)}",
                )
            )

    mccs: set[int] = set()
    raw_mccs = raw.get("mccs", [])
    if not isinstance(raw_mccs, (list, tuple)):
        findings.append(Finding(DRAFT_WRONG_TYPE, f"{path}.mccs", "mccs must be an array"))
    else:
        for i, item in enumerate(raw_mccs):
            here = f"{path}.mccs[{i}]"
            num = _number_findings(here, item)
            if num:
                findings.extend(num)
                continue
            if not (MCC_MIN <= int(item) <= MCC_MAX):
                findings.append(
                    Finding(
                        UNREACHABLE_INVALID_MCC,
                        here,
                        f"{item} is not a four-digit MCC, so this selector admits no line "
                        f"any cart could hold",
                    )
                )
                continue
            mccs.add(int(item))

    strings: dict[str, set[str]] = {"merchants": set(), "categories": set()}
    for field_name in ("merchants", "categories"):
        raw_list = raw.get(field_name, [])
        if not isinstance(raw_list, (list, tuple)):
            findings.append(
                Finding(DRAFT_WRONG_TYPE, f"{path}.{field_name}", f"{field_name} must be an array")
            )
            continue
        for i, item in enumerate(raw_list):
            here = f"{path}.{field_name}[{i}]"
            if not isinstance(item, str):
                findings.append(
                    Finding(DRAFT_WRONG_TYPE, here, f"expected a string, got {type(item).__name__}")
                )
                continue
            if not item.strip():
                findings.append(
                    Finding(
                        UNREACHABLE_EMPTY_SELECTOR,
                        here,
                        "an empty selector entry matches nothing and narrows this benefit "
                        "to unreachable",
                    )
                )
                continue
            strings[field_name].add(item)

    return findings, _Selectors(
        mccs=frozenset(mccs),
        merchants=frozenset(strings["merchants"]),
        categories=frozenset(strings["categories"]),
    )


# --------------------------------------------------------------------------------------
# Per-benefit validation
# --------------------------------------------------------------------------------------


@dataclass
class _BenefitDraft:
    """One benefit after parsing, carried forward for cross-benefit checks."""

    benefit_id: str
    kind: str
    selectors: _Selectors
    group: str | None
    rate_bp: int
    capacity_minor: int | None
    flat_minor: int
    cap_spend_minor: int | None
    provenance: str
    label: str
    note: str
    window: str
    requires_enrollment: bool
    enrolled: bool
    sound: bool = True


def _validate_benefit(raw: Any, index: int) -> tuple[list[Finding], _BenefitDraft | None]:
    path = f"benefits[{index}]"
    findings: list[Finding] = []
    if not isinstance(raw, Mapping):
        return [Finding(DRAFT_WRONG_TYPE, path, "each benefit must be an object")], None

    for key in raw:
        if key not in BENEFIT_FIELDS:
            findings.append(
                Finding(
                    DRAFT_UNKNOWN_FIELD,
                    f"{path}.{key}",
                    f"unknown benefit field {str(key)!r}; the schema is closed and known "
                    f"fields are {list(BENEFIT_FIELDS)}",
                )
            )
    for key in BENEFIT_REQUIRED_FIELDS:
        if key not in raw:
            findings.append(
                Finding(DRAFT_MISSING_FIELD, f"{path}.{key}", f"required field {key!r} is absent")
            )

    benefit_id = raw.get("benefit_id")
    if isinstance(benefit_id, str) and not benefit_id.strip():
        findings.append(Finding(DRAFT_EMPTY_BENEFIT_ID, f"{path}.benefit_id", "benefit_id is empty"))
    elif benefit_id is not None and not isinstance(benefit_id, str):
        findings.extend(_string_findings(f"{path}.benefit_id", benefit_id, required=True))
    bid = benefit_id if isinstance(benefit_id, str) else f"<benefit {index}>"

    kind = raw.get("kind")
    if kind is not None and kind not in KNOWN_KINDS:
        findings.append(
            Finding(
                DRAFT_UNKNOWN_KIND,
                f"{path}.kind",
                f"{kind!r} is not a benefit kind; expected one of {list(KNOWN_KINDS)}",
            )
        )
        kind = None

    findings.extend(_string_findings(f"{path}.label", raw.get("label", ""), required=True))

    # provenance
    provenance = raw.get("provenance")
    if provenance is None:
        if "provenance" in raw:
            findings.append(
                Finding(PROVENANCE_MISSING, f"{path}.provenance", "provenance is null")
            )
        provenance = ""
    elif not isinstance(provenance, str):
        findings.append(
            Finding(
                DRAFT_WRONG_TYPE,
                f"{path}.provenance",
                f"provenance must be a string, got {type(provenance).__name__}",
            )
        )
        provenance = ""
    else:
        normalized = provenance.strip().lower().rstrip(".")
        if normalized in PROVENANCE_PLACEHOLDERS or len(provenance.strip()) < MIN_PROVENANCE_CHARS:
            findings.append(
                Finding(
                    PROVENANCE_PLACEHOLDER,
                    f"{path}.provenance",
                    f"{provenance!r} does not identify a source; a number nobody can trace "
                    f"back to a document is a number nobody can contest",
                )
            )

    # numbers
    numbers: dict[str, Any] = {}
    for name in NUMERIC_FIELDS:
        if name not in raw or raw.get(name) is None:
            numbers[name] = None
            continue
        num_findings = _number_findings(f"{path}.{name}", raw[name])
        if num_findings:
            findings.extend(num_findings)
            numbers[name] = None
        else:
            numbers[name] = int(raw[name])

    rate_bp = numbers["rate_bp"] or 0
    flat_minor = numbers["flat_minor"] or 0
    capacity_minor = numbers["capacity_minor"]
    cap_spend = numbers["cap_qualifying_spend_minor"]

    if rate_bp > MAX_RATE_BP:
        findings.append(
            Finding(
                RATE_IMPLAUSIBLE,
                f"{path}.rate_bp",
                f"{rate_bp} bp returns more than 100% of qualifying spend as value; a "
                f"figure this size is a decimal shift, not a card",
            )
        )

    # window / enrollment
    window = raw.get("window", WINDOW_NONE)
    if window is None:
        window = WINDOW_NONE
    if window not in KNOWN_WINDOWS:
        findings.append(
            Finding(
                DRAFT_UNKNOWN_WINDOW,
                f"{path}.window",
                f"{window!r} is not a reset window; expected one of {list(KNOWN_WINDOWS)}",
            )
        )
        window = WINDOW_NONE
    for flag in ("requires_enrollment", "enrolled"):
        if flag in raw and not isinstance(raw[flag], bool):
            findings.append(
                Finding(DRAFT_WRONG_TYPE, f"{path}.{flag}", f"{flag} must be a boolean")
            )
    requires_enrollment = bool(raw.get("requires_enrollment", False))
    enrolled = bool(raw.get("enrolled", True))

    note = raw.get("note", "")
    if not isinstance(note, str):
        findings.append(Finding(DRAFT_WRONG_TYPE, f"{path}.note", "note must be a string"))
        note = ""

    # exclusivity group
    group = raw.get("exclusivity_group")
    if group is not None:
        if not isinstance(group, str):
            findings.append(
                Finding(DRAFT_WRONG_TYPE, f"{path}.exclusivity_group", "group name must be a string")
            )
            group = None
        elif not group.strip():
            findings.append(
                Finding(
                    EXCLUSIVITY_GROUP_EMPTY_NAME,
                    f"{path}.exclusivity_group",
                    "an empty group name groups nothing; use null for no group",
                )
            )
            group = None

    elig_findings, selectors = _validate_eligibility(raw.get("eligibility"), f"{path}.eligibility")
    findings.extend(elig_findings)

    # per-kind coherence and reachability
    if kind == KIND_EARN:
        if rate_bp <= 0:
            findings.append(
                Finding(
                    EARN_RATE_MISSING if "rate_bp" not in raw else UNREACHABLE_ZERO_VALUE,
                    f"{path}.rate_bp",
                    "an earn benefit with no rate yields nothing on every line, forever",
                )
            )
        if flat_minor:
            findings.append(
                Finding(
                    EARN_CARRIES_FLAT_VALUE,
                    f"{path}.flat_minor",
                    "flat_minor belongs to protections",
                )
            )
        if capacity_minor is not None and cap_spend is None:
            findings.append(
                Finding(
                    EARN_CAP_NOT_VALUE_DENOMINATED,
                    f"{path}.capacity_minor",
                    "an earn cap is VALUE headroom, and the draft does not say what "
                    "qualifying-spend figure it was derived from, so the conversion cannot "
                    "be checked",
                )
            )
        elif capacity_minor is not None and cap_spend is not None and rate_bp > 0:
            expected = (cap_spend * rate_bp) // 10_000
            if capacity_minor != expected:
                findings.append(
                    Finding(
                        EARN_CAP_INCONSISTENT_WITH_RATE,
                        f"{path}.capacity_minor",
                        f"{cap_spend} of qualifying spend at {rate_bp} bp is {expected} of "
                        f"value, not {capacity_minor}"
                        + (
                            "; the draft carries the spend cap in the value slot, which "
                            "overstates this benefit"
                            if capacity_minor == cap_spend
                            else ""
                        ),
                    )
                )
        elif capacity_minor is None and cap_spend is not None:
            findings.append(
                Finding(
                    EARN_CAP_NOT_VALUE_DENOMINATED,
                    f"{path}.capacity_minor",
                    f"a qualifying-spend cap of {cap_spend} was declared without the value "
                    f"headroom it implies",
                )
            )
        elif capacity_minor is None and cap_spend is None:
            findings.append(
                Finding(
                    ADVISORY_UNCAPPED_EARN,
                    f"{path}.capacity_minor",
                    "no annual cap declared on this earn benefit",
                    severity=SEVERITY_ADVISORY,
                )
            )
    elif kind == KIND_CREDIT:
        if rate_bp:
            findings.append(
                Finding(CREDIT_CARRIES_RATE, f"{path}.rate_bp", "a credit has no rate")
            )
        if flat_minor:
            findings.append(
                Finding(
                    CREDIT_CARRIES_FLAT_VALUE,
                    f"{path}.flat_minor",
                    "a credit's value is its remaining balance",
                )
            )
        if cap_spend is not None:
            findings.append(
                Finding(
                    SPEND_CAP_ON_NON_EARN,
                    f"{path}.cap_qualifying_spend_minor",
                    "qualifying-spend caps convert rates to value; a credit has no rate",
                )
            )
        if capacity_minor is None:
            findings.append(
                Finding(
                    CREDIT_HAS_NO_BALANCE,
                    f"{path}.capacity_minor",
                    "a credit without a remaining balance is unbounded, and an unbounded "
                    "credit is not a credit",
                )
            )
        elif capacity_minor == 0:
            # State, not structure: the balance is spent. Declaring an exhausted credit is
            # deliberate — it lets a receipt prove the agent saw it and scored it at zero.
            findings.append(
                Finding(
                    ADVISORY_BALANCE_EXHAUSTED,
                    f"{path}.capacity_minor",
                    "remaining balance is zero, so this scores zero on every cart",
                    severity=SEVERITY_ADVISORY,
                )
            )
        if not selectors.restricted():
            findings.append(
                Finding(
                    ADVISORY_UNRESTRICTED_CREDIT,
                    f"{path}.eligibility",
                    "this credit admits every line of every cart",
                    severity=SEVERITY_ADVISORY,
                )
            )
    elif kind == KIND_PROTECTION:
        if rate_bp:
            findings.append(
                Finding(PROTECTION_CARRIES_RATE, f"{path}.rate_bp", "a protection has no rate")
            )
        if cap_spend is not None:
            findings.append(
                Finding(
                    SPEND_CAP_ON_NON_EARN,
                    f"{path}.cap_qualifying_spend_minor",
                    "qualifying-spend caps convert rates to value; a protection has no rate",
                )
            )
        if flat_minor <= 0:
            findings.append(
                Finding(
                    PROTECTION_HAS_NO_VALUE if "flat_minor" not in raw else UNREACHABLE_ZERO_VALUE,
                    f"{path}.flat_minor",
                    "a protection worth zero per line can never appear in a witness; "
                    "declare it as kind 'unpriced' if it cannot be priced honestly",
                )
            )
        elif capacity_minor is None:
            # capacity_minor is the only structural bound a protection has. Nothing ties
            # its value to the line it attaches to — deliberately, since a $100 property
            # credit on a $40 room is genuinely worth $100 — so without a cap the asserted
            # figure grows with the line count and no cart is large enough to bound it.
            findings.append(
                Finding(
                    ADVISORY_UNCAPPED_PROTECTION,
                    f"{path}.capacity_minor",
                    "no capacity declared, so this protection is granted once per eligible "
                    "line with no ceiling on the total",
                    severity=SEVERITY_ADVISORY,
                )
            )
    elif kind == KIND_UNPRICED:
        carried = [n for n in NUMERIC_FIELDS if numbers.get(n)]
        if carried:
            findings.append(
                Finding(
                    UNPRICED_CARRIES_NUMBERS,
                    f"{path}.{carried[0]}",
                    f"an unpriced benefit declares no magnitudes; found {carried}",
                )
            )
        if group is not None:
            findings.append(
                Finding(
                    UNPRICED_IN_EXCLUSIVITY_GROUP,
                    f"{path}.exclusivity_group",
                    "an unpriced benefit is never allocated, so it cannot compete for a line",
                )
            )

    if kind in PRICED_KINDS and requires_enrollment and not enrolled:
        findings.append(
            Finding(
                ADVISORY_NOT_ENROLLED,
                f"{path}.enrolled",
                "enrollment required and not held, so this scores zero until the member enrolls",
                severity=SEVERITY_ADVISORY,
            )
        )

    parsed = _BenefitDraft(
        benefit_id=bid,
        kind=kind or "",
        selectors=selectors,
        group=group,
        rate_bp=rate_bp,
        capacity_minor=capacity_minor,
        flat_minor=flat_minor,
        cap_spend_minor=cap_spend,
        provenance=provenance if isinstance(provenance, str) else "",
        label=raw.get("label", "") if isinstance(raw.get("label", ""), str) else "",
        note=note,
        window=window,
        requires_enrollment=requires_enrollment,
        enrolled=enrolled,
        sound=not any(f.blocking for f in findings),
    )
    return findings, parsed


# --------------------------------------------------------------------------------------
# Cross-benefit checks
# --------------------------------------------------------------------------------------


def _validate_groups(parsed: Sequence[_BenefitDraft]) -> list[Finding]:
    """Exclusivity groups must have at least two members that can actually collide.

    A group of one constrains nothing and is almost always a typo that split a real pair —
    and the split silently permits both benefits on one line, which is precisely the
    double-count the allocator exists to prevent. A member whose eligibility cannot overlap
    any other member's is in the group for decoration only.
    """
    findings: list[Finding] = []
    groups: dict[str, list[_BenefitDraft]] = {}
    for b in parsed:
        if b.group and b.kind in PRICED_KINDS:
            groups.setdefault(b.group, []).append(b)

    for name, members in sorted(groups.items()):
        if len(members) < 2:
            findings.append(
                Finding(
                    EXCLUSIVITY_GROUP_OF_ONE,
                    f"exclusivity_group[{name}]",
                    f"group {name!r} has one member ({members[0].benefit_id!r}); a group of "
                    f"one constrains nothing and lets a benefit that should compete stack",
                )
            )
            continue
        for b in members:
            if not any(other is not b and _can_co_admit(b.selectors, other.selectors) for other in members):
                findings.append(
                    Finding(
                        EXCLUSIVITY_GROUP_INERT,
                        f"exclusivity_group[{name}]",
                        f"{b.benefit_id!r} cannot admit any line that another member of "
                        f"{name!r} also admits, so the exclusivity never binds",
                    )
                )
    return findings


# --------------------------------------------------------------------------------------
# Materialization
# --------------------------------------------------------------------------------------


def _compose_note(b: _BenefitDraft) -> str:
    """Fold provenance and any cap derivation into the note, so both get signed.

    `Benefit` has no provenance field of its own, and the signature is over the benefit
    dict. Carrying the citation anywhere else would leave it outside the signed bytes,
    where it could be dropped without breaking a signature.
    """
    parts: list[str] = []
    if b.note.strip():
        parts.append(b.note.strip())
    if b.provenance.strip():
        parts.append(f"source: {b.provenance.strip()}")
    if b.kind == KIND_EARN and b.cap_spend_minor is not None and b.capacity_minor is not None:
        parts.append(
            f"cap derived: {b.cap_spend_minor} qualifying spend at {b.rate_bp} bp = "
            f"{b.capacity_minor} value headroom"
        )
    return " | ".join(parts)


def _materialize(draft: Mapping[str, Any], parsed: Sequence[_BenefitDraft]) -> Manifest:
    benefits = []
    for b in parsed:
        benefits.append(
            Benefit(
                benefit_id=b.benefit_id,
                kind=b.kind,
                label=b.label,
                eligibility=Eligibility(
                    mccs=tuple(sorted(b.selectors.mccs)),
                    merchants=tuple(sorted(b.selectors.merchants)),
                    categories=tuple(sorted(b.selectors.categories)),
                ),
                rate_bp=b.rate_bp,
                capacity_minor=b.capacity_minor,
                flat_minor=b.flat_minor,
                exclusivity_group=b.group,
                window=b.window,
                requires_enrollment=b.requires_enrollment,
                enrolled=b.enrolled,
                note=_compose_note(b),
            )
        )
    source = str(draft.get("source", "")).strip()
    if UNVERIFIED_SUFFIX.strip() not in source:
        source = source + UNVERIFIED_SUFFIX
    return build_manifest(
        manifest_id=str(draft["manifest_id"]),
        issuer=str(draft["issuer"]),
        product=str(draft["product"]),
        benefits=benefits,
        issued_at=int(draft["issued_at"]),
        currency=str(draft["currency"]),
        source=source,
    )


# --------------------------------------------------------------------------------------
# The validator
# --------------------------------------------------------------------------------------


def normalize_draft(draft: Any) -> str:
    """Canonical bytes for a draft, as a str, so a seal can be hash-checked."""
    return canonical_json(draft).decode("utf-8")


def draft_hash(draft: Any) -> str:
    return hashlib.sha256(canonical_json(draft)).hexdigest()


def validate_draft(draft: Any) -> tuple[ValidationReport, AcceptedDraft | None]:
    """Validate a draft manifest. The only producer of :class:`AcceptedDraft`.

    Returns ``(report, accepted_or_None)``. A rejected draft is never repaired: the caller
    gets reason codes and revises. Nothing in this function edits a number.

    Reminder, because it is the thing most easily overclaimed: acceptance means the draft
    is *internally consistent and structurally sound*. It does not mean the figures are the
    issuer's real figures. That check is not automatable here and is not attempted.
    """
    findings: list[Finding] = []

    if not isinstance(draft, Mapping):
        report = ValidationReport(
            verdict=VERDICT_REJECTED,
            findings=(
                Finding(
                    DRAFT_NOT_AN_OBJECT,
                    "$",
                    f"expected a JSON object, got {type(draft).__name__}",
                ),
                _limitation_finding(),
            ),
            draft_hash=hashlib.sha256(repr(draft).encode("utf-8")).hexdigest(),
        )
        return report, None

    d_hash = draft_hash(draft)

    # The deleted field first, so its code leads the list a drafting agent reads.
    findings.extend(scan_for_acceptance_predicate(draft))

    for key in draft:
        if key not in MANIFEST_FIELDS:
            findings.append(
                Finding(
                    DRAFT_UNKNOWN_FIELD,
                    str(key),
                    f"unknown manifest field {str(key)!r}; the schema is closed and known "
                    f"fields are {list(MANIFEST_FIELDS)}",
                )
            )
    for key in MANIFEST_REQUIRED_FIELDS:
        if key not in draft:
            findings.append(
                Finding(DRAFT_MISSING_FIELD, str(key), f"required field {key!r} is absent")
            )

    version = draft.get("version")
    if version is not None and version != DRAFT_VERSION:
        findings.append(
            Finding(DRAFT_VERSION_UNKNOWN, "version", f"{version!r} is not {DRAFT_VERSION!r}")
        )

    for key in ("manifest_id", "issuer", "product"):
        if key in draft:
            findings.extend(_string_findings(key, draft[key], required=True))

    currency = draft.get("currency")
    if currency is not None and currency not in KNOWN_CURRENCIES:
        findings.append(
            Finding(
                DRAFT_UNKNOWN_CURRENCY,
                "currency",
                f"{currency!r} is not a supported currency; expected one of "
                f"{list(KNOWN_CURRENCIES)}",
            )
        )

    issued_at = draft.get("issued_at")
    if issued_at is not None:
        num = _number_findings("issued_at", issued_at)
        if num:
            findings.extend(num)
        elif int(issued_at) <= 0:
            findings.append(
                Finding(DRAFT_ISSUED_AT_INVALID, "issued_at", "issued_at must be positive")
            )

    source = draft.get("source")
    if not isinstance(source, str) or len(source.strip()) < MIN_PROVENANCE_CHARS:
        findings.append(
            Finding(
                MANIFEST_SOURCE_MISSING,
                "source",
                "the manifest must name the document these terms were modelled from",
            )
        )

    raw_benefits = draft.get("benefits")
    parsed: list[_BenefitDraft] = []
    if raw_benefits is None:
        pass  # already reported as a missing field
    elif not isinstance(raw_benefits, (list, tuple)):
        findings.append(Finding(DRAFT_WRONG_TYPE, "benefits", "benefits must be an array"))
    elif not raw_benefits:
        findings.append(
            Finding(DRAFT_EMPTY_MANIFEST, "benefits", "a manifest with no benefits declares nothing")
        )
    else:
        seen: dict[str, int] = {}
        for i, raw in enumerate(raw_benefits):
            b_findings, b = _validate_benefit(raw, i)
            findings.extend(b_findings)
            if b is None:
                continue
            if b.benefit_id in seen:
                findings.append(
                    Finding(
                        DRAFT_DUPLICATE_BENEFIT_ID,
                        f"benefits[{i}].benefit_id",
                        f"{b.benefit_id!r} was already declared at benefits[{seen[b.benefit_id]}]; "
                        f"ids address capacity buckets, so a shared id shares a pool by accident",
                    )
                )
            else:
                seen[b.benefit_id] = i
            parsed.append(b)
        findings.extend(_validate_groups(parsed))

    findings.append(_limitation_finding())

    blocking = [f for f in findings if f.blocking]
    if blocking:
        report = ValidationReport(
            verdict=VERDICT_REJECTED,
            findings=tuple(findings),
            draft_hash=d_hash,
            benefit_count=len(parsed),
            priced_count=sum(1 for b in parsed if b.kind in PRICED_KINDS),
        )
        return report, None

    try:
        manifest = _materialize(draft, parsed)
    except (ManifestError, KeyError, TypeError, ValueError) as exc:
        # The field checks above should make this unreachable. If it fires, the draft is
        # rejected rather than patched — the constructor is the authority on its own
        # invariants and this validator does not second-guess it.
        findings.append(Finding(DRAFT_MATERIALIZATION_FAILED, "$", str(exc)))
        report = ValidationReport(
            verdict=VERDICT_REJECTED,
            findings=tuple(findings),
            draft_hash=d_hash,
            benefit_count=len(parsed),
            priced_count=sum(1 for b in parsed if b.kind in PRICED_KINDS),
        )
        return report, None

    report = ValidationReport(
        verdict=VERDICT_ACCEPTED,
        findings=tuple(findings),
        draft_hash=d_hash,
        manifest_hash=manifest.content_hash(),
        benefit_count=len(parsed),
        priced_count=sum(1 for b in parsed if b.kind in PRICED_KINDS),
    )
    accepted = AcceptedDraft(
        manifest=manifest,
        report=report,
        payload_json=normalize_draft(draft),
        manifest_hash=manifest.content_hash(),
        grant=_GRANT,
    )
    return report, accepted


def _limitation_finding() -> Finding:
    """Always present, on every report, accepted or rejected.

    The honest limitation travels with the verdict rather than living in a docstring a
    caller can skip. Anything that renders a report renders this too.
    """
    return Finding(
        UNVERIFIED_AGAINST_SOURCE,
        "$",
        "internal consistency and structure checked; correspondence to the issuer's real "
        "published terms NOT checked and not checkable here",
        severity=SEVERITY_ADVISORY,
    )


# --------------------------------------------------------------------------------------
# Signing — gated on AcceptedDraft, re-validated at the gate
# --------------------------------------------------------------------------------------


def sign_accepted(
    accepted: AcceptedDraft, key: str | bytes, key_id: str = "prototype"
) -> SignedManifest:
    """Sign a validated draft. The only signing path in this module.

    Three gates, in order, because each catches something the others do not:

      1. the argument must be an :class:`AcceptedDraft`, which only ``validate_draft``
         produces — so a raw dict has no route here at all;
      2. the sealed payload is re-validated from scratch, so a draft that was accepted
         under different code, or tampered with in transit, is caught;
      3. the re-materialized manifest must hash to what was sealed.

    Raises :class:`AuthoringError` on any of the three. Never returns a signature over
    something that did not pass.
    """
    if not isinstance(accepted, AcceptedDraft):
        raise AuthoringError(
            SEAL_FORGED,
            f"expected an AcceptedDraft from validate_draft, got {type(accepted).__name__}; "
            f"there is no path from a raw draft to a signature",
        )
    payload = accepted.payload()
    report, revalidated = validate_draft(payload)
    if revalidated is None or not report.accepted:
        raise AuthoringError(
            SEAL_REVALIDATION_FAILED,
            f"sealed draft failed re-validation at signing time: {list(report.reason_codes())}",
        )
    if revalidated.manifest_hash != accepted.manifest_hash:
        raise AuthoringError(
            SEAL_PAYLOAD_MUTATED,
            f"sealed manifest hash {accepted.manifest_hash} does not match the hash of the "
            f"re-validated payload {revalidated.manifest_hash}",
        )
    if accepted.manifest.content_hash() != accepted.manifest_hash:
        raise AuthoringError(
            SEAL_PAYLOAD_MUTATED,
            "the sealed manifest object no longer hashes to the sealed hash",
        )
    return sign_manifest(accepted.manifest, key, key_id=key_id)


def validate_and_sign(
    draft: Any, key: str | bytes, key_id: str = "prototype"
) -> tuple[ValidationReport, SignedManifest | None]:
    """Convenience for the whole path. Returns ``(report, None)`` on rejection."""
    report, accepted = validate_draft(draft)
    if accepted is None:
        return report, None
    return report, sign_accepted(accepted, key, key_id=key_id)


# --------------------------------------------------------------------------------------
# Schema description, for whoever is drafting
# --------------------------------------------------------------------------------------


def draft_schema() -> dict[str, Any]:
    """Machine-readable draft schema. Used as a tool input schema by the drafting agent."""
    return {
        "type": "object",
        "required": list(MANIFEST_REQUIRED_FIELDS),
        "properties": {
            "version": {"type": "string", "const": DRAFT_VERSION},
            "manifest_id": {"type": "string"},
            "issuer": {"type": "string"},
            "product": {"type": "string"},
            "currency": {"type": "string", "enum": list(KNOWN_CURRENCIES)},
            "issued_at": {"type": "integer", "description": "Unix seconds, passed in explicitly."},
            "source": {
                "type": "string",
                "description": "The document these terms were modelled from.",
            },
            "benefits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": list(BENEFIT_REQUIRED_FIELDS),
                    "properties": {
                        "benefit_id": {"type": "string"},
                        "kind": {"type": "string", "enum": list(KNOWN_KINDS)},
                        "label": {"type": "string"},
                        "provenance": {
                            "type": "string",
                            "description": "Where in the terms this benefit was read from.",
                        },
                        "eligibility": {
                            "type": "object",
                            "properties": {
                                "mccs": {"type": "array", "items": {"type": "integer"}},
                                "merchants": {"type": "array", "items": {"type": "string"}},
                                "categories": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        "rate_bp": {
                            "type": "integer",
                            "description": "earn only: basis points of qualifying spend returned as value.",
                        },
                        "cap_qualifying_spend_minor": {
                            "type": "integer",
                            "description": "earn only: the qualifying-spend cap from the term sheet.",
                        },
                        "capacity_minor": {
                            "type": "integer",
                            "description": "VALUE headroom in minor units. For earn benefits this is cap_qualifying_spend_minor * rate_bp // 10000, not the spend figure.",
                        },
                        "flat_minor": {
                            "type": "integer",
                            "description": "protection only: value granted per qualifying line.",
                        },
                        "exclusivity_group": {"type": ["string", "null"]},
                        "window": {"type": "string", "enum": list(KNOWN_WINDOWS)},
                        "requires_enrollment": {"type": "boolean"},
                        "enrolled": {"type": "boolean"},
                        "note": {"type": "string"},
                    },
                },
            },
        },
    }


def schema_help() -> str:
    """Prose the drafting agent is given up front. Deterministic, never model-authored."""
    return "\n".join(
        [
            "DRAFT MANIFEST SCHEMA",
            "",
            "Money is an integer count of MINOR units. $25.00 is 2500. Never a float, never a string.",
            "",
            "Manifest fields (required): " + ", ".join(MANIFEST_REQUIRED_FIELDS),
            "Manifest fields (optional): " + ", ".join(MANIFEST_OPTIONAL_FIELDS),
            "Benefit fields (required):  " + ", ".join(BENEFIT_REQUIRED_FIELDS),
            "Benefit fields (optional):  " + ", ".join(BENEFIT_OPTIONAL_FIELDS),
            "The schema is CLOSED: any other field is rejected.",
            "",
            "kind:",
            "  earn        rate_bp basis points of qualifying spend returned as value (500 = 5%).",
            "              A cap from the term sheet is a SPEND figure. Declare it as",
            "              cap_qualifying_spend_minor and set capacity_minor to the VALUE it",
            "              implies: cap_qualifying_spend_minor * rate_bp // 10000.",
            "  credit      capacity_minor is the remaining balance. No rate, no flat value.",
            "  protection  flat_minor is the value granted per qualifying line. No rate.",
            "  unpriced    declared and considered, never scored. Carries no numbers at all.",
            "              Use this when pricing would need an assumption you do not have.",
            "",
            "exclusivity_group: benefits that may not both attach to one line. A group needs at",
            "  least two members whose eligibility can overlap, or it constrains nothing.",
            "",
            "provenance: every benefit names where in the terms it was read from. Required.",
            "",
            "There is no acceptance predicate in this schema and there will not be one. Nothing",
            "here may name where the instrument is refused.",
            "",
            "Windows: " + ", ".join(KNOWN_WINDOWS),
            "Currencies: " + ", ".join(KNOWN_CURRENCIES),
        ]
    )


def reason_catalogue() -> dict[str, str]:
    """Every reason code and its fix guidance. Deterministic text, not model output."""
    return dict(GUIDANCE)
