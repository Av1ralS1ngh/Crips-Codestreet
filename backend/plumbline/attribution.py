"""The receipt corpus, read backwards: which benefits actually win transactions.

This module exists to answer one objection, and the objection is the most dangerous one
this project faces.

Card Member Services expense is recognised largely **as benefits are used**. An issuer's own
variance commentary attributes the increase to *higher usage of Card Member benefits*. So a
product that drives usage inflates the line under the most pressure, and "the money is
already spent" is false on the issuer's own accounting. A benefit-nudging product is not
neutral; it is expensive.

PLUMBLINE is the other thing. Every Decision Receipt records the full candidate set, each
instrument's derivation, and the criterion the agent stated. Read across a corpus, that is a
running, cart-level record of which benefits appeared in a **winning** derivation and which
never do — benefit-level attribution against a revealed-preference outcome at the moment of
choice, rather than a renewal signal that takes two years to reach the P&L. It is the first
instrument that tells an issuer which benefits to **cut**.

The 2x2, re-keyed from usage to selection-influence:

                    rarely decisive          often decisive
      high cost     dead weight              load-bearing
                    cut or renegotiate       protect
      low cost      noise                    an option
                                             fund it, stop apologising for breakage

WHAT THIS OBSERVES, AND WHAT IT DOES NOT
----------------------------------------
It observes **selection influence**: how often a benefit appeared in the derivation of the
instrument an agent chose, against how often it was in play and did not. That is a
revealed preference at a decision point, recorded by a system that had to exhibit a witness
before it could assert anything.

It does **not** observe incremental retention, incremental spend, or willingness to pay.
Nothing here supports a causal claim about member behaviour: a benefit can be decisive at
checkout and irrelevant at renewal, and the reverse. Three further limits, all of them
carried in the output rather than left in a docstring:

  * Influence is conditional on the **criterion the agent stated**. A corpus in which every
    receipt ranked on `max_asserted_value` measures decisiveness under that rule and no
    other. `AttributionReport.criteria` names the rules the corpus actually used.
  * The pivotal test is computed from the **recorded witness**, not by re-running the
    evaluator without the benefit. Removing a benefit lets the allocator reallocate, so
    the test is a bound on influence, not a re-simulated counterfactual.
  * Cost is **issuer-supplied**, per benefit, per member, annualised. Nothing here derives
    it, and a benefit with no supplied cost is placed in no quadrant at all.

WHAT THIS IS NOT ALLOWED TO BECOME
----------------------------------
Portfolio analytics for the issuer. The output carries no member identifier and no
per-member balance, and nothing in it is addressable to a Card Member. The activation gap
this module can measure — a benefit eligible for a cart that the member was never enrolled
in — is reported as a portfolio fact for a benefit-design decision, never as a prompt to go
and use it. A module that drove usage would inflate the line it exists to explain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from caveat.money import fmt_money

from .evaluate import (
    BENEFIT_APPLIED,
    BENEFIT_EXHAUSTED,
    BENEFIT_NOT_ENROLLED,
    BENEFIT_UNPRICED,
    IN_PLAY_STATUSES,
    NODE_BENEFIT,
    STATUS_ATTESTED,
    Evaluation,
)

ATTRIBUTION_VERSION = "plumbline/attribution/1"

BP = 10_000

QUADRANT_DEAD_WEIGHT = "dead_weight"
QUADRANT_LOAD_BEARING = "load_bearing"
QUADRANT_NOISE = "noise"
QUADRANT_OPTION = "option"
QUADRANT_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
QUADRANT_NO_COST_BASIS = "no_cost_basis"

QUADRANTS = (
    QUADRANT_DEAD_WEIGHT,
    QUADRANT_LOAD_BEARING,
    QUADRANT_NOISE,
    QUADRANT_OPTION,
    QUADRANT_INSUFFICIENT_EVIDENCE,
    QUADRANT_NO_COST_BASIS,
)

ACTION_BY_QUADRANT: dict[str, str] = {
    QUADRANT_DEAD_WEIGHT: "cut or renegotiate",
    QUADRANT_LOAD_BEARING: "protect",
    QUADRANT_NOISE: "leave alone; it costs little and decides little",
    QUADRANT_OPTION: "fund it; breakage is not the measure of it",
    QUADRANT_INSUFFICIENT_EVIDENCE: "collect more decisions before acting",
    QUADRANT_NO_COST_BASIS: "supply an annualised cost basis to place this benefit",
}

CLAIM_BOUNDARY = (
    "Observes SELECTION INFLUENCE: how often a benefit appeared in the derivation of the "
    "instrument an agent chose, against how often it was in play and did not. It does NOT "
    "observe incremental retention, incremental spend or willingness to pay, and nothing "
    "here supports a causal claim about member behaviour."
)

PIVOTAL_DEFINITION = (
    "A benefit is counted pivotal for a decision when its realized contribution to the "
    "chosen instrument exceeded that instrument's asserted margin over the best alternative, "
    "so the recorded derivation does not clear the alternative without it. Computed from the "
    "recorded witness, not by re-running the evaluator: removing a benefit lets the allocator "
    "reallocate, so this is a bound on influence rather than a re-simulated outcome."
)

NEVER_A_NUDGE = (
    "Portfolio analytics for the issuer. Carries no member identifier and no per-member "
    "balance. Benefit expense is recognised as benefits are used, so a surface that drove "
    "usage would inflate the line this exists to explain."
)

COST_BASIS_NOTE = (
    "Annualised cost per member per benefit is ISSUER-SUPPLIED. Nothing in this module "
    "derives, estimates or infers it, and a benefit with no supplied cost is placed in no "
    "quadrant."
)


class AttributionError(ValueError):
    """A corpus record this module cannot read. Names the field and the expected shape."""


# --------------------------------------------------------------------------------------
# Keys.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BenefitKey:
    """A benefit belongs to a card product, so the key carries the product.

    Two issuers may both call something `b_dining_credit`, and collapsing them would
    attribute one issuer's decisions to another's benefit design.
    """

    issuer: str
    product: str
    benefit_id: str

    def as_str(self) -> str:
        return f"{self.issuer}/{self.product}/{self.benefit_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "product": self.product,
            "benefit_id": self.benefit_id,
            "key": self.as_str(),
        }


def _key_str(key: BenefitKey | str) -> str:
    return key.as_str() if isinstance(key, BenefitKey) else str(key)


# --------------------------------------------------------------------------------------
# Observations — one per decision.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BenefitObservation:
    """What happened to one benefit in one decision."""

    key: BenefitKey
    label: str
    benefit_kind: str
    status: str
    value_minor: int
    eligible_lines: int
    on_chosen_instrument: bool
    instrument_attested: bool

    @property
    def in_play(self) -> bool:
        """The benefit admitted a line of this cart and could still yield something."""
        return self.instrument_attested and self.status in IN_PLAY_STATUSES

    @property
    def applied(self) -> bool:
        return self.instrument_attested and self.status == BENEFIT_APPLIED and self.value_minor > 0

    @property
    def winning(self) -> bool:
        return self.applied and self.on_chosen_instrument

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.key.to_dict(),
            "label": self.label,
            "benefit_kind": self.benefit_kind,
            "status": self.status,
            "value_minor": self.value_minor,
            "eligible_lines": self.eligible_lines,
            "on_chosen_instrument": self.on_chosen_instrument,
            "instrument_attested": self.instrument_attested,
        }


@dataclass(frozen=True)
class DecisionObservation:
    """One receipt, reduced to what attribution needs and nothing that identifies a member."""

    decision_id: str
    cart_hash: str
    evaluated_at: int
    criterion: str
    chosen_manifest_id: str | None
    chosen_attested: bool
    attested_candidates: int
    margin_minor: int | None
    benefits: tuple[BenefitObservation, ...]

    @property
    def contested(self) -> bool:
        """Two or more instruments were attested, so a choice was actually made."""
        return self.attested_candidates >= 2

    @property
    def pivotal_testable(self) -> bool:
        """A pivotal test is only meaningful against a non-negative margin.

        A negative margin means the agent picked an instrument its own stated criterion
        ranked below another. That choice was not made on these numbers, so no benefit in
        it gets credit for deciding it.
        """
        return self.contested and self.chosen_attested and (self.margin_minor or 0) >= 0

    def pivotal(self, obs: BenefitObservation) -> bool:
        if not obs.winning or not self.pivotal_testable:
            return False
        return obs.value_minor > (self.margin_minor or 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "cart_hash": self.cart_hash,
            "evaluated_at": self.evaluated_at,
            "criterion": self.criterion,
            "chosen_manifest_id": self.chosen_manifest_id,
            "chosen_attested": self.chosen_attested,
            "attested_candidates": self.attested_candidates,
            "contested": self.contested,
            "margin_minor": self.margin_minor,
            "benefits": [b.to_dict() for b in self.benefits],
        }


def observe_evaluation(
    evaluation: Evaluation,
    *,
    decision_id: str,
    chosen_manifest_id: str | None = None,
) -> DecisionObservation:
    """Reduce an in-memory `Evaluation` to one corpus observation.

    `chosen_manifest_id` records what the agent actually did, which is not always the top
    of the ranking — an agent may choose on acceptance, on habit, or on something the
    valuation never saw. Passing it is how that divergence stays visible. It defaults to
    the ranking's winner.
    """
    return observe_receipt(
        {"decision_id": decision_id, "chosen_manifest_id": chosen_manifest_id, **evaluation.to_dict()}
    )


def observe_receipt(receipt: Mapping[str, Any]) -> DecisionObservation:
    """Read one Decision Receipt (or a bare evaluation body) into an observation.

    Accepts either a receipt carrying an `evaluation` object or an evaluation dict
    directly, and reads the agent's actual selection from any of `chosen_manifest_id`,
    `selected_instrument` or `selection.manifest_id`, falling back to the ranking's winner.
    Deliberately tolerant about the envelope and strict about the payload: the envelope is
    the receipt format's business, the payload is this module's contract.
    """
    if not isinstance(receipt, Mapping):
        raise AttributionError(
            f"a corpus record must be a mapping, got {type(receipt).__name__}"
        )

    body = receipt.get("evaluation") if isinstance(receipt.get("evaluation"), Mapping) else receipt
    candidates = body.get("candidates")
    if not isinstance(candidates, list):
        raise AttributionError(
            "record carries no 'candidates' list; expected an evaluation body of the shape "
            "produced by plumbline.evaluate.Evaluation.to_dict(), either at the top level or "
            f"under an 'evaluation' key (top-level keys seen: {sorted(map(str, receipt))[:12]})"
        )

    cart_hash = str(body.get("cart_hash", ""))
    evaluated_at = int(body.get("evaluated_at", 0))
    ranking = body.get("ranking") if isinstance(body.get("ranking"), Mapping) else None

    decision_id = _first_str(receipt, "decision_id", "receipt_id", "id")
    if decision_id is None:
        # Deterministic fallback so a corpus assembled before receipt.py settled its
        # envelope still de-duplicates correctly.
        decision_id = f"{cart_hash[:16]}:{evaluated_at}"

    chosen = _first_str(receipt, "chosen_manifest_id", "selected_instrument", "chosen_instrument")
    if chosen is None:
        selection = receipt.get("selection")
        if isinstance(selection, Mapping):
            chosen = _first_str(selection, "manifest_id", "instrument_id")
    if chosen is None and ranking is not None:
        raw = ranking.get("chosen_manifest_id")
        chosen = str(raw) if isinstance(raw, str) else None

    criterion = str(ranking.get("criterion", "")) if ranking else ""

    attested = [
        c
        for c in candidates
        if isinstance(c, Mapping) and c.get("status") == STATUS_ATTESTED
    ]
    chosen_value: int | None = None
    best_other: int | None = None
    for c in attested:
        value = int(c.get("asserted_minor") or 0)
        if str(c.get("manifest_id")) == chosen:
            chosen_value = value
        elif best_other is None or value > best_other:
            best_other = value

    margin = None if chosen_value is None or best_other is None else chosen_value - best_other

    observations: list[BenefitObservation] = []
    for c in candidates:
        if not isinstance(c, Mapping):
            raise AttributionError(f"candidate entries must be mappings, got {type(c).__name__}")
        manifest_id = str(c.get("manifest_id", ""))
        issuer = str(c.get("issuer", ""))
        product = str(c.get("product", ""))
        derivation = c.get("derivation")
        if not isinstance(derivation, Mapping):
            # A refused candidate can legitimately carry no derivation; there is nothing to
            # attribute and no benefit was in play on it.
            continue
        for node in derivation.get("children", []) or []:
            if not isinstance(node, Mapping) or node.get("kind") != NODE_BENEFIT:
                continue
            facts = node.get("facts") if isinstance(node.get("facts"), Mapping) else {}
            benefit_id = str(facts.get("benefit_id") or node.get("node_id", ""))
            observations.append(
                BenefitObservation(
                    key=BenefitKey(issuer=issuer, product=product, benefit_id=benefit_id),
                    label=str(node.get("label", benefit_id)),
                    benefit_kind=str(facts.get("benefit_kind", "")),
                    status=str(node.get("status", "")),
                    value_minor=int(node.get("value_minor") or 0),
                    eligible_lines=int(facts.get("eligible_lines") or 0),
                    on_chosen_instrument=manifest_id == chosen,
                    instrument_attested=c.get("status") == STATUS_ATTESTED,
                )
            )

    return DecisionObservation(
        decision_id=decision_id,
        cart_hash=cart_hash,
        evaluated_at=evaluated_at,
        criterion=criterion,
        chosen_manifest_id=chosen,
        chosen_attested=chosen_value is not None,
        attested_candidates=len(attested),
        margin_minor=margin,
        benefits=tuple(observations),
    )


def _first_str(obj: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# --------------------------------------------------------------------------------------
# Settings.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AttributionSettings:
    """Every threshold this module applies, named and carried in the output.

    No chart can be screenshotted without them, which is the same discipline the exposure
    book uses: a 2x2 with unstated cut points is a picture, not an analysis.
    """

    # Below this many in-play observations a benefit is not classified at all. A benefit
    # seen three times has not earned the word "dead weight".
    min_observations: int = 5

    # At or above this share of its in-play appearances landing in a winning derivation, a
    # benefit counts as often decisive. Basis points, so the whole model stays integer.
    decisive_threshold_bp: int = 2_500

    # Annualised issuer cost per member at or above which a benefit counts as high cost.
    # None means "use the median of the supplied cost basis", which adapts to the portfolio
    # and is deterministic for a given corpus.
    cost_threshold_minor: int | None = None

    def __post_init__(self) -> None:
        if self.min_observations < 1:
            raise AttributionError(
                f"min_observations must be >= 1, got {self.min_observations}"
            )
        if not 0 <= self.decisive_threshold_bp <= BP:
            raise AttributionError(
                f"decisive_threshold_bp must be between 0 and {BP}, got "
                f"{self.decisive_threshold_bp}"
            )
        if self.cost_threshold_minor is not None and self.cost_threshold_minor < 0:
            raise AttributionError(
                f"cost_threshold_minor must be >= 0 or None, got {self.cost_threshold_minor}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_observations": self.min_observations,
            "decisive_threshold_bp": self.decisive_threshold_bp,
            "cost_threshold_minor": self.cost_threshold_minor,
        }

    def describe(self) -> list[str]:
        threshold = (
            "median of the supplied cost basis"
            if self.cost_threshold_minor is None
            else fmt_money(self.cost_threshold_minor)
        )
        return [
            f"min observations      {self.min_observations} in-play appearances before classifying",
            f"decisive threshold    {self.decisive_threshold_bp / 100:.2f}% of in-play appearances",
            f"high-cost threshold   {threshold}",
            f"NOTE: {COST_BASIS_NOTE}",
            f"NOTE: {CLAIM_BOUNDARY}",
        ]


# --------------------------------------------------------------------------------------
# Results.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BenefitAttribution:
    """One benefit's record across the corpus. All money is integer minor units."""

    key: BenefitKey
    label: str
    benefit_kind: str
    in_play: int
    applied: int
    winning: int
    pivotal: int
    contested_in_play: int
    not_enrolled_while_eligible: int
    exhausted_while_eligible: int
    realized_value_minor: int
    annual_cost_minor: int | None
    quadrant: str

    @property
    def applied_rate_bp(self) -> int:
        """Of the carts where it was in play, how often the allocator used it."""
        return 0 if self.in_play <= 0 else self.applied * BP // self.in_play

    @property
    def decisive_rate_bp(self) -> int:
        """Of the carts where it was in play, how often it was in a winning derivation."""
        return 0 if self.in_play <= 0 else self.winning * BP // self.in_play

    @property
    def pivotal_rate_bp(self) -> int:
        """Of the contested decisions where it was in play, how often it covered the margin."""
        return 0 if self.contested_in_play <= 0 else self.pivotal * BP // self.contested_in_play

    @property
    def action(self) -> str:
        return ACTION_BY_QUADRANT[self.quadrant]

    def rationale(self) -> str:
        cost = (
            "no issuer cost basis supplied"
            if self.annual_cost_minor is None
            else f"issuer-supplied annual cost {fmt_money(self.annual_cost_minor)}"
        )
        return (
            f"in play on {self.in_play} cart(s), in a winning derivation on {self.winning} "
            f"({self.decisive_rate_bp / 100:.1f}%), pivotal on {self.pivotal} of "
            f"{self.contested_in_play} contested; {cost}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.key.to_dict(),
            "label": self.label,
            "benefit_kind": self.benefit_kind,
            "in_play": self.in_play,
            "applied": self.applied,
            "winning": self.winning,
            "pivotal": self.pivotal,
            "contested_in_play": self.contested_in_play,
            "not_enrolled_while_eligible": self.not_enrolled_while_eligible,
            "exhausted_while_eligible": self.exhausted_while_eligible,
            "realized_value_minor": self.realized_value_minor,
            "realized_value_display": fmt_money(self.realized_value_minor),
            "annual_cost_minor": self.annual_cost_minor,
            "applied_rate_bp": self.applied_rate_bp,
            "decisive_rate_bp": self.decisive_rate_bp,
            "pivotal_rate_bp": self.pivotal_rate_bp,
            "quadrant": self.quadrant,
            "action": self.action,
            "rationale": self.rationale(),
            "observes": CLAIM_BOUNDARY,
        }


@dataclass(frozen=True)
class AttributionReport:
    version: str
    generated_at: int
    settings: AttributionSettings
    entries: tuple[BenefitAttribution, ...]
    decisions: int
    contested_decisions: int
    duplicates_ignored: int
    resolved_cost_threshold_minor: int | None
    criteria: tuple[str, ...]

    def quadrant(self, name: str) -> tuple[BenefitAttribution, ...]:
        if name not in QUADRANTS:
            raise AttributionError(f"unknown quadrant {name!r}; expected one of {QUADRANTS}")
        return tuple(e for e in self.entries if e.quadrant == name)

    def cut_candidates(self) -> tuple[BenefitAttribution, ...]:
        """Dead weight, most expensive first. The list this whole module exists to produce."""
        return tuple(
            sorted(
                self.quadrant(QUADRANT_DEAD_WEIGHT),
                key=lambda e: (-(e.annual_cost_minor or 0), e.key.as_str()),
            )
        )

    def activation_gaps(self) -> tuple[BenefitAttribution, ...]:
        """Benefits that admitted a cart line while the member was not enrolled.

        A benefit-design and enrollment-flow finding for the issuer. It is not a member
        prompt: surfacing an unused benefit to a Card Member converts breakage into
        recognised expense, which is the opposite of what this corpus is for.
        """
        return tuple(
            sorted(
                (e for e in self.entries if e.not_enrolled_while_eligible > 0),
                key=lambda e: (-e.not_enrolled_while_eligible, e.key.as_str()),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "settings": self.settings.to_dict(),
            "settings_notes": self.settings.describe(),
            "entries": [e.to_dict() for e in self.entries],
            "decisions": self.decisions,
            "contested_decisions": self.contested_decisions,
            "duplicates_ignored": self.duplicates_ignored,
            "resolved_cost_threshold_minor": self.resolved_cost_threshold_minor,
            "criteria": list(self.criteria),
            "quadrants": {
                name: [e.key.as_str() for e in self.quadrant(name)] for name in QUADRANTS
            },
            "cut_candidates": [e.key.as_str() for e in self.cut_candidates()],
            "observes": CLAIM_BOUNDARY,
            "pivotal_definition": PIVOTAL_DEFINITION,
            "cost_basis": COST_BASIS_NOTE,
            "audience": NEVER_A_NUDGE,
        }

    def render_text(self, width: int = 104) -> str:
        rule = "=" * width
        thin = "-" * width
        out = [
            rule,
            "BENEFIT ATTRIBUTION — SELECTION INFLUENCE AT THE MOMENT OF CHOICE".center(width),
            (
                f"{self.decisions} decision(s), {self.contested_decisions} contested, "
                f"generated at {self.generated_at}"
            ).center(width),
            rule,
            "",
            f"  {'benefit':<38}{'in play':>8}{'winning':>9}{'decisive':>10}"
            f"{'pivotal':>9}{'annual cost':>14}  quadrant",
            thin,
        ]
        for e in self.entries:
            cost = "—" if e.annual_cost_minor is None else fmt_money(e.annual_cost_minor)
            out.append(
                f"  {e.label[:36]:<38}{e.in_play:>8}{e.winning:>9}"
                f"{e.decisive_rate_bp / 100:>9.1f}%{e.pivotal:>9}{cost:>14}  {e.quadrant}"
            )
        out += ["", thin, "CUT CANDIDATES (high cost, rarely decisive)", thin]
        cuts = self.cut_candidates()
        if not cuts:
            out.append("  none at these thresholds")
        for e in cuts:
            out.append(f"  {e.label} — {e.rationale()}")
        out += ["", thin, "SETTINGS", thin]
        for line in self.settings.describe():
            out.append(f"  {line}")
        out += ["", f"  {PIVOTAL_DEFINITION}", "", f"  {NEVER_A_NUDGE}", rule]
        return "\n".join(out)


# --------------------------------------------------------------------------------------
# The aggregation.
# --------------------------------------------------------------------------------------


def median_cost_minor(values: Sequence[int]) -> int | None:
    """Integer median. Even-length corpora take the floor of the two middles.

    Integer throughout so the threshold is reproducible from the corpus alone.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def attribute(
    observations: Iterable[DecisionObservation],
    *,
    now: int,
    cost_basis: Mapping[BenefitKey | str, int] | None = None,
    settings: AttributionSettings | None = None,
) -> AttributionReport:
    """Aggregate a corpus of decision observations into the 2x2.

    `cost_basis` is the issuer's annualised cost per member for a benefit, keyed by
    `BenefitKey` or by its string form. It is an input, never a derivation: a benefit with
    no supplied cost lands in `QUADRANT_NO_COST_BASIS` rather than being guessed at.

    `now` is an explicit parameter; no clock is read.
    """
    settings = settings or AttributionSettings()
    costs = {_key_str(k): int(v) for k, v in (cost_basis or {}).items()}

    seen_decisions: set[str] = set()
    duplicates = 0
    decisions = 0
    contested = 0
    criteria: set[str] = set()

    acc: dict[str, dict[str, Any]] = {}

    for decision in observations:
        if not isinstance(decision, DecisionObservation):
            raise AttributionError(
                f"attribute() takes DecisionObservation values, got "
                f"{type(decision).__name__}; build them with observe_receipt() for corpus "
                "records or observe_evaluation() for an in-memory evaluation"
            )
        if decision.decision_id in seen_decisions:
            # A receipt appearing twice would double-count its winner. First occurrence
            # wins, deterministically.
            duplicates += 1
            continue
        seen_decisions.add(decision.decision_id)
        decisions += 1
        if decision.contested:
            contested += 1
        if decision.criterion:
            criteria.add(decision.criterion)

        for obs in decision.benefits:
            if obs.status == BENEFIT_UNPRICED:
                # Unpriced value is carried in the receipt so the record shows the agent saw
                # it. Scoring its influence would put a number on the thing we deliberately
                # refuse to price.
                continue
            row = acc.setdefault(
                obs.key.as_str(),
                {
                    "key": obs.key,
                    "label": obs.label,
                    "benefit_kind": obs.benefit_kind,
                    "in_play": 0,
                    "applied": 0,
                    "winning": 0,
                    "pivotal": 0,
                    "contested_in_play": 0,
                    "not_enrolled": 0,
                    "exhausted": 0,
                    "value": 0,
                },
            )
            if obs.in_play:
                row["in_play"] += 1
                if decision.pivotal_testable:
                    row["contested_in_play"] += 1
            if obs.applied:
                row["applied"] += 1
            if obs.winning:
                row["winning"] += 1
                row["value"] += obs.value_minor
            if decision.pivotal(obs):
                row["pivotal"] += 1
            if obs.status == BENEFIT_NOT_ENROLLED and obs.eligible_lines > 0:
                row["not_enrolled"] += 1
            if obs.status == BENEFIT_EXHAUSTED and obs.eligible_lines > 0:
                row["exhausted"] += 1

    threshold = settings.cost_threshold_minor
    if threshold is None:
        # Median over the whole supplied cost basis, not only the benefits this corpus
        # happened to see, so the high/low split does not move when a benefit is absent
        # from a week's traffic.
        threshold = median_cost_minor([costs[k] for k in sorted(costs)])

    entries = tuple(
        _finalize(row, cost=costs.get(key), threshold=threshold, settings=settings)
        for key, row in sorted(acc.items())
    )

    return AttributionReport(
        version=ATTRIBUTION_VERSION,
        generated_at=now,
        settings=settings,
        entries=entries,
        decisions=decisions,
        contested_decisions=contested,
        duplicates_ignored=duplicates,
        resolved_cost_threshold_minor=threshold,
        criteria=tuple(sorted(criteria)),
    )


def _finalize(
    row: Mapping[str, Any],
    *,
    cost: int | None,
    threshold: int | None,
    settings: AttributionSettings,
) -> BenefitAttribution:
    in_play = int(row["in_play"])
    winning = int(row["winning"])
    decisive_bp = 0 if in_play <= 0 else winning * BP // in_play

    if in_play < settings.min_observations:
        quadrant = QUADRANT_INSUFFICIENT_EVIDENCE
    elif cost is None or threshold is None:
        quadrant = QUADRANT_NO_COST_BASIS
    else:
        high_cost = cost >= threshold
        often = decisive_bp >= settings.decisive_threshold_bp
        if high_cost:
            quadrant = QUADRANT_LOAD_BEARING if often else QUADRANT_DEAD_WEIGHT
        else:
            quadrant = QUADRANT_OPTION if often else QUADRANT_NOISE

    return BenefitAttribution(
        key=row["key"],
        label=str(row["label"]),
        benefit_kind=str(row["benefit_kind"]),
        in_play=in_play,
        applied=int(row["applied"]),
        winning=winning,
        pivotal=int(row["pivotal"]),
        contested_in_play=int(row["contested_in_play"]),
        not_enrolled_while_eligible=int(row["not_enrolled"]),
        exhausted_while_eligible=int(row["exhausted"]),
        realized_value_minor=int(row["value"]),
        annual_cost_minor=cost,
        quadrant=quadrant,
    )
