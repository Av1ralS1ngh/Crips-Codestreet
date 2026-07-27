"""Cart + signed manifests -> per-instrument value, a derivation tree, and a refusal.

This is the function an agent calls at the moment it is about to choose an instrument. It
answers one question per candidate card — *what is this cart worth on this card, and can
you prove it* — and it answers a second question the industry currently has no place to
put: *what happens when it cannot be proved.*

Three properties hold, and each one is a defence against a specific way this could go
wrong.

**Conservatism by construction.** A value is asserted only when `allocate()` has produced a
concrete allocation and `verify_witness()` has re-checked it: every assignment eligible,
every capacity respected, every exclusivity group honoured, every number re-added. Because
the exhibited allocation is achievable, the asserted value cannot exceed the true optimum.
Nothing here proves the allocation is *optimal* — the greedy allocator is not, and the
offline oracle measures how far below it sits. Understating is safe; overstating is the one
error that must never happen.

**Refusal is a typed, first-class output.** When a witness cannot be produced or verified,
or when a value proposed from outside is not supported by one, this module does not fall
back to a smaller number, a cached number or a marketing number. It returns a `Refusal`
with a reason code and a remedy, drops that instrument out of the ranking entirely, and
records it in the candidate set so the receipt shows the refusal happened. A system that
visibly declines to make a claim is worth more than one that always has an answer.

**The ranking is outside the signature boundary, structurally.** `InstrumentValuation.
attestable_body()` — the part an issuer could sign — contains facts about *one* instrument
on *one* cart: the manifest hash, the cart hash, the witness, the value it realizes. It
contains no rank, no policy, no baseline and no other instrument's identifier. Evaluating
the same cart with one competitor manifest added produces byte-identical attestable bytes
for every instrument already present, which is what makes "the issuer never signs *we beat
another card on this cart*" a property of the code rather than a promise on a slide. The
ranking lives in a separate `Ranking` object carrying the hash of the valuation policy that
produced it, and `Ranking.issuer_endorsed` is a read-only False.

Two further rules this module holds to:

  * The LLM proposes; this function disposes. A proposed value arrives as `claims` and is
    only ever used as a *hypothesis to reject*. No number from a model, a merchant or a
    marketing page becomes the asserted value of anything.
  * Nothing here is member-facing. The output feeds an agent's ranking. Benefit expense is
    recognised as benefits are used, so a surface that nudged a member to burn an unused
    credit would inflate the very line this exists to explain.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from caveat.cart import Cart
from caveat.money import fmt_currency

if TYPE_CHECKING:  # pragma: no cover - typing only
    from caveat.adapters import NormalizedRequest, TranslationWarning

from .allocate import allocate, naive_sum
from .manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    Benefit,
    Manifest,
    SignedManifest,
    canonical_json,
    verify_manifest,
)
from .witness import Verification, Witness, verify_witness

EVALUATION_VERSION = "plumbline/evaluation/1"

BP = 10_000

# --------------------------------------------------------------------------------------
# Outcome vocabulary. Closed sets, module-level constants.
# --------------------------------------------------------------------------------------

STATUS_ATTESTED = "ATTESTED"
STATUS_REFUSED = "REFUSED"

# Refusals. Each one means "no value is asserted for this subject", never "a smaller value
# is asserted quietly".
REFUSE_MANIFEST_UNSIGNED = "PLUMBLINE_REFUSE_MANIFEST_UNSIGNED"
REFUSE_MANIFEST_KEY_UNKNOWN = "PLUMBLINE_REFUSE_MANIFEST_KEY_UNKNOWN"
REFUSE_MANIFEST_SIGNATURE = "PLUMBLINE_REFUSE_MANIFEST_SIGNATURE_INVALID"
REFUSE_MANIFEST_POSTDATED = "PLUMBLINE_REFUSE_MANIFEST_POSTDATED"
REFUSE_MANIFEST_STALE = "PLUMBLINE_REFUSE_MANIFEST_STALE"
REFUSE_DUPLICATE_MANIFEST = "PLUMBLINE_REFUSE_DUPLICATE_MANIFEST_ID"
REFUSE_CURRENCY_MISMATCH = "PLUMBLINE_REFUSE_CURRENCY_MISMATCH"
REFUSE_WITNESS_INVALID = "PLUMBLINE_REFUSE_WITNESS_FAILED_VERIFICATION"
REFUSE_WITNESS_CART_MISMATCH = "PLUMBLINE_REFUSE_WITNESS_CART_MISMATCH"
REFUSE_CLAIM_UNSUPPORTED = "PLUMBLINE_REFUSE_CLAIM_UNSUPPORTED_BY_WITNESS"
REFUSE_EMPTY_CART = "PLUMBLINE_REFUSE_EMPTY_CART"
REFUSE_DUPLICATE_SKU = "PLUMBLINE_REFUSE_CART_DUPLICATE_SKU"
REFUSE_NO_ATTESTABLE_INSTRUMENT = "PLUMBLINE_REFUSE_NO_ATTESTABLE_INSTRUMENT"

# Disclosures. A disclosure is a fact about how the number was reached, not a decision.
DISCLOSE_LINE_DETAIL_MISSING = "PLUMBLINE_DISCLOSE_LINE_DETAIL_MISSING"
DISCLOSE_UNPRICED_VALUE = "PLUMBLINE_DISCLOSE_CONSIDERED_BUT_UNPRICED"
DISCLOSE_NOT_PROVEN_OPTIMAL = "PLUMBLINE_DISCLOSE_ALLOCATION_NOT_PROVEN_OPTIMAL"
DISCLOSE_ENROLLMENT_GATED = "PLUMBLINE_DISCLOSE_ENROLLMENT_GATED_BENEFIT"
DISCLOSE_EXHAUSTED_BENEFIT = "PLUMBLINE_DISCLOSE_BENEFIT_EXHAUSTED"
DISCLOSE_PROTOCOL_WIDENING = "PLUMBLINE_DISCLOSE_PROTOCOL_TRANSLATION_WIDENING"

# Which way a disclosed limitation moves the number. There is deliberately no
# "overstates" member: a fact that could inflate the asserted value is a refusal, not a
# footnote, and giving it a disclosure code would create somewhere to hide it.
DIRECTION_UNDERSTATES = "understates"
DIRECTION_NEUTRAL = "neutral"

# Derivation node kinds.
NODE_INSTRUMENT = "instrument"
NODE_BENEFIT = "benefit"
NODE_ASSIGNMENT = "assignment"

# What became of each declared benefit on this cart. The corpus reads these, so they are
# part of the wire contract with attribution.py.
BENEFIT_APPLIED = "APPLIED"
BENEFIT_ELIGIBLE_UNUSED = "ELIGIBLE_UNUSED"
BENEFIT_INELIGIBLE = "INELIGIBLE"
BENEFIT_NOT_ENROLLED = "NOT_ENROLLED"
BENEFIT_EXHAUSTED = "EXHAUSTED"
BENEFIT_UNPRICED = "CONSIDERED_BUT_UNPRICED"

# A benefit was "in play" for a cart when it admitted at least one line and could still
# yield something. Attribution's denominators key on this set.
IN_PLAY_STATUSES = frozenset({BENEFIT_APPLIED, BENEFIT_ELIGIBLE_UNUSED})

# Why an in-play benefit yielded nothing.
UNUSED_DISPLACED = "displaced within its exclusivity group on every line it admits"
UNUSED_NO_HEADROOM = "no capacity headroom left after higher-value assignments"
UNUSED_ZERO_VALUE = "yields nothing on the lines it admits"

# Ranking criteria. The criterion is the cardholder's or the agent's, never the issuer's.
CRITERION_MAX_INCREMENTAL = "max_incremental_value"
CRITERION_MAX_ASSERTED = "max_asserted_value"
CRITERION_MAX_PROTECTION_THEN_VALUE = "max_protection_then_value"
CRITERIA = (CRITERION_MAX_INCREMENTAL, CRITERION_MAX_ASSERTED, CRITERION_MAX_PROTECTION_THEN_VALUE)

AUTHOR_CARDHOLDER = "cardholder"
AUTHOR_AGENT = "agent"
# The issuer signs facts. It does not author the policy that ranks its own product against
# someone else's, and there is no enum value here that would let it.
POLICY_AUTHORS = (AUTHOR_CARDHOLDER, AUTHOR_AGENT)

# Read-only. `Ranking.issuer_endorsed` returns this; there is no field to set.
ISSUER_ENDORSES_RANKING = False

# Mirrored from caveat.adapters.base rather than imported. That module reaches the Z3
# constraint DSL, and the valuation path has to stay importable without a solver — the
# claim "verification needs no solver" is about the import graph as well as the algorithm.
# test_evaluate.py asserts these still equal the adapter's values, so drift is caught.
UNKNOWN_MCC = 0
WARN_MISSING_LINE_DETAIL = "ADAPTER_MISSING_LINE_DETAIL"


class EvaluationError(ValueError):
    """A caller-side mistake: a malformed policy, a bad type, an impossible argument.

    Distinct from a `Refusal`, which is a normal, expected, typed *output* describing input
    the evaluator understood perfectly and declined to sign.
    """


# --------------------------------------------------------------------------------------
# Refusals and disclosures.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """A claim this module declined to make, and what would let it make one."""

    code: str
    subject: str
    detail: str
    remedy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "subject": self.subject,
            "detail": self.detail,
            "remedy": self.remedy,
        }

    def describe(self) -> str:
        return f"{self.code} on {self.subject}: {self.detail}"


@dataclass(frozen=True)
class Disclosure:
    code: str
    detail: str
    direction: str = DIRECTION_NEUTRAL

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "direction": self.direction}


# --------------------------------------------------------------------------------------
# Derivation tree.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivationNode:
    """One node of the line-item derivation.

    The tree is the witness plus everything the witness does *not* say: which benefits were
    considered and did not apply, and why. A receipt carrying only the winning assignments
    cannot answer "was the dining credit even looked at", which is the question the
    attribution corpus is built to answer.

    Value invariant: an internal node's value equals the sum of its children's. The root's
    value equals the witness's realized total. `consistent()` re-checks it.
    """

    node_id: str
    kind: str
    label: str
    value_minor: int
    status: str = ""
    detail: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    children: tuple["DerivationNode", ...] = ()

    def consistent(self) -> bool:
        if self.children:
            if self.value_minor != sum(c.value_minor for c in self.children):
                return False
        return all(c.consistent() for c in self.children)

    def walk(self) -> Iterable["DerivationNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def leaves(self) -> tuple["DerivationNode", ...]:
        return tuple(n for n in self.walk() if not n.children)

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only.

        The root node carries the manifest's currency in `facts`, but children do not, and
        a defaulted symbol here renders a USD derivation in rupees with every integer
        intact — the failure mode that looks correct. The caller holding the valuation
        names it, and a call site that forgets fails loudly rather than quietly.
        """
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "value_minor": self.value_minor,
            "value_display": fmt_currency(self.value_minor, currency),
            "status": self.status,
            "detail": self.detail,
            "facts": dict(self.facts),
            "children": [c.to_dict(currency=currency) for c in self.children],
        }

    def render_lines(self, depth: int = 0, *, currency: str) -> list[str]:
        pad = "  " * depth
        head = f"{pad}{self.label}"
        if self.status:
            head = f"{head}  [{self.status}]"
        head = f"{head}  {fmt_currency(self.value_minor, currency)}"
        if self.detail:
            head = f"{head}  — {self.detail}"
        out = [head]
        for child in self.children:
            out.extend(child.render_lines(depth + 1, currency=currency))
        return out


def build_derivation(manifest: Manifest, cart: Cart, witness: Witness) -> DerivationNode:
    """Assemble the derivation tree for one instrument from its witness.

    Deterministic: benefits appear in manifest order, assignments in witness order. No
    ordering depends on dictionary iteration.
    """
    by_benefit: dict[str, list[Any]] = {}
    for a in witness.assignments:
        by_benefit.setdefault(a.benefit_id, []).append(a)

    lines_by_sku = {line.sku: line for line in cart.lines}
    # Which (line, exclusivity group) pairs the witness actually claimed, and by whom.
    group_holder: dict[tuple[str, str], str] = {}
    benefits_by_id = {b.benefit_id: b for b in manifest.benefits}
    for a in witness.assignments:
        b = benefits_by_id.get(a.benefit_id)
        if b is not None and b.exclusivity_group:
            group_holder[(a.line_sku, b.exclusivity_group)] = a.benefit_id

    children: list[DerivationNode] = []
    for benefit in manifest.benefits:
        children.append(
            _benefit_node(
                benefit,
                cart,
                assignments=by_benefit.get(benefit.benefit_id, []),
                lines_by_sku=lines_by_sku,
                group_holder=group_holder,
            )
        )

    total = sum(c.value_minor for c in children)
    return DerivationNode(
        node_id=f"instrument:{manifest.manifest_id}",
        kind=NODE_INSTRUMENT,
        label=f"{manifest.issuer} {manifest.product}",
        value_minor=total,
        facts={
            "manifest_id": manifest.manifest_id,
            "issuer": manifest.issuer,
            "product": manifest.product,
            "currency": manifest.currency,
            "cart_hash": cart.hash(),
        },
        children=tuple(children),
    )


def _benefit_node(
    benefit: Benefit,
    cart: Cart,
    *,
    assignments: Sequence[Any],
    lines_by_sku: Mapping[str, Any],
    group_holder: Mapping[tuple[str, str], str],
) -> DerivationNode:
    facts = {
        "benefit_id": benefit.benefit_id,
        "benefit_kind": benefit.kind,
        "rate_bp": benefit.rate_bp,
        "flat_minor": benefit.flat_minor,
        "capacity_minor": benefit.capacity_minor,
        "exclusivity_group": benefit.exclusivity_group,
        "window": benefit.window,
        "requires_enrollment": benefit.requires_enrollment,
        "enrolled": benefit.enrolled,
    }

    if benefit.kind == KIND_UNPRICED:
        # Carried so the receipt proves the agent saw it, scored at zero so the integer
        # never claims to be the whole worth of the card.
        return DerivationNode(
            node_id=f"benefit:{benefit.benefit_id}",
            kind=NODE_BENEFIT,
            label=benefit.label,
            value_minor=0,
            status=BENEFIT_UNPRICED,
            detail=benefit.note or "declared and considered; deliberately not priced",
            facts=facts,
        )

    eligible = [line for line in cart.lines if benefit.eligibility.admits(line, cart.merchant)]

    if benefit.requires_enrollment and not benefit.enrolled:
        status, detail = BENEFIT_NOT_ENROLLED, "enrollment-gated and not enrolled"
    elif benefit.capacity_minor is not None and benefit.capacity_minor <= 0:
        status, detail = BENEFIT_EXHAUSTED, "declared balance is exhausted for this window"
    elif not eligible:
        status, detail = BENEFIT_INELIGIBLE, "admits no line of this cart"
    elif assignments:
        status, detail = BENEFIT_APPLIED, ""
    else:
        status, detail = BENEFIT_ELIGIBLE_UNUSED, _unused_reason(
            benefit, eligible, cart, group_holder
        )

    child_nodes = tuple(
        DerivationNode(
            node_id=f"assignment:{benefit.benefit_id}:{a.line_sku}",
            kind=NODE_ASSIGNMENT,
            label=_assignment_label(benefit, lines_by_sku.get(a.line_sku), a.line_sku),
            value_minor=a.value_minor,
            status=BENEFIT_APPLIED,
            facts={
                "line_sku": a.line_sku,
                "benefit_id": a.benefit_id,
                "consumed_minor": a.consumed_minor,
                "line_amount_minor": getattr(lines_by_sku.get(a.line_sku), "amount", None),
            },
        )
        for a in assignments
    )

    return DerivationNode(
        node_id=f"benefit:{benefit.benefit_id}",
        kind=NODE_BENEFIT,
        label=benefit.label,
        value_minor=sum(c.value_minor for c in child_nodes),
        status=status,
        detail=detail,
        facts={**facts, "eligible_lines": len(eligible)},
        children=child_nodes,
    )


def _pct(rate_bp: int) -> str:
    """Basis points rendered as a percentage with integer arithmetic only.

    This string is part of the derivation, which is hashed into a receipt, so it is built
    without floating point rather than relying on a format specifier to round the same way
    everywhere.
    """
    whole, frac = divmod(rate_bp, 100)
    return f"{whole}%" if frac == 0 else f"{whole}.{frac:02d}%"


def _assignment_label(benefit: Benefit, line: Any, sku: str) -> str:
    where = line.description if line is not None else sku
    if benefit.kind == KIND_EARN:
        return f"{benefit.label} at {_pct(benefit.rate_bp)} on {where}"
    if benefit.kind == KIND_CREDIT:
        return f"{benefit.label} offsetting {where}"
    if benefit.kind == KIND_PROTECTION:
        return f"{benefit.label} covering {where}"
    return f"{benefit.label} on {where}"


def _unused_reason(
    benefit: Benefit,
    eligible: Sequence[Any],
    cart: Cart,
    group_holder: Mapping[tuple[str, str], str],
) -> str:
    if max(benefit.value_for_line(line, cart.merchant) for line in eligible) <= 0:
        return UNUSED_ZERO_VALUE
    if benefit.exclusivity_group:
        holders = {
            group_holder.get((line.sku, benefit.exclusivity_group)) for line in eligible
        }
        if None not in holders:
            named = ", ".join(sorted(h for h in holders if h))
            return f"{UNUSED_DISPLACED} (held by {named})"
    return UNUSED_NO_HEADROOM


# --------------------------------------------------------------------------------------
# Valuation policy — the cardholder's or the agent's, never the issuer's.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ValuationPolicy:
    """How this cardholder wants instruments ranked.

    Its hash travels in the receipt so a reviewer can tell which rule produced the order,
    and so a changed rule is detectable. It is not issuer-endorsed and its author may not
    be an issuer — `POLICY_AUTHORS` has no such member.

    `baseline_earn_bp` is the return the cardholder assumes from whatever they would have
    used otherwise. It is a *policy* input, not an issuer fact, so it never enters an
    attestable body: incremental value is a cardholder-side number by construction.
    """

    policy_id: str = "default_cardholder_policy"
    criterion: str = CRITERION_MAX_INCREMENTAL
    author: str = AUTHOR_CARDHOLDER
    baseline_earn_bp: int = 0
    require_signed_manifest: bool = True
    max_manifest_age_s: int | None = None
    min_value_to_rank_minor: int = 0

    def __post_init__(self) -> None:
        if self.criterion not in CRITERIA:
            raise EvaluationError(
                f"unknown ranking criterion {self.criterion!r}; expected one of {CRITERIA}"
            )
        if self.author not in POLICY_AUTHORS:
            raise EvaluationError(
                f"policy author {self.author!r} is not permitted; the valuation policy "
                f"belongs to {POLICY_AUTHORS} — an issuer signs facts, not the rule that "
                "ranks its own product against another's"
            )
        if self.baseline_earn_bp < 0:
            raise EvaluationError(
                f"baseline_earn_bp must be >= 0, got {self.baseline_earn_bp}"
            )
        if self.max_manifest_age_s is not None and self.max_manifest_age_s < 0:
            raise EvaluationError(
                f"max_manifest_age_s must be >= 0 or None, got {self.max_manifest_age_s}"
            )
        if self.min_value_to_rank_minor < 0:
            raise EvaluationError(
                f"min_value_to_rank_minor must be >= 0, got {self.min_value_to_rank_minor}"
            )

    def baseline_for(self, cart: Cart) -> int:
        """What the cardholder assumes they would have earned anyway, in minor units."""
        return cart.total() * self.baseline_earn_bp // BP

    def body(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "criterion": self.criterion,
            "author": self.author,
            "baseline_earn_bp": self.baseline_earn_bp,
            "require_signed_manifest": self.require_signed_manifest,
            "max_manifest_age_s": self.max_manifest_age_s,
            "min_value_to_rank_minor": self.min_value_to_rank_minor,
        }

    def policy_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.body())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "policy_hash": self.policy_hash()}


# --------------------------------------------------------------------------------------
# Per-instrument result.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentValuation:
    """What one instrument is worth on this cart, or why nothing is asserted.

    `asserted_minor` is None exactly when `status` is STATUS_REFUSED. There is no third
    state and no partially-trusted number: a refused instrument carries no value at all,
    is excluded from the ranking, and is still reported so the receipt shows the refusal.
    """

    manifest_id: str
    issuer: str
    product: str
    currency: str
    manifest_hash: str
    cart_hash: str
    status: str
    asserted_minor: int | None
    naive_sum_minor: int
    witness: Witness | None = None
    verification: Verification | None = None
    derivation: DerivationNode | None = None
    refusals: tuple[Refusal, ...] = ()
    disclosures: tuple[Disclosure, ...] = ()
    # Display LABELS, not benefit ids. Named for what it holds: a consumer that read this
    # as ids would look every one of them up, find nothing, and report no unpriced value
    # on a card that declares plenty. `receipt.unpriced_considerations` reads the manifest
    # when it needs the ids.
    unpriced_labels: tuple[str, ...] = ()
    claimed_minor: int | None = None
    considered_benefits: int = 0
    applied_benefits: int = 0
    elapsed_ms: float = 0.0

    @property
    def attested(self) -> bool:
        return self.status == STATUS_ATTESTED

    @property
    def protection_value_minor(self) -> int:
        """Value from protections alone — cover the member never has to redeem."""
        if self.derivation is None:
            return 0
        return sum(
            n.value_minor
            for n in self.derivation.children
            if n.facts.get("benefit_kind") == KIND_PROTECTION
        )

    def overstatement_avoided_minor(self) -> int:
        """How far above the provable number a naive per-line summation would have gone.

        Zero when nothing competes; the demo cart makes it large. Never negative: the
        witness is one achievable allocation and naive summation ignores every capacity,
        so it cannot come in below.
        """
        if self.asserted_minor is None:
            return 0
        return self.naive_sum_minor - self.asserted_minor

    def attestable_body(self) -> dict[str, Any]:
        """The subset an issuer could put a signature on.

        One instrument, one cart, facts and a witness. No rank, no policy, no baseline, no
        other instrument. Deterministic for a given (manifest, cart) pair — measured
        latency is deliberately excluded, so these bytes are stable enough to hash into a
        receipt.
        """
        return {
            "version": EVALUATION_VERSION,
            "manifest_id": self.manifest_id,
            "manifest_hash": self.manifest_hash,
            "issuer": self.issuer,
            "product": self.product,
            "currency": self.currency,
            "cart_hash": self.cart_hash,
            "status": self.status,
            "asserted_minor": self.asserted_minor,
            "witness": (
                self.witness.to_dict(currency=self.currency) if self.witness else None
            ),
            "witness_verified": bool(self.verification and self.verification.ok),
            "derivation": (
                self.derivation.to_dict(currency=self.currency) if self.derivation else None
            ),
            "refusals": [r.to_dict() for r in self.refusals],
            "disclosures": [d.to_dict() for d in self.disclosures],
            "unpriced_labels": list(self.unpriced_labels),
        }

    def attestable_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.attestable_body())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Full record for the console and the corpus.

        Carries `elapsed_ms`, so it is not byte-stable across runs. Use `attestable_body()`
        for anything that gets hashed or signed.
        """
        return {
            **self.attestable_body(),
            "asserted_display": (
                fmt_currency(self.asserted_minor, self.currency)
                if self.asserted_minor is not None
                else None
            ),
            "naive_sum_minor": self.naive_sum_minor,
            "naive_sum_display": fmt_currency(self.naive_sum_minor, self.currency),
            "overstatement_avoided_minor": self.overstatement_avoided_minor(),
            "protection_value_minor": self.protection_value_minor,
            "verification": (
                self.verification.to_dict(currency=self.currency)
                if self.verification
                else None
            ),
            "claimed_minor": self.claimed_minor,
            "considered_benefits": self.considered_benefits,
            "applied_benefits": self.applied_benefits,
            "elapsed_ms": round(self.elapsed_ms, 4),
            "attestable_hash": self.attestable_hash(),
        }


# --------------------------------------------------------------------------------------
# Ranking — outside the signature boundary.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedInstrument:
    rank: int
    manifest_id: str
    asserted_minor: int
    baseline_minor: int
    incremental_minor: int
    protection_value_minor: int
    margin_over_next_minor: int | None

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only.

        A ranking entry names a manifest but does not carry it, so it cannot look the
        currency up. The candidate set is single-currency by construction — ranking across
        currencies would need an FX rate this system does not have — so the enclosing
        `Ranking` knows it and passes it down.
        """
        return {
            "rank": self.rank,
            "manifest_id": self.manifest_id,
            "asserted_minor": self.asserted_minor,
            "asserted_display": fmt_currency(self.asserted_minor, currency),
            "baseline_minor": self.baseline_minor,
            "incremental_minor": self.incremental_minor,
            "incremental_display": fmt_currency(self.incremental_minor, currency),
            "protection_value_minor": self.protection_value_minor,
            "margin_over_next_minor": self.margin_over_next_minor,
        }


@dataclass(frozen=True)
class Ranking:
    """The order, and the rule that produced it. Never issuer-endorsed.

    This object is the whole reason the comparison is safe to compute: it exists outside
    every attestable body, carries the hash of the cardholder's policy, and cannot be
    mistaken for an issuer's assertion that it beat a competitor.
    """

    policy_id: str
    policy_hash: str
    criterion: str
    baseline_minor: int
    entries: tuple[RankedInstrument, ...]

    @property
    def issuer_endorsed(self) -> bool:
        return ISSUER_ENDORSES_RANKING

    @property
    def chosen_manifest_id(self) -> str | None:
        return self.entries[0].manifest_id if self.entries else None

    @property
    def margin_minor(self) -> int | None:
        """The winner's lead over the runner-up. None when there was no contest."""
        if len(self.entries) < 2:
            return None
        return self.entries[0].asserted_minor - self.entries[1].asserted_minor

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only — see `RankedInstrument`."""
        return {
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "criterion": self.criterion,
            "baseline_minor": self.baseline_minor,
            "entries": [e.to_dict(currency=currency) for e in self.entries],
            "chosen_manifest_id": self.chosen_manifest_id,
            "margin_minor": self.margin_minor,
            "issuer_endorsed": self.issuer_endorsed,
            "note": (
                "Ranking is produced by the cardholder's or the agent's valuation policy. "
                "No issuer signs it, and no issuer-signed artifact asserts that one "
                "instrument beat another on this cart."
            ),
        }


# --------------------------------------------------------------------------------------
# The evaluation.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Evaluation:
    version: str
    cart: Cart
    cart_hash: str
    evaluated_at: int
    policy: ValuationPolicy
    candidates: tuple[InstrumentValuation, ...]
    ranking: Ranking | None
    refusals: tuple[Refusal, ...] = ()
    disclosures: tuple[Disclosure, ...] = ()
    protocol: str | None = None

    def attested(self) -> tuple[InstrumentValuation, ...]:
        return tuple(c for c in self.candidates if c.attested)

    def refused(self) -> tuple[InstrumentValuation, ...]:
        return tuple(c for c in self.candidates if not c.attested)

    @property
    def signable(self) -> bool:
        """True when at least one instrument carries a verified witness.

        A false value is the demo's second beat: the evaluator has nothing it is willing to
        put a signature on, and says so rather than emitting a smaller number.
        """
        return bool(self.attested())

    def valuation(self, manifest_id: str) -> InstrumentValuation | None:
        for c in self.candidates:
            if c.manifest_id == manifest_id:
                return c
        return None

    def all_refusals(self) -> tuple[Refusal, ...]:
        out = list(self.refusals)
        for c in self.candidates:
            out.extend(c.refusals)
        return tuple(out)

    def attestable_body(self) -> dict[str, Any]:
        """Everything an issuer could sign, ordered by manifest id rather than by rank.

        Ordering by rank would smuggle the comparison back in: the position of an
        instrument in a signed list is itself a claim about the others.
        """
        return {
            "version": self.version,
            "cart_hash": self.cart_hash,
            "evaluated_at": self.evaluated_at,
            "instruments": [
                c.attestable_body() for c in sorted(self.candidates, key=lambda c: c.manifest_id)
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "cart": self.cart.to_dict(),
            "cart_hash": self.cart_hash,
            "cart_total_minor": self.cart.total(),
            "evaluated_at": self.evaluated_at,
            "protocol": self.protocol,
            "policy": self.policy.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "ranking": (
                self.ranking.to_dict(currency=self.cart.currency) if self.ranking else None
            ),
            "refusals": [r.to_dict() for r in self.refusals],
            "disclosures": [d.to_dict() for d in self.disclosures],
            "signable": self.signable,
            "attested_count": len(self.attested()),
            "refused_count": len(self.refused()),
        }

    def render_text(self, width: int = 92) -> str:
        rule = "=" * width
        thin = "-" * width
        out = [
            rule,
            "PLUMBLINE — CART VALUATION".center(width),
            f"cart {self.cart_hash[:16]}…  "
            f"total {fmt_currency(self.cart.total(), self.cart.currency)}".center(width),
            rule,
            "",
        ]
        for c in self.candidates:
            head = f"  {c.issuer} {c.product}  [{c.status}]"
            if c.asserted_minor is not None:
                head += (
                    f"  asserted {fmt_currency(c.asserted_minor, c.currency)}"
                    f"   (naive sum would claim "
                    f"{fmt_currency(c.naive_sum_minor, c.currency)})"
                )
            out.append(head)
            for r in c.refusals:
                out.append(f"      refused: {r.code} — {r.detail}")
                out.append(f"      remedy:  {r.remedy}")
            if c.derivation is not None:
                for line in c.derivation.render_lines(depth=2, currency=c.currency):
                    out.append(f"  {line}")
            out.append("")
        if self.ranking is not None:
            out += [thin, f"RANKING (policy {self.ranking.policy_id}, not issuer-endorsed)", thin]
            for e in self.ranking.entries:
                out.append(
                    f"  {e.rank}. {e.manifest_id:<28}"
                    f"{fmt_currency(e.asserted_minor, self.cart.currency):>14}"
                    f"   incremental "
                    f"{fmt_currency(e.incremental_minor, self.cart.currency)}"
                )
        else:
            out += [thin, "NO RANKING — nothing here is attestable", thin]
        for r in self.refusals:
            out.append(f"  {r.code}: {r.detail}")
        for d in self.disclosures:
            out.append(f"  disclosure [{d.direction}] {d.code}: {d.detail}")
        out.append(rule)
        return "\n".join(out)


# --------------------------------------------------------------------------------------
# Entry points.
# --------------------------------------------------------------------------------------


def evaluate(
    *,
    cart: Cart,
    manifests: Sequence[SignedManifest | Manifest],
    now: int,
    policy: ValuationPolicy | None = None,
    keys: Mapping[str, str | bytes] | None = None,
    claims: Mapping[str, int] | None = None,
    protocol: str | None = None,
    translation_warnings: Sequence[TranslationWarning] = (),
) -> Evaluation:
    """Value a cart on every candidate instrument, and rank what survived.

    `keys` maps a manifest's `key_id` to the secret that verifies it. A signed manifest
    whose key is absent is refused rather than trusted, unless the policy explicitly does
    not require signatures.

    `claims` maps a manifest id to a value someone *proposed* — a model's guess, a
    merchant's marketing, a cached figure. A claim is only ever used as a hypothesis to
    reject: if the witness does not support it, the instrument is refused. A claim is never
    the source of an asserted number.

    `now` is an explicit parameter and the system clock is never consulted, so any run
    replays byte for byte.
    """
    policy = policy or ValuationPolicy()
    keys = dict(keys or {})
    claims = dict(claims or {})
    if not isinstance(now, int):
        raise EvaluationError(f"now must be epoch seconds as an int, got {type(now).__name__}")

    cart_hash = cart.hash()
    refusals: list[Refusal] = []
    disclosures = _dedupe_disclosures(
        (*_cart_disclosures(cart), *_protocol_disclosures(translation_warnings))
    )

    duplicate_skus = _duplicate_skus(cart)
    if duplicate_skus:
        refusals.append(
            Refusal(
                code=REFUSE_DUPLICATE_SKU,
                subject=cart_hash,
                detail=(
                    f"cart repeats SKU(s) {', '.join(duplicate_skus)}; a witness assignment "
                    "names a line by SKU, so a repeated SKU makes the derivation ambiguous"
                ),
                remedy="fold repeated SKUs into one line with the summed amount and quantity",
            )
        )
    if not cart.lines:
        refusals.append(
            Refusal(
                code=REFUSE_EMPTY_CART,
                subject=cart_hash,
                detail="cart carries no line items, so there is nothing to value",
                remedy="supply the checkout session's line items before asking for a valuation",
            )
        )

    candidates: list[InstrumentValuation] = []
    if not refusals:
        seen: set[str] = set()
        for entry in manifests:
            valuation = _evaluate_one(
                entry,
                cart=cart,
                cart_hash=cart_hash,
                now=now,
                policy=policy,
                keys=keys,
                claims=claims,
                seen=seen,
            )
            candidates.append(valuation)
            seen.add(valuation.manifest_id)
        candidates.sort(key=lambda c: c.manifest_id)

    ranking = _rank(candidates, policy=policy, cart=cart)
    if ranking is None and not refusals:
        refusals.append(
            Refusal(
                code=REFUSE_NO_ATTESTABLE_INSTRUMENT,
                subject=cart_hash,
                detail=(
                    f"none of the {len(candidates)} candidate instrument(s) produced a "
                    "verified witness, so there is no value this evaluator will assert and "
                    "no ranking it will emit"
                ),
                remedy=(
                    "inspect each candidate's refusal codes; a fresh, correctly signed "
                    "manifest in the cart's currency is what makes an instrument rankable"
                ),
            )
        )

    return Evaluation(
        version=EVALUATION_VERSION,
        cart=cart,
        cart_hash=cart_hash,
        evaluated_at=now,
        policy=policy,
        candidates=tuple(candidates),
        ranking=ranking,
        refusals=tuple(refusals),
        disclosures=tuple(disclosures),
        protocol=protocol,
    )


def evaluate_normalized(
    request: NormalizedRequest,
    manifests: Sequence[SignedManifest | Manifest],
    *,
    now: int,
    policy: ValuationPolicy | None = None,
    keys: Mapping[str, str | bytes] | None = None,
    claims: Mapping[str, int] | None = None,
) -> Evaluation:
    """Value the cart carried by a normalized ACP / AP2 / MCP request.

    The *executed* cart is valued when the payload carries one, falling back to the signed
    intent cart. Valuing intent when execution is available would price a cart nobody is
    about to be charged for — and post-signature mutation of the executed cart is the
    documented attack this kernel already exists to catch.

    Translation warnings are carried through as disclosures. A widening warning is about
    *authority*, not value: it does not change what the cart is worth, and it is surfaced
    here only so a caller cannot claim the evaluator saw it and said nothing. Gating
    authorization on it remains the engine's job.
    """
    cart = request.executed_cart or request.intent_cart
    return evaluate(
        cart=cart,
        manifests=manifests,
        now=now,
        policy=policy,
        keys=keys,
        claims=claims,
        protocol=request.protocol,
        translation_warnings=request.warnings,
    )


def evaluate_payload(
    payload: Mapping[str, Any],
    manifests: Sequence[SignedManifest | Manifest],
    *,
    now: int,
    policy: ValuationPolicy | None = None,
    keys: Mapping[str, str | bytes] | None = None,
    claims: Mapping[str, int] | None = None,
) -> Evaluation:
    """Value a raw ACP / AP2 / MCP payload. Raises AdapterError on a payload it cannot read.

    The adapter import is deferred to call time: translating a wire format needs the
    kernel's constraint DSL, which reaches Z3, and importing this module must not.
    """
    from caveat.adapters import normalize

    return evaluate_normalized(
        normalize(payload), manifests, now=now, policy=policy, keys=keys, claims=claims
    )


# --------------------------------------------------------------------------------------
# One instrument.
# --------------------------------------------------------------------------------------


def _evaluate_one(
    entry: SignedManifest | Manifest,
    *,
    cart: Cart,
    cart_hash: str,
    now: int,
    policy: ValuationPolicy,
    keys: Mapping[str, str | bytes],
    claims: Mapping[str, int],
    seen: set[str],
) -> InstrumentValuation:
    signed = entry if isinstance(entry, SignedManifest) else None
    manifest = signed.manifest if signed is not None else entry
    if not isinstance(manifest, Manifest):
        raise EvaluationError(
            f"manifests must be Manifest or SignedManifest, got {type(entry).__name__}"
        )

    def refused(*refusals: Refusal) -> InstrumentValuation:
        return InstrumentValuation(
            manifest_id=manifest.manifest_id,
            issuer=manifest.issuer,
            product=manifest.product,
            currency=manifest.currency,
            manifest_hash=manifest.content_hash(),
            cart_hash=cart_hash,
            status=STATUS_REFUSED,
            asserted_minor=None,
            naive_sum_minor=0,
            refusals=refusals,
            claimed_minor=claims.get(manifest.manifest_id),
        )

    if manifest.manifest_id in seen:
        return refused(
            Refusal(
                code=REFUSE_DUPLICATE_MANIFEST,
                subject=manifest.manifest_id,
                detail=(
                    "a manifest with this id was already evaluated for this cart; two "
                    "different sets of facts under one id cannot both be attested"
                ),
                remedy="deduplicate the candidate set, keeping the manifest you intend to use",
            )
        )

    gate = _manifest_gate(signed, manifest, now=now, policy=policy, keys=keys, cart=cart)
    if gate is not None:
        return refused(gate)

    result = allocate(manifest, cart)
    witness = result.witness
    verification = verify_witness(
        witness=witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=witness.realized_minor(),
    )

    witness_refusals: list[Refusal] = []
    if witness.cart_hash != cart_hash:
        witness_refusals.append(
            Refusal(
                code=REFUSE_WITNESS_CART_MISMATCH,
                subject=manifest.manifest_id,
                detail=(
                    f"witness binds cart {witness.cart_hash[:16]}… but this evaluation is of "
                    f"cart {cart_hash[:16]}…"
                ),
                remedy="re-run the allocator against the cart actually being authorized",
            )
        )
    if not verification.ok:
        witness_refusals.append(
            Refusal(
                code=REFUSE_WITNESS_INVALID,
                subject=manifest.manifest_id,
                detail=(
                    "the exhibited allocation failed independent verification: "
                    + "; ".join(f"{f.code} ({f.detail})" for f in verification.failures)
                ),
                remedy=(
                    "this is an allocator defect, not a data problem — the witness must be "
                    "reproducible by re-adding the numbers and checking the capacities"
                ),
            )
        )

    realized = verification.realized_minor if verification.ok else 0
    claimed = claims.get(manifest.manifest_id)
    if claimed is not None and claimed > realized:
        witness_refusals.append(
            Refusal(
                code=REFUSE_CLAIM_UNSUPPORTED,
                subject=manifest.manifest_id,
                detail=(
                    f"a value of {fmt_currency(claimed, manifest.currency)} was proposed "
                    f"for this instrument; the best allocation this evaluator can exhibit "
                    f"realizes {fmt_currency(realized, manifest.currency)}, "
                    "so the proposal is not backed by a witness"
                ),
                remedy=(
                    f"assert at most {fmt_currency(realized, manifest.currency)}, or "
                    f"supply an allocation that "
                    "realizes the proposed value and survives verify_witness"
                ),
            )
        )

    if witness_refusals:
        # Deliberately no silent downgrade to the witness-backed figure. An unsupported
        # proposal is a refusal to sign, visible in the candidate set and in the log.
        return InstrumentValuation(
            manifest_id=manifest.manifest_id,
            issuer=manifest.issuer,
            product=manifest.product,
            currency=manifest.currency,
            manifest_hash=manifest.content_hash(),
            cart_hash=cart_hash,
            status=STATUS_REFUSED,
            asserted_minor=None,
            naive_sum_minor=naive_sum(manifest, cart),
            witness=witness,
            verification=verification,
            derivation=build_derivation(manifest, cart, witness),
            refusals=tuple(witness_refusals),
            claimed_minor=claimed,
            considered_benefits=len(manifest.benefits),
            elapsed_ms=result.elapsed_ms,
        )

    derivation = build_derivation(manifest, cart, witness)
    return InstrumentValuation(
        manifest_id=manifest.manifest_id,
        issuer=manifest.issuer,
        product=manifest.product,
        currency=manifest.currency,
        manifest_hash=manifest.content_hash(),
        cart_hash=cart_hash,
        status=STATUS_ATTESTED,
        asserted_minor=realized,
        naive_sum_minor=naive_sum(manifest, cart),
        witness=witness,
        verification=verification,
        derivation=derivation,
        disclosures=_instrument_disclosures(manifest, cart, derivation),
        unpriced_labels=tuple(b.label for b in manifest.unpriced()),
        claimed_minor=claimed,
        considered_benefits=len(manifest.benefits),
        applied_benefits=sum(
            1 for n in derivation.children if n.status == BENEFIT_APPLIED
        ),
        elapsed_ms=result.elapsed_ms,
    )


def _manifest_gate(
    signed: SignedManifest | None,
    manifest: Manifest,
    *,
    now: int,
    policy: ValuationPolicy,
    keys: Mapping[str, str | bytes],
    cart: Cart,
) -> Refusal | None:
    """Checks that must pass before an allocation is worth computing at all."""
    if signed is None:
        if policy.require_signed_manifest:
            return Refusal(
                code=REFUSE_MANIFEST_UNSIGNED,
                subject=manifest.manifest_id,
                detail="policy requires a signed manifest and this one carries no signature",
                remedy=(
                    "supply a SignedManifest, or set require_signed_manifest=False on the "
                    "valuation policy if unsigned facts are acceptable for this run"
                ),
            )
    else:
        key = keys.get(signed.key_id)
        if key is None:
            if policy.require_signed_manifest:
                return Refusal(
                    code=REFUSE_MANIFEST_KEY_UNKNOWN,
                    subject=manifest.manifest_id,
                    detail=(
                        f"manifest is signed under key_id {signed.key_id!r}, which was not "
                        "supplied, so its facts cannot be checked"
                    ),
                    remedy=f"pass keys={{{signed.key_id!r}: <secret>}} to evaluate()",
                )
        elif not verify_manifest(signed, key):
            # Checked whenever a key is available, not only when the policy demands a
            # signature. `require_signed_manifest=False` says signing is optional; it does
            # not say a signature that fails may be ignored.
            return Refusal(
                code=REFUSE_MANIFEST_SIGNATURE,
                subject=manifest.manifest_id,
                detail=(
                    "signature does not verify over the manifest's canonical bytes; its "
                    "contents were altered after signing or the wrong key was supplied"
                ),
                remedy="re-fetch the manifest from the issuer and re-check the key id",
            )

    if manifest.currency != cart.currency:
        return Refusal(
            code=REFUSE_CURRENCY_MISMATCH,
            subject=manifest.manifest_id,
            detail=(
                f"manifest declares {manifest.currency} and the cart is in {cart.currency}; "
                "no conversion is performed, because a rate applied here would be an "
                "unsigned number inside a signed derivation"
            ),
            remedy="supply a manifest denominated in the cart's currency",
        )

    if manifest.issued_at > now:
        return Refusal(
            code=REFUSE_MANIFEST_POSTDATED,
            subject=manifest.manifest_id,
            detail=(
                f"manifest is dated {manifest.issued_at}, later than the evaluation time "
                f"{now}; facts that do not exist yet cannot be attested"
            ),
            remedy="check the manifest's issued_at and the clock passed to evaluate()",
        )

    if policy.max_manifest_age_s is not None and now - manifest.issued_at > policy.max_manifest_age_s:
        return Refusal(
            code=REFUSE_MANIFEST_STALE,
            subject=manifest.manifest_id,
            detail=(
                f"manifest is {now - manifest.issued_at}s old and the policy admits at most "
                f"{policy.max_manifest_age_s}s; a credit balance goes stale as it is spent"
            ),
            remedy="refresh the manifest from the issuer, or widen max_manifest_age_s",
        )

    return None


# --------------------------------------------------------------------------------------
# Ranking and disclosures.
# --------------------------------------------------------------------------------------


def _rank(
    candidates: Sequence[InstrumentValuation], *, policy: ValuationPolicy, cart: Cart
) -> Ranking | None:
    baseline = policy.baseline_for(cart)
    eligible = [
        c
        for c in candidates
        if c.attested
        and c.asserted_minor is not None
        and c.asserted_minor >= policy.min_value_to_rank_minor
    ]
    if not eligible:
        return None

    def sort_key(c: InstrumentValuation) -> tuple[Any, ...]:
        asserted = c.asserted_minor or 0
        if policy.criterion == CRITERION_MAX_PROTECTION_THEN_VALUE:
            return (-c.protection_value_minor, -asserted, c.manifest_id)
        # Incremental and asserted order identically — the baseline is a property of the
        # cart, not of the instrument — but they report different numbers, and the
        # criterion is recorded so a reviewer knows which one the agent acted on.
        return (-asserted, c.manifest_id)

    ordered = sorted(eligible, key=sort_key)
    entries: list[RankedInstrument] = []
    for i, c in enumerate(ordered):
        asserted = c.asserted_minor or 0
        next_value = ordered[i + 1].asserted_minor if i + 1 < len(ordered) else None
        entries.append(
            RankedInstrument(
                rank=i + 1,
                manifest_id=c.manifest_id,
                asserted_minor=asserted,
                baseline_minor=baseline,
                incremental_minor=asserted - baseline,
                protection_value_minor=c.protection_value_minor,
                margin_over_next_minor=None if next_value is None else asserted - next_value,
            )
        )
    return Ranking(
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash(),
        criterion=policy.criterion,
        baseline_minor=baseline,
        entries=tuple(entries),
    )


def _cart_disclosures(cart: Cart) -> tuple[Disclosure, ...]:
    blind = [line.sku for line in cart.lines if line.mcc == UNKNOWN_MCC]
    if not blind:
        return ()
    return (
        Disclosure(
            code=DISCLOSE_LINE_DETAIL_MISSING,
            detail=(
                f"{len(blind)} line(s) carry no merchant category code "
                f"({', '.join(sorted(blind)[:5])}); benefits keyed on MCC could not attach to "
                "them, so every value below is a floor"
            ),
            direction=DIRECTION_UNDERSTATES,
        ),
    )


def _protocol_disclosures(warnings: Sequence[TranslationWarning]) -> tuple[Disclosure, ...]:
    out: list[Disclosure] = []
    widening = [w for w in warnings if w.widening]
    if widening:
        out.append(
            Disclosure(
                code=DISCLOSE_PROTOCOL_WIDENING,
                detail=(
                    f"{len(widening)} constraint(s) were lost translating this payload "
                    f"({', '.join(sorted({w.code for w in widening}))}). That concerns what the "
                    "agent is authorized to do, not what the cart is worth; gate authorization "
                    "on it separately"
                ),
                direction=DIRECTION_NEUTRAL,
            )
        )
    if any(w.code == WARN_MISSING_LINE_DETAIL for w in warnings):
        out.append(
            Disclosure(
                code=DISCLOSE_LINE_DETAIL_MISSING,
                detail=(
                    "the source payload omitted line detail the adapter had to default; "
                    "benefits keyed on the missing field could not attach"
                ),
                direction=DIRECTION_UNDERSTATES,
            )
        )
    return tuple(out)


def _instrument_disclosures(
    manifest: Manifest, cart: Cart, derivation: DerivationNode
) -> tuple[Disclosure, ...]:
    out = [
        Disclosure(
            code=DISCLOSE_NOT_PROVEN_OPTIMAL,
            detail=(
                "the exhibited allocation is produced by a deterministic greedy allocator. "
                "It is achievable, so the asserted value cannot exceed the true optimum, but "
                "it is not proven optimal; the offline oracle measures the gap"
            ),
            direction=DIRECTION_UNDERSTATES,
        )
    ]
    gated = [n.label for n in derivation.children if n.status == BENEFIT_NOT_ENROLLED]
    if gated:
        out.append(
            Disclosure(
                code=DISCLOSE_ENROLLMENT_GATED,
                detail=(
                    f"{len(gated)} declared benefit(s) are enrollment-gated and not enrolled "
                    f"({', '.join(sorted(gated)[:5])}), so they contribute nothing here"
                ),
                direction=DIRECTION_UNDERSTATES,
            )
        )
    exhausted = [n.label for n in derivation.children if n.status == BENEFIT_EXHAUSTED]
    if exhausted:
        out.append(
            Disclosure(
                code=DISCLOSE_EXHAUSTED_BENEFIT,
                detail=(
                    f"{len(exhausted)} declared benefit(s) have no balance left in the current "
                    f"window ({', '.join(sorted(exhausted)[:5])})"
                ),
                direction=DIRECTION_NEUTRAL,
            )
        )
    unpriced = manifest.unpriced()
    if unpriced:
        out.append(
            Disclosure(
                code=DISCLOSE_UNPRICED_VALUE,
                detail=(
                    f"{len(unpriced)} declared benefit(s) are carried as considered-but-unpriced "
                    f"({', '.join(sorted(b.label for b in unpriced)[:5])}); the integer above "
                    "does not claim to be the whole worth of this card"
                ),
                direction=DIRECTION_UNDERSTATES,
            )
        )
    return tuple(out)


def _dedupe_disclosures(items: Sequence[Disclosure]) -> list[Disclosure]:
    """One disclosure per code. The cart-level text is the more specific of the pair."""
    out: list[Disclosure] = []
    seen: set[str] = set()
    for d in items:
        if d.code in seen:
            continue
        seen.add(d.code)
        out.append(d)
    return out


def _duplicate_skus(cart: Cart) -> tuple[str, ...]:
    seen: set[str] = set()
    dupes: list[str] = []
    for line in cart.lines:
        if line.sku in seen and line.sku not in dupes:
            dupes.append(line.sku)
        seen.add(line.sku)
    return tuple(sorted(dupes))
