"""Typed constraint DSL for payment mandates.

The DSL is deliberately restricted to quantifier-free linear integer arithmetic plus
finite-set membership so that entailment stays decidable and fast (see entailment.py).
Money is always integer minor units (paise). Never floats.

Each constraint knows three things:
  * how to serialise itself canonically (it lives inside an HMAC'd caveat),
  * how to compile to a Z3 assertion over the shared decision variables,
  * how to evaluate against a concrete decision context at authorization time.

Constraints split into two families:

  structural  amount / merchant / mcc / category / geo / expiry — verifiable offline
              from the credential alone.
  stateful    cumulative budget and velocity — require the ledger to evaluate, and so
              are NOT offline-verifiable. This distinction is disclosed, not hidden.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, Sequence

import z3

from .money import fmt_money

# --------------------------------------------------------------------------------------
# Reason codes. Closed vocabulary — add here rather than inventing strings at call sites.
# --------------------------------------------------------------------------------------

REASON_AMOUNT_EXCEEDED = "AMOUNT_EXCEEDED"
REASON_CUMULATIVE_EXCEEDED = "CUMULATIVE_BUDGET_EXCEEDED"
REASON_VELOCITY_EXCEEDED = "VELOCITY_EXCEEDED"
REASON_MERCHANT_NOT_ALLOWED = "MERCHANT_NOT_ALLOWED"
REASON_MCC_NOT_ALLOWED = "MCC_NOT_ALLOWED"
REASON_CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
REASON_GEO_NOT_ALLOWED = "GEO_NOT_ALLOWED"
REASON_MANDATE_EXPIRED = "MANDATE_EXPIRED"
REASON_MANDATE_NOT_YET_VALID = "MANDATE_NOT_YET_VALID"
REASON_STEP_UP_REQUIRED = "STEP_UP_REQUIRED"


class Vars:
    """The shared decision variables every constraint is expressed over.

    A single instance is threaded through one entailment check so that both constraint
    sets talk about the same Z3 symbols.
    """

    def __init__(self) -> None:
        self.amount = z3.Int("amount")
        self.merchant = z3.Int("merchant")
        self.mcc = z3.Int("mcc")
        self.category = z3.Int("category")
        self.geo = z3.Int("geo")
        self.ts = z3.Int("ts")
        self.cumulative = z3.Int("cumulative")
        # Velocity is per-window: a child bounding a 1h window says nothing about a
        # parent's 24h window, so each window gets its own symbol. That makes
        # cross-window delegation fail closed, which is the conservative answer.
        self._velocity: dict[int, z3.ArithRef] = {}
        self.interner = Interner()

    def velocity(self, window_s: int) -> z3.ArithRef:
        if window_s not in self._velocity:
            self._velocity[window_s] = z3.Int(f"velocity_{window_s}")
        return self._velocity[window_s]


class Interner:
    """Deterministic string -> int mapping so set membership becomes integer equality.

    Both sides of an entailment check share one interner, so a merchant that appears in
    only one side still gets a distinct integer and Z3 can use it as a counterexample.
    """

    def __init__(self) -> None:
        self._to_int: dict[tuple[str, str], int] = {}
        self._to_str: dict[tuple[str, int], str] = {}
        self._next = 1

    def intern(self, domain: str, value: str) -> int:
        key = (domain, value)
        if key not in self._to_int:
            self._to_int[key] = self._next
            self._to_str[(domain, self._next)] = value
            self._next += 1
        return self._to_int[key]

    def decode(self, domain: str, value: int) -> str | None:
        """Map an integer back to its string, or None if Z3 invented a fresh value.

        A fresh value is meaningful: it means "some element outside every set either
        side named", which is exactly how a missing constraint gets exposed.
        """
        return self._to_str.get((domain, value))


# --------------------------------------------------------------------------------------
# Decision context — the concrete facts of one attempted authorization.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionContext:
    """Concrete facts at authorization time. Timestamps are explicit, never time.time()."""

    amount: int
    merchant: str
    mcc: int
    category: str
    geo: str
    ts: int
    cumulative: int = 0
    velocity: dict[int, int] | None = None

    def velocity_for(self, window_s: int) -> int:
        return (self.velocity or {}).get(window_s, 0)


@dataclass(frozen=True)
class Violation:
    reason: str
    detail: str
    constraint: dict[str, Any]


# --------------------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------------------


class Constraint:
    kind: ClassVar[str]
    stateful: ClassVar[bool] = False

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def assertion(self, v: Vars) -> z3.BoolRef:
        """The region of transactions this constraint authorizes."""
        raise NotImplementedError

    def check(self, ctx: DecisionContext) -> Violation | None:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.describe()}>"


@dataclass(frozen=True)
class AmountMax(Constraint):
    kind: ClassVar[str] = "amount_max"
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "value": self.value}

    def assertion(self, v: Vars) -> z3.BoolRef:
        return v.amount <= self.value

    def check(self, ctx: DecisionContext) -> Violation | None:
        if ctx.amount > self.value:
            return Violation(
                REASON_AMOUNT_EXCEEDED,
                f"amount {fmt_money(ctx.amount)} exceeds per-transaction cap {fmt_money(self.value)}",
                self.to_dict(),
            )
        return None

    def describe(self) -> str:
        return f"amount ≤ {fmt_money(self.value)}"


@dataclass(frozen=True)
class CumulativeMax(Constraint):
    """Stateful: needs the ledger. Not offline-verifiable — disclosed as such."""

    kind: ClassVar[str] = "cumulative_max"
    stateful: ClassVar[bool] = True
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "value": self.value}

    def assertion(self, v: Vars) -> z3.BoolRef:
        return v.cumulative <= self.value

    def check(self, ctx: DecisionContext) -> Violation | None:
        if ctx.cumulative > self.value:
            return Violation(
                REASON_CUMULATIVE_EXCEEDED,
                f"cumulative spend {fmt_money(ctx.cumulative)} exceeds budget {fmt_money(self.value)}",
                self.to_dict(),
            )
        return None

    def describe(self) -> str:
        return f"cumulative ≤ {fmt_money(self.value)}"


@dataclass(frozen=True)
class VelocityMax(Constraint):
    """Stateful: needs the ledger."""

    kind: ClassVar[str] = "velocity_max"
    stateful: ClassVar[bool] = True
    count: int
    window_s: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "count": self.count, "window_s": self.window_s}

    def assertion(self, v: Vars) -> z3.BoolRef:
        return v.velocity(self.window_s) <= self.count

    def check(self, ctx: DecisionContext) -> Violation | None:
        seen = ctx.velocity_for(self.window_s)
        if seen > self.count:
            return Violation(
                REASON_VELOCITY_EXCEEDED,
                f"{seen} transactions in {self.window_s}s exceeds limit of {self.count}",
                self.to_dict(),
            )
        return None

    def describe(self) -> str:
        return f"≤ {self.count} txn / {self.window_s}s"


class _SetAllow(Constraint):
    """Shared machinery for finite-set membership constraints."""

    domain: ClassVar[str]
    reason: ClassVar[str]
    label: ClassVar[str]

    def _values(self) -> Sequence[str]:
        raise NotImplementedError

    def _var(self, v: Vars) -> z3.ArithRef:
        raise NotImplementedError

    def _ctx_value(self, ctx: DecisionContext) -> str:
        raise NotImplementedError

    def assertion(self, v: Vars) -> z3.BoolRef:
        var = self._var(v)
        allowed = [var == v.interner.intern(self.domain, val) for val in self._values()]
        if not allowed:
            # An empty allowlist authorizes nothing. Fail closed.
            return z3.BoolVal(False)
        return z3.Or(*allowed) if len(allowed) > 1 else allowed[0]

    def check(self, ctx: DecisionContext) -> Violation | None:
        actual = self._ctx_value(ctx)
        if actual not in self._values():
            allowed = ", ".join(str(x) for x in self._values())
            return Violation(
                self.reason,
                f"{self.label} {actual!r} is not in the allowlist [{allowed}]",
                self.to_dict(),
            )
        return None

    def describe(self) -> str:
        return f"{self.label} ∈ {{{', '.join(str(x) for x in self._values())}}}"


@dataclass(frozen=True)
class MerchantAllow(_SetAllow):
    kind: ClassVar[str] = "merchant_allow"
    domain: ClassVar[str] = "merchant"
    reason: ClassVar[str] = REASON_MERCHANT_NOT_ALLOWED
    label: ClassVar[str] = "merchant"
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "values": list(self.values)}

    def _values(self) -> Sequence[str]:
        return self.values

    def _var(self, v: Vars) -> z3.ArithRef:
        return v.merchant

    def _ctx_value(self, ctx: DecisionContext) -> str:
        return ctx.merchant


@dataclass(frozen=True)
class MccAllow(_SetAllow):
    kind: ClassVar[str] = "mcc_allow"
    domain: ClassVar[str] = "mcc"
    reason: ClassVar[str] = REASON_MCC_NOT_ALLOWED
    label: ClassVar[str] = "MCC"
    values: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "values": list(self.values)}

    def _values(self) -> Sequence[str]:
        return [str(x) for x in self.values]

    def _var(self, v: Vars) -> z3.ArithRef:
        return v.mcc

    def _ctx_value(self, ctx: DecisionContext) -> str:
        return str(ctx.mcc)


@dataclass(frozen=True)
class CategoryAllow(_SetAllow):
    kind: ClassVar[str] = "category_allow"
    domain: ClassVar[str] = "category"
    reason: ClassVar[str] = REASON_CATEGORY_NOT_ALLOWED
    label: ClassVar[str] = "category"
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "values": list(self.values)}

    def _values(self) -> Sequence[str]:
        return self.values

    def _var(self, v: Vars) -> z3.ArithRef:
        return v.category

    def _ctx_value(self, ctx: DecisionContext) -> str:
        return ctx.category


@dataclass(frozen=True)
class GeoAllow(_SetAllow):
    kind: ClassVar[str] = "geo_allow"
    domain: ClassVar[str] = "geo"
    reason: ClassVar[str] = REASON_GEO_NOT_ALLOWED
    label: ClassVar[str] = "geo"
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "values": list(self.values)}

    def _values(self) -> Sequence[str]:
        return self.values

    def _var(self, v: Vars) -> z3.ArithRef:
        return v.geo

    def _ctx_value(self, ctx: DecisionContext) -> str:
        return ctx.geo


@dataclass(frozen=True)
class ExpiresAt(Constraint):
    kind: ClassVar[str] = "expires_at"
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "value": self.value}

    def assertion(self, v: Vars) -> z3.BoolRef:
        return v.ts <= self.value

    def check(self, ctx: DecisionContext) -> Violation | None:
        if ctx.ts > self.value:
            return Violation(
                REASON_MANDATE_EXPIRED,
                f"mandate expired at {self.value} (now {ctx.ts})",
                self.to_dict(),
            )
        return None

    def describe(self) -> str:
        return f"expires at {self.value}"


@dataclass(frozen=True)
class NotBefore(Constraint):
    kind: ClassVar[str] = "not_before"
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "value": self.value}

    def assertion(self, v: Vars) -> z3.BoolRef:
        return v.ts >= self.value

    def check(self, ctx: DecisionContext) -> Violation | None:
        if ctx.ts < self.value:
            return Violation(
                REASON_MANDATE_NOT_YET_VALID,
                f"mandate not valid before {self.value} (now {ctx.ts})",
                self.to_dict(),
            )
        return None

    def describe(self) -> str:
        return f"not before {self.value}"


@dataclass(frozen=True)
class StepUpOver(Constraint):
    """Transactions above `value` are not auto-authorized; they demand a step-up discharge.

    For entailment this contributes `amount <= value`, because the region a credential
    authorizes *without further human interaction* is exactly the region at or below the
    threshold. A child with a lower threshold is therefore strictly narrower, which is
    the semantics we want. At runtime the PDP treats a breach as STEP_UP, not DENY.
    """

    kind: ClassVar[str] = "step_up_over"
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "value": self.value}

    def assertion(self, v: Vars) -> z3.BoolRef:
        return v.amount <= self.value

    def check(self, ctx: DecisionContext) -> Violation | None:
        if ctx.amount > self.value:
            return Violation(
                REASON_STEP_UP_REQUIRED,
                f"amount {fmt_money(ctx.amount)} is over the step-up threshold {fmt_money(self.value)}",
                self.to_dict(),
            )
        return None

    def describe(self) -> str:
        return f"step-up over {fmt_money(self.value)}"


_REGISTRY: dict[str, Any] = {
    AmountMax.kind: lambda d: AmountMax(int(d["value"])),
    CumulativeMax.kind: lambda d: CumulativeMax(int(d["value"])),
    VelocityMax.kind: lambda d: VelocityMax(int(d["count"]), int(d["window_s"])),
    MerchantAllow.kind: lambda d: MerchantAllow(tuple(d["values"])),
    MccAllow.kind: lambda d: MccAllow(tuple(int(x) for x in d["values"])),
    CategoryAllow.kind: lambda d: CategoryAllow(tuple(d["values"])),
    GeoAllow.kind: lambda d: GeoAllow(tuple(d["values"])),
    ExpiresAt.kind: lambda d: ExpiresAt(int(d["value"])),
    NotBefore.kind: lambda d: NotBefore(int(d["value"])),
    StepUpOver.kind: lambda d: StepUpOver(int(d["value"])),
}


def constraint_from_dict(d: dict[str, Any]) -> Constraint:
    kind = d.get("type")
    if kind not in _REGISTRY:
        raise ValueError(f"unknown constraint type: {kind!r}")
    return _REGISTRY[kind](d)


class ConstraintSet:
    """An ordered, canonically serialisable bundle of constraints."""

    def __init__(self, constraints: Iterable[Constraint] = ()) -> None:
        self.constraints: list[Constraint] = list(constraints)

    # -- construction ------------------------------------------------------------------

    @classmethod
    def from_dicts(cls, items: Iterable[dict[str, Any]]) -> "ConstraintSet":
        return cls(constraint_from_dict(d) for d in items)

    @classmethod
    def from_json(cls, blob: str) -> "ConstraintSet":
        return cls.from_dicts(json.loads(blob))

    def to_dicts(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.constraints]

    def to_json(self) -> str:
        """Canonical form. Sorted keys and tight separators so bytes are stable."""
        return json.dumps(self.to_dicts(), sort_keys=True, separators=(",", ":"))

    def with_added(self, extra: Iterable[Constraint]) -> "ConstraintSet":
        return ConstraintSet([*self.constraints, *extra])

    # -- semantics ---------------------------------------------------------------------

    def assertions(self, v: Vars) -> list[z3.BoolRef]:
        return [c.assertion(v) for c in self.constraints]

    def region(self, v: Vars) -> z3.BoolRef:
        """The conjunction of every constraint: the set of authorized transactions."""
        parts = self.assertions(v)
        if not parts:
            # No constraints authorizes everything. Callers should never mint this.
            return z3.BoolVal(True)
        return z3.And(*parts) if len(parts) > 1 else parts[0]

    def violations(self, ctx: DecisionContext) -> list[Violation]:
        """Every violated constraint, structural and stateful alike."""
        out = []
        for c in self.constraints:
            v = c.check(ctx)
            if v is not None:
                out.append(v)
        return out

    def structural(self) -> "ConstraintSet":
        return ConstraintSet(c for c in self.constraints if not c.stateful)

    def stateful(self) -> "ConstraintSet":
        return ConstraintSet(c for c in self.constraints if c.stateful)

    def describe(self) -> list[str]:
        return [c.describe() for c in self.constraints]

    def __len__(self) -> int:
        return len(self.constraints)

    def __iter__(self):
        return iter(self.constraints)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ConstraintSet({self.describe()})"


# Re-exported so existing callers are unaffected. It lives in money.py now because this
# module imports z3, and importing it to render an amount put a solver in the valuation
# path — see caveat/money.py.
__all__ = [*globals().get("__all__", []), "fmt_money"]
