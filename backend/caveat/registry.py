"""Agent operator registry and behavioural risk score.

Amex's ACE Developer Kit lists five services; **Identify (Agent Registration) is still
marked under development**. This module is that missing service, plus the thing a registry
is only useful with: a number that moves when an agent misbehaves.

The score is computed from an operator's OWN telemetry — the signals `engine.py` already
records against `store.operator_events` — and nothing else. No model, no third-party
reputation feed, no hand-tuned per-agent overrides. Every step is arithmetic a judge can
redo on paper:

    1. count the signals                    (store.operator_event_counts)
    2. divide by an explicit denominator    -> observed rate
    3. shrink toward a documented prior     -> smoothed rate  (small samples do not shout)
    4. divide by a saturation rate, clamp   -> expression in [0, 1]
    5. multiply by a weight, sum            -> behavioural points
    6. add an unproven premium that decays  -> score in [0, 100], HIGHER = RISKIER

`authorized` is deliberately not weighted. It is not a risk signal; it is the denominator
that turns counts into rates, which is the whole reason a high-volume operator with three
incidents is not scored like a brand-new operator with three incidents.

Every constant below is a field on `RiskWeights` and can be overridden per deployment. The
defaults are engineering judgement calibrated against this prototype's own synthetic
telemetry — they are not measured industry rates, and nothing here should be presented as
one.

Honest limitations:
  * `scope_escalation_attempt` has no natural per-delegation denominator, because the
    engine records rejected delegations but not accepted ones. We use "share of all
    governed actions", which is documented on the signal itself rather than hidden.
  * The score is behavioural only. It has no view of an operator's code, its provenance,
    or its attestation, and it says nothing about an operator that has simply not acted
    yet beyond "unproven".
  * The suggested exposure cap is advice to an underwriter. The PDP does not consult it;
    authority still comes only from the mandate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from .constraints import fmt_money
from .store import Store

# --------------------------------------------------------------------------------------
# Signal vocabulary. These strings are exactly what engine.py writes; never inline them.
# --------------------------------------------------------------------------------------

SIGNAL_AUTHORIZED = "authorized"
SIGNAL_DENIED = "denied"
SIGNAL_STEP_UP = "step_up_required"
SIGNAL_CART_DIVERGENCE = "cart_divergence"
SIGNAL_INJECTION_ABSORBED = "injection_absorbed"
SIGNAL_SCOPE_ESCALATION = "scope_escalation_attempt"

KNOWN_SIGNALS = (
    SIGNAL_AUTHORIZED,
    SIGNAL_DENIED,
    SIGNAL_STEP_UP,
    SIGNAL_CART_DIVERGENCE,
    SIGNAL_INJECTION_ABSORBED,
    SIGNAL_SCOPE_ESCALATION,
)

# Exactly one of these is written per PDP decision, so their sum is the decision count.
DECISION_OUTCOME_SIGNALS = (SIGNAL_AUTHORIZED, SIGNAL_DENIED, SIGNAL_STEP_UP)

# Denominators a signal can be expressed against.
BASIS_DECISIONS = "decisions"
BASIS_GOVERNED_ACTIONS = "governed_actions"

# Bands. A band is what an underwriter acts on; the score is what they audit.
BAND_TRUSTED = "trusted"
BAND_WATCH = "watch"
BAND_RESTRICTED = "restricted"
BAND_SUSPENDED = "suspended"

BAND_ORDER = (BAND_TRUSTED, BAND_WATCH, BAND_RESTRICTED, BAND_SUSPENDED)

UNREGISTERED_NAME = "(unregistered operator)"


@dataclass(frozen=True)
class SignalWeight:
    """How one telemetry signal turns into risk points.

    weight           points contributed when the signal is fully expressed.
    saturation_rate  the rate at which the signal is considered fully expressed. Above
                     this, more of the same signal adds nothing — the operator is already
                     as bad as this signal can describe.
    prior_rate       what we assume before we have evidence. Chosen at a quarter of the
                     saturation rate so an operator with no history is neither clean nor
                     condemned by this signal alone.
    basis            which denominator the rate is taken over.
    """

    signal: str
    weight: float
    saturation_rate: float
    prior_rate: float
    basis: str
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "weight": self.weight,
            "saturation_rate": self.saturation_rate,
            "prior_rate": self.prior_rate,
            "basis": self.basis,
            "rationale": self.rationale,
        }


DEFAULT_SIGNAL_WEIGHTS: tuple[SignalWeight, ...] = (
    SignalWeight(
        signal=SIGNAL_SCOPE_ESCALATION,
        weight=45.0,
        saturation_rate=0.02,
        prior_rate=0.005,
        basis=BASIS_GOVERNED_ACTIONS,
        rationale=(
            "an agent that tried to widen its own mandate is the only signal here that is "
            "an intent to exceed authority rather than a bad outcome; weighted highest"
        ),
    ),
    SignalWeight(
        signal=SIGNAL_INJECTION_ABSORBED,
        weight=25.0,
        saturation_rate=0.05,
        prior_rate=0.0125,
        basis=BASIS_DECISIONS,
        rationale=(
            "the agent was successfully manipulated into presenting a cart nobody signed; "
            "the mandate layer caught it, but the operator's surface is demonstrably reachable"
        ),
    ),
    SignalWeight(
        signal=SIGNAL_CART_DIVERGENCE,
        weight=15.0,
        saturation_rate=0.10,
        prior_rate=0.025,
        basis=BASIS_DECISIONS,
        rationale=(
            "executed cart drifted from signed intent; not always hostile, but an operator "
            "whose carts routinely move after signature has weak execution control"
        ),
    ),
    SignalWeight(
        signal=SIGNAL_DENIED,
        weight=10.0,
        saturation_rate=0.25,
        prior_rate=0.0625,
        basis=BASIS_DECISIONS,
        rationale=(
            "a denial is the governance layer working, not a loss; it is priced because a "
            "high denial rate means the agent repeatedly plans outside its own mandate"
        ),
    ),
    SignalWeight(
        signal=SIGNAL_STEP_UP,
        weight=5.0,
        saturation_rate=0.50,
        prior_rate=0.125,
        basis=BASIS_DECISIONS,
        rationale=(
            "step-ups are a designed outcome, not a failure; weighted lowest, and present "
            "only because an agent that constantly needs a human is not operating autonomously"
        ),
    ),
)


@dataclass(frozen=True)
class RiskWeights:
    """The whole scoring policy, in one auditable object.

    Nothing about the score lives outside this dataclass and the telemetry. Change a field,
    re-run `assess`, and the number moves in a way you can explain in a sentence.
    """

    signals: tuple[SignalWeight, ...] = DEFAULT_SIGNAL_WEIGHTS

    # Additive (Laplace-style) shrinkage. `prior_weight` is in pseudo-observations: with
    # the default of 5, an operator needs five real actions before its own telemetry
    # outweighs the prior.
    prior_weight: float = 5.0

    # An operator with no history is not trusted; it is unproven. This premium is added on
    # top of behavioural points and decays as observations accumulate, so evidence — good
    # or bad — always displaces assumption.
    unproven_premium: float = 50.0
    confidence_pseudocount: float = 20.0

    # Never registered, yet holding a mandate. This is the shadow-agent case, and the exact
    # gap an agent registry exists to close.
    unregistered_premium: float = 15.0

    # Band cutoffs, in score points.
    band_trusted_below: float = 25.0
    band_watch_below: float = 55.0
    band_restricted_below: float = 80.0

    # Suggested per-transaction exposure cap, integer minor units (paise).
    base_exposure_cap_minor: int = 2_500_000  # ₹25,000 for a spotless operator
    cap_curve_exponent: int = 2

    def signal(self, name: str) -> SignalWeight | None:
        for s in self.signals:
            if s.signal == name:
                return s
        return None

    def with_signal(self, name: str, **changes: Any) -> "RiskWeights":
        """Tune one signal without restating the rest."""
        out = []
        for s in self.signals:
            out.append(replace(s, **changes) if s.signal == name else s)
        return replace(self, signals=tuple(out))

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": [s.to_dict() for s in self.signals],
            "prior_weight": self.prior_weight,
            "unproven_premium": self.unproven_premium,
            "confidence_pseudocount": self.confidence_pseudocount,
            "unregistered_premium": self.unregistered_premium,
            "bands": {
                BAND_TRUSTED: f"score < {self.band_trusted_below}",
                BAND_WATCH: f"{self.band_trusted_below} ≤ score < {self.band_watch_below}",
                BAND_RESTRICTED: f"{self.band_watch_below} ≤ score < {self.band_restricted_below}",
                BAND_SUSPENDED: f"score ≥ {self.band_restricted_below}",
            },
            "base_exposure_cap_minor": self.base_exposure_cap_minor,
            "cap_curve_exponent": self.cap_curve_exponent,
        }


@dataclass(frozen=True)
class SignalEvidence:
    """One row of the evidence trail: how a signal became points."""

    signal: str
    count: int
    basis: str
    denominator: int
    observed_rate: float
    smoothed_rate: float
    saturation_rate: float
    expression: float
    weight: float
    points: float

    def describe(self) -> str:
        return (
            f"{self.signal}: {self.count}/{self.denominator} {self.basis} "
            f"(observed {self.observed_rate:.4f}, smoothed {self.smoothed_rate:.4f}, "
            f"saturates at {self.saturation_rate:.4f}) → "
            f"{self.expression:.3f} × {self.weight:g} = {self.points:.2f} pts"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "count": self.count,
            "basis": self.basis,
            "denominator": self.denominator,
            "observed_rate": round(self.observed_rate, 6),
            "smoothed_rate": round(self.smoothed_rate, 6),
            "saturation_rate": self.saturation_rate,
            "expression": round(self.expression, 6),
            "weight": self.weight,
            "points": round(self.points, 3),
            "describe": self.describe(),
        }


@dataclass(frozen=True)
class RiskAssessment:
    """A score, its band, its cap, and the arithmetic that produced all three."""

    operator_id: str
    name: str
    registered: bool
    registered_at: int | None
    assessed_at: int
    score: float
    band: str
    behavioural_points: float
    unproven_premium_points: float
    unregistered_premium_points: float
    confidence: float
    decisions: int
    governed_actions: int
    counts: dict[str, int]
    signals: tuple[SignalEvidence, ...]
    suggested_exposure_cap_minor: int
    weights: dict[str, Any]

    def drivers(self, top: int = 3) -> tuple[SignalEvidence, ...]:
        """The signals that actually moved the number, largest first."""
        ranked = sorted(self.signals, key=lambda s: s.points, reverse=True)
        return tuple(s for s in ranked[:top] if s.points > 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_id": self.operator_id,
            "name": self.name,
            "registered": self.registered,
            "registered_at": self.registered_at,
            "assessed_at": self.assessed_at,
            "score": round(self.score, 2),
            "band": self.band,
            "behavioural_points": round(self.behavioural_points, 3),
            "unproven_premium_points": round(self.unproven_premium_points, 3),
            "unregistered_premium_points": round(self.unregistered_premium_points, 3),
            "confidence": round(self.confidence, 4),
            "decisions": self.decisions,
            "governed_actions": self.governed_actions,
            "counts": dict(self.counts),
            "signals": [s.to_dict() for s in self.signals],
            "drivers": [s.describe() for s in self.drivers()],
            "suggested_exposure_cap_minor": self.suggested_exposure_cap_minor,
            "suggested_exposure_cap_display": fmt_money(self.suggested_exposure_cap_minor),
            "weights": self.weights,
        }

    def render_text(self, width: int = 78) -> str:
        rule = "─" * width
        lines = [
            rule,
            f"OPERATOR RISK  {self.operator_id}  ({self.name})",
            rule,
            f"  score              {self.score:6.2f} / 100   (higher = riskier)",
            f"  band               {self.band}",
            f"  registered         {'yes' if self.registered else 'NO — shadow operator'}",
            f"  decisions seen     {self.decisions}",
            f"  governed actions   {self.governed_actions}",
            f"  confidence         {self.confidence:.3f}",
            f"  exposure cap       {fmt_money(self.suggested_exposure_cap_minor)} per transaction",
            "",
            "  how the score was computed",
        ]
        for s in self.signals:
            lines.append(f"    {s.describe()}")
        lines.append(f"    behavioural subtotal          {self.behavioural_points:6.2f} pts")
        lines.append(f"    unproven premium (decays)     {self.unproven_premium_points:6.2f} pts")
        if self.unregistered_premium_points:
            lines.append(
                f"    unregistered premium          {self.unregistered_premium_points:6.2f} pts"
            )
        lines.append(f"    total                         {self.score:6.2f} pts")
        lines.append(rule)
        return "\n".join(lines)


class AgentRegistry:
    """The registry service: who the operators are, and how they have behaved.

    Reads and writes only through `store`, so the scoring has no private state that could
    drift from what the ledger and the event table say.
    """

    def __init__(self, store: Store, weights: RiskWeights | None = None) -> None:
        self.store = store
        self.weights = weights or RiskWeights()

    # -- registration ------------------------------------------------------------------

    def register(
        self,
        operator_id: str,
        name: str,
        *,
        now: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.store.put_operator(operator_id, name, now, metadata)

    def is_registered(self, operator_id: str) -> bool:
        return self.store.get_operator(operator_id) is not None

    def operators(self) -> list[dict[str, Any]]:
        return [
            {
                "operator_id": r["operator_id"],
                "name": r["name"],
                "registered_at": r["registered_at"],
            }
            for r in self.store.all_operators()
        ]

    def known_operator_ids(self) -> tuple[str, ...]:
        """Registered operators plus every holder that appears on a mandate.

        The second half matters: sub-agents nobody registered still hold credentials, and
        an underwriter needs them in the book precisely because they were never onboarded.
        """
        ids = {r["operator_id"] for r in self.store.all_operators()}
        for row in self.store.all_mandates():
            ids.add(row["holder"])
        return tuple(sorted(ids))

    def record_signal(
        self,
        operator_id: str,
        signal: str,
        *,
        now: int,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append one behavioural signal. Rejects names outside the closed vocabulary."""
        if signal not in KNOWN_SIGNALS:
            raise ValueError(f"unknown operator signal: {signal!r}")
        self.store.record_operator_event(operator_id, signal, now, detail)

    # -- scoring -----------------------------------------------------------------------

    def assess(self, operator_id: str, *, now: int) -> RiskAssessment:
        counts = self.store.operator_event_counts(operator_id)
        row = self.store.get_operator(operator_id)
        w = self.weights

        decisions = sum(counts.get(s, 0) for s in DECISION_OUTCOME_SIGNALS)
        governed = decisions + counts.get(SIGNAL_SCOPE_ESCALATION, 0)

        evidence: list[SignalEvidence] = []
        behavioural = 0.0
        for spec in w.signals:
            denominator = governed if spec.basis == BASIS_GOVERNED_ACTIONS else decisions
            count = counts.get(spec.signal, 0)
            observed = (count / denominator) if denominator else 0.0
            smoothed = (count + w.prior_weight * spec.prior_rate) / (denominator + w.prior_weight)
            expression = min(1.0, smoothed / spec.saturation_rate) if spec.saturation_rate else 0.0
            points = expression * spec.weight
            behavioural += points
            evidence.append(
                SignalEvidence(
                    signal=spec.signal,
                    count=count,
                    basis=spec.basis,
                    denominator=denominator,
                    observed_rate=observed,
                    smoothed_rate=smoothed,
                    saturation_rate=spec.saturation_rate,
                    expression=expression,
                    weight=spec.weight,
                    points=points,
                )
            )

        confidence = governed / (governed + w.confidence_pseudocount) if w.confidence_pseudocount else 1.0
        unproven = w.unproven_premium * (1.0 - confidence)
        unregistered = 0.0 if row is not None else w.unregistered_premium

        score = max(0.0, min(100.0, behavioural + unproven + unregistered))
        band = band_for(score, w)

        return RiskAssessment(
            operator_id=operator_id,
            name=row["name"] if row is not None else UNREGISTERED_NAME,
            registered=row is not None,
            registered_at=int(row["registered_at"]) if row is not None else None,
            assessed_at=now,
            score=score,
            band=band,
            behavioural_points=behavioural,
            unproven_premium_points=unproven,
            unregistered_premium_points=unregistered,
            confidence=confidence,
            decisions=decisions,
            governed_actions=governed,
            counts=dict(counts),
            signals=tuple(evidence),
            suggested_exposure_cap_minor=exposure_cap_minor(score, band, w),
            weights=w.to_dict(),
        )

    def portfolio(
        self, *, now: int, operator_ids: Sequence[str] | None = None
    ) -> tuple[RiskAssessment, ...]:
        """Assess every known operator, riskiest first."""
        ids: Iterable[str] = operator_ids if operator_ids is not None else self.known_operator_ids()
        out = [self.assess(op, now=now) for op in ids]
        out.sort(key=lambda a: (-a.score, a.operator_id))
        return tuple(out)


def band_for(score: float, weights: RiskWeights | None = None) -> str:
    w = weights or RiskWeights()
    if score < w.band_trusted_below:
        return BAND_TRUSTED
    if score < w.band_watch_below:
        return BAND_WATCH
    if score < w.band_restricted_below:
        return BAND_RESTRICTED
    return BAND_SUSPENDED


def exposure_cap_minor(score: float, band: str, weights: RiskWeights | None = None) -> int:
    """Suggested per-transaction exposure cap, integer minor units.

    Quadratic decay in the score: a spotless operator gets the full base cap, the cap has
    halved by the time the score reaches ~30, and a suspended operator gets nothing. The
    arithmetic is integer so the cap can be quoted as a mandate `AmountMax` directly.
    """
    w = weights or RiskWeights()
    if band == BAND_SUSPENDED:
        return 0
    s = max(0, min(100, int(round(score))))
    exp = w.cap_curve_exponent
    return w.base_exposure_cap_minor * ((100 - s) ** exp) // (100**exp)
