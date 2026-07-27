"""Entailment checking: does a child's declared scope stay inside its parent's?

Two delegation paths exist in the real ecosystem and CAVEAT secures both:

  chained          caveats accumulate on the macaroon and verification evaluates all of
                   them under an HMAC chain, so a caveat cannot be removed without the
                   root key. Attenuation is a property of the credential.

  declared-scope   when a mandate crosses a protocol boundary (ACP delegated payment,
                   AP2 Cart Mandate, MCP tool call) the chain does not travel with it and
                   the child presents its own declared constraint set. This is where
                   authority silently widens today, and it is what this module guards.

Set containment is the wrong test for money. Two escalations a subset check waves through:

  dropped constraint   parent {amount ≤ ₹5,000, category ∈ groceries}; child declares
                       {amount ≤ ₹5,000}. The child has FEWER caveats and every shared
                       caveat is equally tight — a naive check calls it narrower. It is
                       broader: the child can now buy anything.

  widened bound        child declares {amount ≤ ₹50,000} under a parent of ₹5,000.

Delegation is accepted iff  UNSAT(child_constraints ∧ ¬parent_constraints).
When SAT, the model is a concrete transaction the child would authorize and the parent
would not — that is the counterexample we show the operator.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import z3

from .constraints import Constraint, ConstraintSet, Vars, fmt_money

# Entailment is decidable here because the DSL is quantifier-free linear integer
# arithmetic plus finite-set membership. A timeout still guards against pathological
# inputs so a delegation call can never hang the PDP.
SOLVER_TIMEOUT_MS = 2_000


@dataclass(frozen=True)
class Counterexample:
    """A concrete transaction the child authorizes but the parent does not."""

    amount: int
    merchant: str
    mcc: str
    category: str
    geo: str
    ts: int
    cumulative: int
    violated: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "amount_display": fmt_money(self.amount),
            "merchant": self.merchant,
            "mcc": self.mcc,
            "category": self.category,
            "geo": self.geo,
            "ts": self.ts,
            "cumulative": self.cumulative,
            "violated": list(self.violated),
        }

    def summary(self) -> str:
        bits = [f"amount={fmt_money(self.amount)}"]
        for label, value in (
            ("merchant", self.merchant),
            ("mcc", self.mcc),
            ("category", self.category),
            ("geo", self.geo),
        ):
            bits.append(f"{label}={value}")
        head = ", ".join(bits)
        if self.violated:
            return f"{head} satisfies the child, violates the parent ({'; '.join(self.violated)})"
        return f"{head} satisfies the child, violates the parent"


@dataclass(frozen=True)
class EntailmentResult:
    entailed: bool
    elapsed_ms: float
    solver_result: str
    counterexample: Counterexample | None = None
    child_scope: tuple[str, ...] = field(default_factory=tuple)
    parent_scope: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entailed": self.entailed,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "solver_result": self.solver_result,
            "counterexample": self.counterexample.to_dict() if self.counterexample else None,
            "child_scope": list(self.child_scope),
            "parent_scope": list(self.parent_scope),
        }


def _decode(v: Vars, model: z3.ModelRef, domain: str, var: z3.ArithRef) -> str:
    """Read a symbol out of the model and map it back to a human-readable value.

    Z3 may pick an integer that no allowlist on either side interned. That is not noise:
    it means "some value outside every set anyone named", which is precisely how a
    dropped constraint reveals itself.
    """
    raw = model.eval(var, model_completion=True)
    as_int = raw.as_long()
    decoded = v.interner.decode(domain, as_int)
    return decoded if decoded is not None else f"<any {domain} outside the named sets>"


def entails(child: ConstraintSet, parent: ConstraintSet) -> EntailmentResult:
    """Prove the child's authority region is a subset of the parent's.

    Returns entailed=True only on UNSAT. An `unknown` result (timeout) fails closed.
    """
    started = time.perf_counter()
    v = Vars()

    # Both regions must be built against the same Vars so the interner is shared and the
    # two sides genuinely talk about the same symbols.
    child_region = child.region(v)
    parent_region = parent.region(v)

    solver = z3.Solver()
    solver.set("timeout", SOLVER_TIMEOUT_MS)
    solver.add(child_region)
    solver.add(z3.Not(parent_region))

    result = solver.check()
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if result == z3.unsat:
        return EntailmentResult(
            entailed=True,
            elapsed_ms=elapsed_ms,
            solver_result="unsat",
            child_scope=tuple(child.describe()),
            parent_scope=tuple(parent.describe()),
        )

    if result == z3.unknown:
        # Fail closed: an unproven narrowing is not a narrowing.
        return EntailmentResult(
            entailed=False,
            elapsed_ms=elapsed_ms,
            solver_result="unknown",
            child_scope=tuple(child.describe()),
            parent_scope=tuple(parent.describe()),
        )

    model = solver.model()
    violated = _violated_parent_constraints(parent, v, model)
    counterexample = Counterexample(
        amount=model.eval(v.amount, model_completion=True).as_long(),
        merchant=_decode(v, model, "merchant", v.merchant),
        mcc=_decode(v, model, "mcc", v.mcc),
        category=_decode(v, model, "category", v.category),
        geo=_decode(v, model, "geo", v.geo),
        ts=model.eval(v.ts, model_completion=True).as_long(),
        cumulative=model.eval(v.cumulative, model_completion=True).as_long(),
        violated=violated,
    )
    return EntailmentResult(
        entailed=False,
        elapsed_ms=elapsed_ms,
        solver_result="sat",
        counterexample=counterexample,
        child_scope=tuple(child.describe()),
        parent_scope=tuple(parent.describe()),
    )


def _violated_parent_constraints(
    parent: ConstraintSet, v: Vars, model: z3.ModelRef
) -> tuple[str, ...]:
    """Name exactly which parent constraints the counterexample breaks."""
    out: list[str] = []
    for c in parent.constraints:
        try:
            holds = model.eval(c.assertion(v), model_completion=True)
        except z3.Z3Exception:  # pragma: no cover - defensive
            continue
        if z3.is_false(holds):
            out.append(c.describe())
    return tuple(out)


def naive_subset_check(child: ConstraintSet, parent: ConstraintSet) -> bool:
    """The check a reasonable engineer writes first — and why it is not enough.

    "Every caveat the child declares is present on the parent and no looser." It passes
    the dropped-constraint escalation, because dropping a caveat trivially satisfies it.
    Kept here so the console can show both verdicts side by side; it is never used to
    authorize anything.
    """
    parent_by_kind: dict[str, Constraint] = {c.kind: c for c in parent.constraints}
    for c in child.constraints:
        counterpart = parent_by_kind.get(c.kind)
        if counterpart is None:
            continue  # child introduced a constraint the parent lacks: strictly narrower
        if not _no_looser(c, counterpart):
            return False
    return True


def _no_looser(child: Constraint, parent: Constraint) -> bool:
    cv, pv = getattr(child, "value", None), getattr(parent, "value", None)
    if isinstance(cv, int) and isinstance(pv, int):
        if child.kind == "not_before":
            return cv >= pv
        return cv <= pv
    cvals, pvals = getattr(child, "values", None), getattr(parent, "values", None)
    if cvals is not None and pvals is not None:
        return set(cvals).issubset(set(pvals))
    if child.kind == "velocity_max" and parent.kind == "velocity_max":
        return child.window_s == parent.window_s and child.count <= parent.count  # type: ignore[attr-defined]
    return True
