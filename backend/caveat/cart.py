"""Carts, and the intent-vs-execution diff that catches post-signature mutation.

The AP2 red-team literature's core finding is that payment parameters get mutated *after*
the human signs intent: the agent plans a ₹4,000 espresso machine, an injected instruction
appends ₹50,000 of gift cards, and the signature still validates because it covered the
intent, not the executed cart.

So the intent cart is hashed and bound into the credential at plan time, and the PDP
re-validates the *actual* cart against it at authorization time. A signature over stale
intent is not authorization.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from .money import fmt_money


@dataclass(frozen=True)
class CartLine:
    sku: str
    description: str
    amount: int  # integer minor units, total for the line
    mcc: int
    category: str
    qty: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "description": self.description,
            "amount": self.amount,
            "mcc": self.mcc,
            "category": self.category,
            "qty": self.qty,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CartLine":
        return cls(
            sku=str(d["sku"]),
            description=str(d.get("description", "")),
            amount=int(d["amount"]),
            mcc=int(d["mcc"]),
            category=str(d.get("category", "")),
            qty=int(d.get("qty", 1)),
        )

    def display(self) -> str:
        q = f"{self.qty} × " if self.qty != 1 else ""
        return f"{q}{self.description} — {fmt_money(self.amount)} (MCC {self.mcc})"


@dataclass(frozen=True)
class Cart:
    merchant: str
    currency: str
    lines: tuple[CartLine, ...]

    @classmethod
    def of(cls, merchant: str, lines: Iterable[CartLine], currency: str = "INR") -> "Cart":
        return cls(merchant=merchant, currency=currency, lines=tuple(lines))

    def total(self) -> int:
        return sum(line.amount for line in self.lines)

    def with_lines(self, lines: Iterable[CartLine]) -> "Cart":
        return replace(self, lines=tuple(lines))

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant": self.merchant,
            "currency": self.currency,
            "lines": [line.to_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cart":
        return cls(
            merchant=str(d["merchant"]),
            currency=str(d.get("currency", "INR")),
            lines=tuple(CartLine.from_dict(x) for x in d.get("lines", [])),
        )

    def hash(self) -> str:
        """Stable content hash. This is what gets bound into the step-up caveat."""
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def mccs(self) -> tuple[int, ...]:
        return tuple(sorted({line.mcc for line in self.lines}))

    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({line.category for line in self.lines}))


@dataclass(frozen=True)
class LineChange:
    before: CartLine
    after: CartLine

    def to_dict(self) -> dict[str, Any]:
        return {"before": self.before.to_dict(), "after": self.after.to_dict()}


@dataclass(frozen=True)
class CartDiff:
    """What changed between the signed intent and what actually arrived."""

    added: tuple[CartLine, ...] = ()
    removed: tuple[CartLine, ...] = ()
    modified: tuple[LineChange, ...] = ()
    merchant_changed: tuple[str, str] | None = None
    intent_total: int = 0
    executed_total: int = 0

    @property
    def delta(self) -> int:
        return self.executed_total - self.intent_total

    def diverged(self) -> bool:
        return bool(self.added or self.removed or self.modified or self.merchant_changed)

    def added_value(self) -> int:
        return sum(line.amount for line in self.added)

    def summary(self) -> str:
        if not self.diverged():
            return "cart matches signed intent"
        bits = []
        if self.merchant_changed:
            bits.append(f"merchant {self.merchant_changed[0]} → {self.merchant_changed[1]}")
        if self.added:
            bits.append(f"{len(self.added)} line(s) added worth {fmt_money(self.added_value())}")
        if self.removed:
            bits.append(f"{len(self.removed)} line(s) removed")
        if self.modified:
            bits.append(f"{len(self.modified)} line(s) modified")
        bits.append(f"total {fmt_money(self.intent_total)} → {fmt_money(self.executed_total)}")
        return "; ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "diverged": self.diverged(),
            "added": [line.to_dict() for line in self.added],
            "removed": [line.to_dict() for line in self.removed],
            "modified": [m.to_dict() for m in self.modified],
            "merchant_changed": list(self.merchant_changed) if self.merchant_changed else None,
            "intent_total": self.intent_total,
            "executed_total": self.executed_total,
            "delta": self.delta,
            "added_value": self.added_value(),
            "summary": self.summary(),
        }


def diff_carts(intent: Cart, executed: Cart) -> CartDiff:
    """Diff by SKU. Duplicate SKUs are folded so quantity changes surface as modifications."""
    intent_by_sku = _fold(intent.lines)
    executed_by_sku = _fold(executed.lines)

    added = tuple(
        executed_by_sku[sku] for sku in executed_by_sku if sku not in intent_by_sku
    )
    removed = tuple(intent_by_sku[sku] for sku in intent_by_sku if sku not in executed_by_sku)
    modified = tuple(
        LineChange(before=intent_by_sku[sku], after=executed_by_sku[sku])
        for sku in intent_by_sku
        if sku in executed_by_sku and intent_by_sku[sku] != executed_by_sku[sku]
    )
    merchant_changed = (
        (intent.merchant, executed.merchant) if intent.merchant != executed.merchant else None
    )
    return CartDiff(
        added=added,
        removed=removed,
        modified=modified,
        merchant_changed=merchant_changed,
        intent_total=intent.total(),
        executed_total=executed.total(),
    )


def _fold(lines: Sequence[CartLine]) -> dict[str, CartLine]:
    out: dict[str, CartLine] = {}
    for line in lines:
        if line.sku in out:
            prev = out[line.sku]
            out[line.sku] = replace(prev, amount=prev.amount + line.amount, qty=prev.qty + line.qty)
        else:
            out[line.sku] = line
    return out
