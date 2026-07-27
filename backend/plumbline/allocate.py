"""The hot path: a deterministic allocator that produces a witness.

This runs at checkout, so it is greedy rather than optimal, and that trade is deliberate.
See witness.py for the full argument; the short version is that the value asserted is
whatever a concrete exhibited allocation realizes, so a suboptimal allocation understates
and is therefore safe, while a solver on the hot path is both slow and — on timeout —
capable of reporting an incoherent bound.

Determinism matters as much as speed. The same cart and manifest must always produce the
same witness, byte for byte, because the witness is hashed into a signed receipt. Every
ordering here is total: candidates sort by value, then by benefit id, then by line sku,
so ties never resolve by dictionary iteration order.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from caveat.cart import Cart, CartLine

from .manifest import KIND_CREDIT, KIND_EARN, KIND_PROTECTION, Benefit, Manifest
from .witness import Assignment, Witness


@dataclass(frozen=True)
class Candidate:
    """One possible (line, benefit) pairing, before capacity is applied."""

    line: CartLine
    benefit: Benefit
    value_minor: int
    consumed_minor: int

    def sort_key(self) -> tuple[int, str, str]:
        # Descending value via negation; then a total order on identifiers so the result
        # is reproducible regardless of input ordering.
        return (-self.value_minor, self.benefit.benefit_id, self.line.sku)


class AllocationError(ValueError):
    """Raised when a cart cannot be valued as posed. Never raised for low value."""


@dataclass(frozen=True)
class AllocationResult:
    """The witness, plus what the allocator had to give up to produce it.

    The counters are what make the overstatement demonstrable rather than asserted: the
    naive sum exceeds the witness by exactly the value of what these count. `elapsed_ms` is
    a measurement and is deliberately the only non-deterministic field — the witness, which
    is what gets hashed into a receipt, does not contain it.
    """

    witness: Witness
    elapsed_ms: float
    considered: int
    assigned: int
    skipped_capacity: int
    skipped_exclusivity: int
    skipped_offset: int = 0
    clipped_credits: int = 0

    def binding(self) -> bool:
        """Whether any constraint actually bound. If none did, naive summation was right."""
        return bool(
            self.skipped_capacity
            or self.skipped_exclusivity
            or self.skipped_offset
            or self.clipped_credits
        )

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only — see `Witness.to_dict`."""
        return {
            "witness": self.witness.to_dict(currency=currency),
            "elapsed_ms": round(self.elapsed_ms, 4),
            "considered": self.considered,
            "assigned": self.assigned,
            "skipped_capacity": self.skipped_capacity,
            "skipped_exclusivity": self.skipped_exclusivity,
            "skipped_offset": self.skipped_offset,
            "clipped_credits": self.clipped_credits,
            "binding": self.binding(),
        }


def allocate(manifest: Manifest, cart: Cart) -> AllocationResult:
    """Greedily assign cart lines to benefits, respecting capacity and exclusivity."""
    started = time.perf_counter()
    _require_addressable_cart(cart)

    candidates = _candidates(manifest, cart)
    candidates.sort(key=Candidate.sort_key)

    remaining: dict[str, int | None] = {
        b.benefit_id: b.capacity_minor for b in manifest.benefits
    }
    # A statement credit offsets spend, so the credits on one line cannot together offset
    # more than the line costs. Exclusivity groups express the same idea when an author
    # declares them; this bound holds whether or not one did.
    offset_headroom: dict[str, int] = {line.sku: line.amount for line in cart.lines}
    claimed_groups: set[tuple[str, str]] = set()
    used_pairs: set[tuple[str, str]] = set()

    assignments: list[Assignment] = []
    skipped_capacity = 0
    skipped_exclusivity = 0
    skipped_offset = 0
    clipped_credits = 0

    for cand in candidates:
        benefit = cand.benefit
        pair = (cand.line.sku, benefit.benefit_id)
        if pair in used_pairs:
            continue

        if benefit.exclusivity_group:
            key = (cand.line.sku, benefit.exclusivity_group)
            if key in claimed_groups:
                skipped_exclusivity += 1
                continue

        cap = remaining.get(benefit.benefit_id)
        if cap is not None and cap <= 0:
            skipped_capacity += 1
            continue

        if benefit.kind == KIND_CREDIT:
            # A credit is bounded three times: by the line it offsets, by the balance still
            # on it, and by the spend on that line no earlier credit has already claimed.
            # Every clamp must land on `value` as well as `consumed`, because a credit's
            # value IS the spend it offsets and the verifier recomputes it from the draw.
            # Clamping only the draw asserts the whole line against a balance that cannot
            # cover it — an overstatement, and one its own verifier rejects.
            headroom = offset_headroom[cand.line.sku]
            if headroom <= 0:
                skipped_offset += 1
                continue
            consumed = min(headroom, cand.line.amount)
            if cap is not None:
                consumed = min(consumed, cap)
            value = consumed
            if value < cand.line.amount:
                clipped_credits += 1
        else:
            # Earn and protection are all-or-nothing: a fractional multiplier is not a
            # thing the manifest declared, so a benefit with less headroom than the line
            # needs is skipped rather than prorated.
            consumed = cand.consumed_minor
            value = cand.value_minor
            if cap is not None and consumed > cap:
                skipped_capacity += 1
                continue

        if value <= 0:
            continue

        # Capacity is drawn down only once the assignment is certain to be recorded;
        # decrementing before the final guards would burn balance on nothing.
        if cap is not None:
            remaining[benefit.benefit_id] = cap - consumed
        if benefit.kind == KIND_CREDIT:
            offset_headroom[cand.line.sku] -= value

        used_pairs.add(pair)
        if benefit.exclusivity_group:
            claimed_groups.add((cand.line.sku, benefit.exclusivity_group))

        assignments.append(
            Assignment(
                line_sku=cand.line.sku,
                benefit_id=benefit.benefit_id,
                consumed_minor=consumed,
                value_minor=value,
            )
        )

    # Stable output ordering, independent of the greedy processing order.
    assignments.sort(key=lambda a: (a.line_sku, a.benefit_id))

    witness = Witness(
        manifest_id=manifest.manifest_id,
        cart_hash=cart.hash(),
        assignments=tuple(assignments),
    )
    return AllocationResult(
        witness=witness,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        considered=len(candidates),
        assigned=len(assignments),
        skipped_capacity=skipped_capacity,
        skipped_exclusivity=skipped_exclusivity,
        skipped_offset=skipped_offset,
        clipped_credits=clipped_credits,
    )


def _require_addressable_cart(cart: Cart) -> None:
    """A witness addresses lines by SKU, so SKUs must be unique and amounts non-negative.

    Duplicate SKUs are the dangerous case: the verifier indexes lines by SKU and would
    check an assignment against whichever line it happened to keep, which can be the larger
    one. That turns a valid witness into an unverifiable one, or worse, an overstated one
    that verifies.
    """
    seen: set[str] = set()
    for line in cart.lines:
        if line.sku in seen:
            raise AllocationError(
                f"cart names SKU {line.sku!r} more than once; a witness addresses lines by "
                f"SKU, so fold duplicates into one line (caveat.cart.diff_carts already "
                f"folds this way) or give each line a distinct SKU"
            )
        seen.add(line.sku)
        if line.amount < 0:
            raise AllocationError(
                f"line {line.sku!r} has amount {line.amount}; valuation is defined over "
                f"non-negative spend, so post a refund as a separate settlement rather "
                f"than a negative cart line"
            )


def candidates(manifest: Manifest, cart: Cart) -> list[Candidate]:
    """Every (line, benefit) pairing worth considering, before capacity is applied.

    Public because the offline oracle builds its solver model from exactly this list. The
    oracle's claim to be measuring the gap on *this* problem rests on sharing the
    enumeration and the per-candidate value with the hot path rather than restating them.
    """
    return _candidates(manifest, cart)


def _candidates(manifest: Manifest, cart: Cart) -> list[Candidate]:
    out: list[Candidate] = []
    for benefit in manifest.priced():
        for line in cart.lines:
            if not benefit.eligibility.admits(line, cart.merchant):
                continue
            value = benefit.value_for_line(line, cart.merchant)
            if value <= 0:
                continue
            consumed = _consumption(benefit, line, value)
            if benefit.kind == KIND_CREDIT:
                # A credit yields exactly the spend it offsets. `value_for_line` returns the
                # uncapped line amount — deliberately, so `naive_sum` can show the overstated
                # figure — so a credit whose remaining balance is below the line amount must
                # be clamped here. Without this the witness claims the whole line while
                # consuming only the balance, which is precisely the overstatement the
                # verifier exists to reject.
                value = consumed
            if value <= 0:
                continue
            out.append(
                Candidate(
                    line=line, benefit=benefit, value_minor=value, consumed_minor=consumed
                )
            )
    return out


def _consumption(benefit: Benefit, line: CartLine, value: int) -> int:
    """How much of a benefit's capacity an assignment draws down.

    A credit consumes exactly the spend it offsets. An earn benefit consumes the value it
    yields, against its annual cap. A protection consumes its own flat value.
    """
    if benefit.kind == KIND_CREDIT:
        if benefit.capacity_minor is None:
            return line.amount
        return min(line.amount, benefit.capacity_minor)
    if benefit.kind == KIND_EARN:
        return value
    if benefit.kind == KIND_PROTECTION:
        return benefit.flat_minor
    return value


def naive_sum(manifest: Manifest, cart: Cart) -> int:
    """The intuitive answer, kept so the demo can show it being wrong.

    Sums every eligible benefit against every line with no capacity and no exclusivity.
    This is what a per-line valuation produces, and it OVERSTATES — two credits both
    claiming the same dinner, multipliers ignoring their annual caps. An agent acting on
    this number produces a purchase the card cannot actually back.
    """
    total = 0
    for benefit in manifest.priced():
        for line in cart.lines:
            total += benefit.value_for_line(line, cart.merchant)
    return total
