"""Allocation witnesses, and the linear-time verifier that makes them worth something.

This module carries the correctness argument for the whole system, so it is worth stating
plainly.

The question "what is this cart worth on this card" is a capacitated assignment problem:
cart lines are assigned to benefit buckets subject to remaining balances, annual caps, and
exclusivity groups. Naive per-line summation double-counts — two credits both claiming the
same dinner — and therefore *overstates*. Overstating is the one error that must never
happen, because an agent that acts on an inflated number produces a purchase the card
cannot actually back.

The obvious formal approach is to assert a value and ask a solver to prove no better
allocation is needed. Two problems, both found by benchmarking rather than by argument:

  1. It does not hold a checkout latency budget. Measured at 8 instruments x 20 lines x 40
     benefits: 451ms to 2695ms, six-fold variance at constant problem size, timeouts at 2s.
     Worse, on timeout the solver can report a lower bound above its upper bound, so an
     implementation that reads the bound signs an incoherent number. And there is no
     dynamic-programming fallback: capacitated assignment with exclusivity groups is a
     generalized assignment problem, which is NP-hard.

  2. The natural soundness query is trivially satisfiable. "No allocation realizes less
     than the asserted value" is always true of the empty allocation, which realizes zero.
     Written that way the check passes everything.

So the argument runs the other way, constructively:

    An asserted value is sound if we can EXHIBIT a concrete, valid allocation that
    realizes at least that much.

Since the exhibited allocation is achievable, the asserted value cannot exceed the true
optimum. Conservatism is proved by producing an allocation, not by an unsat proof. Three
consequences, all of them good:

  * The hot path is a deterministic allocator producing a witness. No solver, and a spread
    tight enough to budget: p99 within 1.10x of p50 at 8 instruments x 20 lines x 40
    benefits, measured in artifacts/plumbline_bench.json. Not "no variance" — the same
    input always yields the same allocation, but wall-clock still moves a little.
  * Verification is linear in the size of the witness and needs no solver at all — re-add
    the numbers and check the capacities. Any counterparty can verify independently.
  * A solver is still useful, but offline: it measures how far below optimal the witness
    sits. That gap is a number we quote rather than an optimality claim we make.

The argument holds only if the verifier is at least as strict as reality, so three of the
constraints below are load-bearing rather than defensive:

  * Consumption must equal realized value. Capacity is value-denominated, so a witness
    that declares value against a smaller draw would evade every cap in the manifest.
  * The statement credits attached to one line cannot together offset more than that line
    costs. Per-assignment bounds are not enough: two credits, each individually no larger
    than the dinner and with no declared exclusivity group, otherwise verify at twice the
    dinner. Exclusivity groups are a manifest-authoring convenience; this bound is
    structural and does not depend on an author remembering to declare one.
  * Cart SKUs address lines, so they must be unique. Otherwise one assignment names two
    different line amounts at once and the verifier silently picks whichever it indexed
    last, which can be the larger.

The scope of the guarantee, stated exactly, because a weaker claim honestly bounded is
worth more than a stronger one that a reviewer can puncture:

    The asserted value never exceeds the best value obtainable under the constraints the
    MANIFEST DECLARES.

The verifier enforces the whole of that vocabulary and nothing beyond it: per-benefit
capacity, per-(line, group) exclusivity, and the per-line credit offset bound. It follows
that a manifest which under-declares its own limits yields a sound witness for an unsound
set of facts. One case is worth naming rather than leaving for someone else to find:

  * A PROTECTION's value is bounded by its own `capacity_minor` and by nothing else. It is
    not bounded by the line it attaches to, nor by the cart total, and it is granted once
    per qualifying line. That is deliberate — a $100 Fine Hotels + Resorts property credit
    on a $40 room really is worth $100, because it is spent on property rather than
    against the room — but it means an UNCAPPED protection asserts `flat_minor` times the
    number of eligible lines, a figure with no upper bound in the cart at all. An uncapped
    earn is self-limiting at rate times spend; an uncapped protection is not self-limiting
    in any way. `authoring.py` raises ADVISORY_UNCAPPED_PROTECTION for exactly this, and
    every protection in the shipped catalogue sets `capacity_minor == flat_minor`.

Verification also binds: a witness names the manifest and the cart it was computed for,
and this module checks those names rather than assuming its caller passed the right pair.
`evaluate.py` has its own cart-hash gate, but the argument on the slide is that ANY
counterparty can run this function alone, so this function has to be safe alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from caveat.cart import Cart, CartLine
from caveat.money import fmt_currency

from .manifest import KIND_CREDIT, KIND_EARN, KIND_PROTECTION, Benefit, Manifest

# Verifier failure codes. Closed vocabulary.
ERR_UNKNOWN_BENEFIT = "WITNESS_UNKNOWN_BENEFIT"
ERR_UNKNOWN_LINE = "WITNESS_UNKNOWN_LINE"
ERR_INELIGIBLE = "WITNESS_INELIGIBLE_ASSIGNMENT"
ERR_CAPACITY = "WITNESS_CAPACITY_EXCEEDED"
ERR_EXCLUSIVITY = "WITNESS_EXCLUSIVITY_VIOLATED"
ERR_VALUE_MISMATCH = "WITNESS_VALUE_MISMATCH"
ERR_OVERSTATED = "WITNESS_DOES_NOT_SUPPORT_ASSERTION"
ERR_UNAVAILABLE = "WITNESS_BENEFIT_UNAVAILABLE"
ERR_DOUBLE_ASSIGNED = "WITNESS_LINE_DOUBLE_ASSIGNED_SAME_BENEFIT"
ERR_CONSUMPTION_MISMATCH = "WITNESS_CONSUMPTION_MISMATCH"
ERR_CREDIT_OVER_LINE = "WITNESS_CREDIT_EXCEEDS_LINE_AMOUNT"
ERR_LINE_OVER_OFFSET = "WITNESS_LINE_OFFSET_EXCEEDS_LINE_AMOUNT"
ERR_NEGATIVE_AMOUNT = "WITNESS_NEGATIVE_AMOUNT"
ERR_UNPRICED = "WITNESS_UNPRICED_BENEFIT_ASSIGNED"
ERR_DUPLICATE_SKU = "WITNESS_CART_DUPLICATE_SKU"
ERR_MANIFEST_MISMATCH = "WITNESS_MANIFEST_MISMATCH"
ERR_CART_MISMATCH = "WITNESS_CART_MISMATCH"
ERR_CURRENCY_MISMATCH = "WITNESS_CURRENCY_MISMATCH"

FAILURE_CODES = (
    ERR_UNKNOWN_BENEFIT,
    ERR_UNKNOWN_LINE,
    ERR_INELIGIBLE,
    ERR_CAPACITY,
    ERR_EXCLUSIVITY,
    ERR_VALUE_MISMATCH,
    ERR_OVERSTATED,
    ERR_UNAVAILABLE,
    ERR_DOUBLE_ASSIGNED,
    ERR_CONSUMPTION_MISMATCH,
    ERR_CREDIT_OVER_LINE,
    ERR_LINE_OVER_OFFSET,
    ERR_NEGATIVE_AMOUNT,
    ERR_UNPRICED,
    ERR_DUPLICATE_SKU,
    ERR_MANIFEST_MISMATCH,
    ERR_CART_MISMATCH,
    ERR_CURRENCY_MISMATCH,
)


@dataclass(frozen=True)
class Assignment:
    """One benefit attaching to one cart line, and the value that yields."""

    line_sku: str
    benefit_id: str
    # How much of the benefit's capacity this assignment consumes.
    consumed_minor: int
    # The value the Card Member actually realizes from it.
    value_minor: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_sku": self.line_sku,
            "benefit_id": self.benefit_id,
            "consumed_minor": self.consumed_minor,
            "value_minor": self.value_minor,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Assignment":
        return cls(
            line_sku=str(d["line_sku"]),
            benefit_id=str(d["benefit_id"]),
            consumed_minor=int(d["consumed_minor"]),
            value_minor=int(d["value_minor"]),
        )


@dataclass(frozen=True)
class Witness:
    """A concrete allocation. This IS the line-item derivation shown to a human."""

    manifest_id: str
    cart_hash: str
    assignments: tuple[Assignment, ...]

    def realized_minor(self) -> int:
        return sum(a.value_minor for a in self.assignments)

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only, deliberately.

        A witness carries no currency of its own — it names a manifest and a cart, and both
        of those know. Defaulting the symbol here is how a USD manifest comes out in rupees:
        every number is right, every sign is wrong, and nothing raises. So the caller, who
        is holding the manifest or the cart, has to say.
        """
        return {
            "manifest_id": self.manifest_id,
            "cart_hash": self.cart_hash,
            "assignments": [a.to_dict() for a in self.assignments],
            "realized_minor": self.realized_minor(),
            "realized_display": fmt_currency(self.realized_minor(), currency),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Witness":
        return cls(
            manifest_id=str(d["manifest_id"]),
            cart_hash=str(d["cart_hash"]),
            assignments=tuple(Assignment.from_dict(a) for a in d.get("assignments", [])),
        )

    def derivation(self, manifest: Manifest, cart: Cart) -> list[str]:
        """Human-readable derivation lines, in allocation order.

        The currency comes off the cart rather than from a parameter: `verify_witness`
        already rejects a manifest and cart that disagree on it, so there is exactly one
        right answer here and no reason to let a caller supply a different one. A derivation
        shown to a Card Member under the wrong currency sign is one they are right not to
        trust.
        """
        by_id = {b.benefit_id: b for b in manifest.benefits}
        by_sku = {line.sku: line for line in cart.lines}
        out = []
        for a in self.assignments:
            benefit = by_id.get(a.benefit_id)
            line = by_sku.get(a.line_sku)
            label = benefit.label if benefit else a.benefit_id
            desc = line.description if line else a.line_sku
            out.append(f"{label} on {desc}: {fmt_currency(a.value_minor, cart.currency)}")
        return out


@dataclass(frozen=True)
class Failure:
    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class Verification:
    """Result of independently checking a witness. No solver involved.

    `claimed_minor` is what the witness said. `realized_minor` is what the verifier will
    stand behind: the total over the assignments that survived every check, with any
    benefit that overran its capacity and any credit on an over-offset line dropped
    entirely. What remains is itself a valid allocation, so `realized_minor` is always
    achievable — a sound lower bound, never an upper one.

    On a clean verification the two are equal. On a failed one the difference localises the
    overstatement, which is more useful than reporting zero and is safe precisely because
    the number can only ever be too low. `ok` is False either way and `supports_assertion`
    is gated on `ok`, so a failed verification supports nothing whatever number it carries.
    """

    ok: bool
    realized_minor: int
    asserted_minor: int
    failures: tuple[Failure, ...] = ()
    claimed_minor: int = 0

    @property
    def supports_assertion(self) -> bool:
        return self.ok and self.realized_minor >= self.asserted_minor

    def codes(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.failures)

    def to_dict(self, *, currency: str) -> dict[str, Any]:
        """Serialize. `currency` is required and keyword-only — see `Witness.to_dict`."""
        return {
            "ok": self.ok,
            "supports_assertion": self.supports_assertion,
            "realized_minor": self.realized_minor,
            "realized_display": fmt_currency(self.realized_minor, currency),
            "claimed_minor": self.claimed_minor,
            "claimed_display": fmt_currency(self.claimed_minor, currency),
            "asserted_minor": self.asserted_minor,
            "asserted_display": fmt_currency(self.asserted_minor, currency),
            "failures": [f.to_dict() for f in self.failures],
        }


def verify_witness(
    *,
    witness: Witness,
    manifest: Manifest,
    cart: Cart,
    asserted_minor: int,
) -> Verification:
    """Check a witness against the manifest and cart it claims to describe.

    Linear in the number of assignments. Deliberately needs no solver: a counterparty
    verifies an issuer's claim with arithmetic, not by trusting the issuer's tooling.

    Checks, in order:
      * the witness names this manifest and this cart, and the manifest is denominated in
        the cart's currency;
      * cart SKUs are unique, so every assignment names exactly one line;
      * every referenced benefit and line exists, and the benefit is available;
      * no benefit attaches to the same line twice;
      * every assignment is eligible under the benefit's own predicate;
      * no two benefits from one exclusivity group attach to the same line;
      * each assignment's claimed value matches what the manifest says it yields, and its
        declared capacity draw matches that value;
      * no benefit consumes more than its declared capacity;
      * the credits on a line do not together offset more than the line costs;
      * the total realized value is at least the asserted value.

    A witness is untrusted data, so this never raises. Every rejection is a typed code.
    """
    failures: list[Failure] = []
    claimed = witness.realized_minor()

    binding = _check_binding(witness, manifest, cart)
    if binding is not None:
        # Bail rather than accumulate. Every downstream number is computed against the
        # manifest and cart passed in, so once the witness is known to describe a
        # different pair, an arithmetic verdict about it would be an answer to a question
        # nobody asked.
        return Verification(
            ok=False,
            realized_minor=0,
            asserted_minor=asserted_minor,
            claimed_minor=claimed,
            failures=(binding,),
        )

    # Safe only below `_check_binding`, which has already refused a manifest and cart that
    # disagree on it. Every failure detail below quotes an amount, and a detail quoting the
    # wrong currency sign is a diagnosis a counterparty is right to distrust.
    currency = manifest.currency

    by_id = {b.benefit_id: b for b in manifest.benefits}
    by_sku: dict[str, CartLine] = {}
    for line in cart.lines:
        if line.sku in by_sku:
            # Bail rather than accumulate: with an ambiguous SKU every downstream number
            # is computed against an arbitrarily chosen line.
            return Verification(
                ok=False,
                realized_minor=0,
                asserted_minor=asserted_minor,
                claimed_minor=claimed,
                failures=(
                    Failure(
                        ERR_DUPLICATE_SKU,
                        f"cart names SKU {line.sku!r} more than once, so an assignment to "
                        f"it is ambiguous; fold duplicate SKUs into a single line before "
                        f"valuing the cart",
                    ),
                ),
            )
        by_sku[line.sku] = line

    group_claims: dict[tuple[str, str], str] = {}  # (sku, group) -> benefit_id
    seen_pairs: set[tuple[str, str]] = set()
    surviving: list[tuple[Assignment, Benefit]] = []

    for a in witness.assignments:
        benefit = by_id.get(a.benefit_id)
        line = by_sku.get(a.line_sku)

        if benefit is None:
            failures.append(Failure(ERR_UNKNOWN_BENEFIT, f"no benefit {a.benefit_id!r} in manifest"))
            continue
        if line is None:
            failures.append(Failure(ERR_UNKNOWN_LINE, f"no line {a.line_sku!r} in cart"))
            continue
        if not benefit.available():
            failures.append(
                Failure(ERR_UNAVAILABLE, f"{benefit.benefit_id} is unenrolled or exhausted")
            )
            continue

        pair = (a.line_sku, a.benefit_id)
        if pair in seen_pairs:
            failures.append(
                Failure(ERR_DOUBLE_ASSIGNED, f"{a.benefit_id} attached twice to {a.line_sku}")
            )
            continue
        seen_pairs.add(pair)

        if not benefit.eligibility.admits(line, cart.merchant):
            failures.append(
                Failure(
                    ERR_INELIGIBLE,
                    f"{benefit.benefit_id} does not admit {line.sku} "
                    f"(mcc {line.mcc}, category {line.category!r}, merchant {cart.merchant!r})",
                )
            )
            continue

        # Exclusivity: two credits may not both claim the same dinner.
        if benefit.exclusivity_group:
            key = (a.line_sku, benefit.exclusivity_group)
            holder = group_claims.get(key)
            if holder is not None and holder != a.benefit_id:
                failures.append(
                    Failure(
                        ERR_EXCLUSIVITY,
                        f"{holder} and {a.benefit_id} both claim {a.line_sku} "
                        f"in group {benefit.exclusivity_group!r}",
                    )
                )
                continue
            group_claims[key] = a.benefit_id

        effect_failure = _check_effect(benefit, line, a, currency)
        if effect_failure is not None:
            failures.append(effect_failure)
            continue

        surviving.append((a, benefit))

    over_capacity = _over_capacity(surviving)
    for benefit_id, (used, cap) in sorted(over_capacity.items()):
        failures.append(
            Failure(
                ERR_CAPACITY,
                f"{benefit_id} consumed {fmt_currency(used, currency)} of "
                f"{fmt_currency(cap, currency)} available",
            )
        )

    over_offset = _over_offset(surviving, by_sku)
    for sku, (offset, amount) in sorted(over_offset.items()):
        failures.append(
            Failure(
                ERR_LINE_OVER_OFFSET,
                f"statement credits offset {fmt_currency(offset, currency)} against "
                f"{sku}, a line costing {fmt_currency(amount, currency)}; a credit cannot "
                f"offset spend that is not on the line",
            )
        )

    realized = _validated_total(surviving, set(over_capacity), set(over_offset))

    if not failures and realized < asserted_minor:
        failures.append(
            Failure(
                ERR_OVERSTATED,
                f"witness realizes {fmt_currency(realized, currency)}, assertion claims "
                f"{fmt_currency(asserted_minor, currency)}",
            )
        )

    return Verification(
        ok=not failures,
        realized_minor=realized,
        asserted_minor=asserted_minor,
        failures=tuple(failures),
        claimed_minor=claimed,
    )


def _check_binding(witness: Witness, manifest: Manifest, cart: Cart) -> Failure | None:
    """Confirm the witness describes the manifest and cart it was handed alongside.

    Without this the verifier answers "are these numbers right for the cart you gave me",
    when the question a counterparty is actually asking is "does this witness support this
    receipt's assertion about this cart". Those differ whenever the two inputs disagree,
    and the arithmetic alone cannot tell them apart: a witness computed against a smaller
    cart re-checks clean against a larger one and simply understates.

    The currency check belongs here for a blunter reason. Every amount in this module is an
    integer minor unit with no unit attached, so a manifest denominated in one currency and
    a cart in another are added together as if a cent were a paisa. There is no exchange
    rate anywhere in the decision path and there must not be one — an FX rate is a market
    price, not an issuer-signed fact.
    """
    if witness.manifest_id != manifest.manifest_id:
        return Failure(
            ERR_MANIFEST_MISMATCH,
            f"witness was computed against manifest {witness.manifest_id!r} but is being "
            f"checked against {manifest.manifest_id!r}; verify it against the manifest it "
            f"names, or re-run the allocator against this one",
        )
    if witness.cart_hash != cart.hash():
        return Failure(
            ERR_CART_MISMATCH,
            f"witness binds cart {witness.cart_hash[:16]}… but is being checked against "
            f"cart {cart.hash()[:16]}…; a witness proves a value for the cart it was "
            f"computed from and says nothing about any other",
        )
    if manifest.currency != cart.currency:
        return Failure(
            ERR_CURRENCY_MISMATCH,
            f"manifest is denominated in {manifest.currency} and the cart in "
            f"{cart.currency}; minor units carry no unit, so these amounts cannot be "
            f"compared or added — supply a manifest in the cart's currency",
        )
    return None


def _over_capacity(
    surviving: Sequence[tuple[Assignment, Benefit]],
) -> dict[str, tuple[int, int]]:
    """benefit_id -> (consumed, capacity), for benefits that overran their capacity."""
    used: dict[str, int] = {}
    caps: dict[str, int] = {}
    for a, benefit in surviving:
        used[a.benefit_id] = used.get(a.benefit_id, 0) + a.consumed_minor
        if benefit.capacity_minor is not None:
            caps[a.benefit_id] = benefit.capacity_minor
    return {bid: (used[bid], cap) for bid, cap in caps.items() if used[bid] > cap}


def _over_offset(
    surviving: Sequence[tuple[Assignment, Benefit]],
    by_sku: Mapping[str, CartLine],
) -> dict[str, tuple[int, int]]:
    """line_sku -> (offset, line amount), for lines the credits together over-offset.

    Only credits count against this bound. An earn multiplier is a rebate on spend, not an
    offset of it, and two multipliers stacking on a line — a base rate plus a category
    bonus — is ordinary. A protection pays out against a loss, not against the line.
    """
    offset: dict[str, int] = {}
    for a, benefit in surviving:
        if benefit.kind != KIND_CREDIT:
            continue
        offset[a.line_sku] = offset.get(a.line_sku, 0) + a.value_minor
    return {
        sku: (total, by_sku[sku].amount)
        for sku, total in offset.items()
        if total > by_sku[sku].amount
    }


def _validated_total(
    surviving: Sequence[tuple[Assignment, Benefit]],
    over_capacity: set[str],
    over_offset: set[str],
) -> int:
    """The total the verifier will stand behind: a sound lower bound, never an upper one.

    Assignments that failed a per-assignment check are already absent. Dropping every
    assignment of an over-capacity benefit, and every credit on an over-offset line, leaves
    a set satisfying all the same constraints — removing an assignment cannot create a
    violation — so what remains is an achievable allocation and its total is achievable.

    On a clean verification nothing is dropped and this equals the witness's own total,
    which is why there is one code path rather than a success case and a failure case.
    """
    total = 0
    for a, benefit in surviving:
        if a.benefit_id in over_capacity:
            continue
        if benefit.kind == KIND_CREDIT and a.line_sku in over_offset:
            continue
        total += a.value_minor
    return total


def _check_effect(
    benefit: Benefit, line: CartLine, a: Assignment, currency: str
) -> Failure | None:
    """Check one assignment's declared value AND its declared capacity draw.

    Checking value alone is not enough, and the reason is the whole point of the capacity
    model: the cap test downstream sums `consumed_minor`, so a witness that declares a
    smaller draw than the benefit actually takes slips past a cap it has already blown.
    An earn benefit with ₹1 of annual headroom left, declaring `consumed_minor = 0` on two
    ₹1,000 lines, would otherwise verify at ₹100 of realized value.

    Per kind:
      credit      value IS the spend offset, so value == consumed, and neither may exceed
                  the line — a credit cannot offset spend that is not on the line.
      earn        value is the rate applied to the line, and the same figure is what the
                  annual cap gives up.
      protection  value and draw are both the declared flat amount.
    """
    if a.consumed_minor < 0 or a.value_minor < 0 or line.amount < 0:
        return Failure(
            ERR_NEGATIVE_AMOUNT,
            f"{a.benefit_id} on {a.line_sku} declares negative amounts "
            f"(consumed {a.consumed_minor}, value {a.value_minor}, line {line.amount})",
        )

    if benefit.kind == KIND_CREDIT:
        # The per-line aggregate bound downstream would also catch a single credit larger
        # than its line. This check runs first so that case gets its own code rather than
        # being reported as a collective over-offset, which reads as the wrong diagnosis.
        if a.consumed_minor > line.amount:
            return Failure(
                ERR_CREDIT_OVER_LINE,
                f"{a.benefit_id} offsets {fmt_currency(a.consumed_minor, currency)} "
                f"against {a.line_sku}, a line of only "
                f"{fmt_currency(line.amount, currency)}",
            )
        if a.value_minor != a.consumed_minor:
            return Failure(
                ERR_VALUE_MISMATCH,
                f"{a.benefit_id} on {a.line_sku} claims "
                f"{fmt_currency(a.value_minor, currency)} while offsetting "
                f"{fmt_currency(a.consumed_minor, currency)}; a credit yields exactly "
                f"what it offsets",
            )
        return None

    if benefit.kind == KIND_EARN:
        expected = (line.amount * benefit.rate_bp) // 10_000
    elif benefit.kind == KIND_PROTECTION:
        expected = benefit.flat_minor
    else:
        return Failure(
            ERR_UNPRICED,
            f"{a.benefit_id} is kind {benefit.kind!r} and yields no priced value",
        )

    if a.value_minor != expected:
        return Failure(
            ERR_VALUE_MISMATCH,
            f"{a.benefit_id} on {a.line_sku} claims "
            f"{fmt_currency(a.value_minor, currency)}, manifest yields "
            f"{fmt_currency(expected, currency)}",
        )
    if a.consumed_minor != expected:
        return Failure(
            ERR_CONSUMPTION_MISMATCH,
            f"{a.benefit_id} on {a.line_sku} yields {fmt_currency(expected, currency)} "
            f"but declares a draw of {fmt_currency(a.consumed_minor, currency)} against "
            f"its cap",
        )
    return None
