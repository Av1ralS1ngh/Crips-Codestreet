"""Regression: the allocator must never emit a witness its own verifier rejects.

The allocator and the verifier are two independent implementations of the same rules, on
purpose — the verifier is what a counterparty runs, and it must be able to reject an
issuer's own tooling. So the strongest property available is that every witness the hot
path produces verifies, and that the value it asserts never exceeds the naive sum.

The specific defect these tests pin: a statement credit whose remaining balance is below
the cart line it attaches to. `Benefit.value_for_line` returns the uncapped line amount so
that `naive_sum` can display the overstated figure; the allocator has to clamp it to the
balance actually consumed. Before the fix it did not, so the witness claimed the whole
line while drawing down only the balance — an overstatement, which is the one error the
system exists to prevent.
"""

from __future__ import annotations

import random

import pytest

from caveat.cart import Cart, CartLine
from plumbline.allocate import allocate, naive_sum
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    Benefit,
    Eligibility,
    build_manifest,
)
from plumbline.witness import verify_witness

T0 = 1_753_600_000


def _cart(*lines: CartLine, merchant: str = "m_taj") -> Cart:
    return Cart(merchant=merchant, currency="INR", lines=tuple(lines))


def _line(sku: str, amount: int, mcc: int, category: str) -> CartLine:
    return CartLine(sku=sku, description=sku, amount=amount, mcc=mcc, category=category, qty=1)


def _manifest(*benefits: Benefit):
    return build_manifest(
        manifest_id="mf_test",
        issuer="test_issuer",
        product="test_product",
        benefits=benefits,
        issued_at=T0,
    )


def _credit(bid: str, capacity: int, mcc: int, group: str | None = None) -> Benefit:
    return Benefit(
        benefit_id=bid,
        kind=KIND_CREDIT,
        label=bid,
        eligibility=Eligibility(mccs=(mcc,)),
        capacity_minor=capacity,
        exclusivity_group=group,
    )


def test_credit_smaller_than_line_verifies() -> None:
    """The defect case: ₹1,000 of balance against a ₹4,000 dinner."""
    cart = _cart(_line("sku_dinner", 400_000, 5812, "dining"))
    manifest = _manifest(_credit("ben_dining", 100_000, 5812))

    result = allocate(manifest, cart)
    assignment = result.witness.assignments[0]

    assert assignment.value_minor == 100_000
    assert assignment.consumed_minor == 100_000

    verification = verify_witness(
        witness=result.witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=result.witness.realized_minor(),
    )
    assert verification.ok, [f.to_dict() for f in verification.failures]
    assert verification.supports_assertion


def test_credit_exhausts_across_lines() -> None:
    """One balance, two eligible lines: the second line gets only what is left."""
    cart = _cart(
        _line("sku_a", 300_000, 5812, "dining"),
        _line("sku_b", 300_000, 5812, "dining"),
    )
    manifest = _manifest(_credit("ben_dining", 400_000, 5812))

    result = allocate(manifest, cart)
    assert result.witness.realized_minor() == 400_000

    verification = verify_witness(
        witness=result.witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=400_000,
    )
    assert verification.ok, [f.to_dict() for f in verification.failures]


def test_exclusivity_group_admits_one_credit_per_line() -> None:
    cart = _cart(_line("sku_dinner", 500_000, 5812, "dining"))
    manifest = _manifest(
        _credit("ben_dining_a", 200_000, 5812, group="dining"),
        _credit("ben_dining_b", 150_000, 5812, group="dining"),
    )

    result = allocate(manifest, cart)
    assert len(result.witness.assignments) == 1
    assert result.skipped_exclusivity == 1
    # Naive summation adds both credits to the same dinner and overstates by the loser.
    assert naive_sum(manifest, cart) > result.witness.realized_minor()


def test_witness_never_exceeds_naive_sum() -> None:
    cart = _cart(
        _line("sku_dinner", 500_000, 5812, "dining"),
        _line("sku_hotel", 1_800_000, 7011, "travel"),
    )
    manifest = _manifest(
        _credit("ben_dining", 200_000, 5812, group="dining"),
        _credit("ben_dining_alt", 150_000, 5812, group="dining"),
        Benefit(
            benefit_id="ben_earn",
            kind=KIND_EARN,
            label="travel earn",
            eligibility=Eligibility(mccs=(7011,)),
            rate_bp=500,
            capacity_minor=50_000,
        ),
        Benefit(
            benefit_id="ben_cover",
            kind=KIND_PROTECTION,
            label="trip cover",
            eligibility=Eligibility(mccs=(7011,)),
            flat_minor=40_000,
            capacity_minor=40_000,
        ),
    )

    result = allocate(manifest, cart)
    assert result.witness.realized_minor() <= naive_sum(manifest, cart)
    verification = verify_witness(
        witness=result.witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=result.witness.realized_minor(),
    )
    assert verification.ok, [f.to_dict() for f in verification.failures]


@pytest.mark.parametrize("seed", range(40))
def test_random_manifests_always_produce_a_verifying_witness(seed: int) -> None:
    """Property: for any manifest and cart, the emitted witness verifies and understates.

    Randomised over the dimensions that interact — capacity below/above line amount,
    exclusivity collisions, unenrolled benefits, mixed kinds.
    """
    rng = random.Random(seed)
    mccs = (5812, 7011, 5411)

    lines = tuple(
        _line(f"sku_{i}", rng.randrange(10_000, 2_000_000), rng.choice(mccs), "x")
        for i in range(rng.randrange(1, 7))
    )
    cart = _cart(*lines)

    benefits: list[Benefit] = []
    for i in range(rng.randrange(1, 9)):
        kind = rng.choice((KIND_CREDIT, KIND_EARN, KIND_PROTECTION))
        mcc = rng.choice(mccs)
        group = rng.choice((None, "g1", "g2"))
        enrolled = rng.random() > 0.25
        benefits.append(
            Benefit(
                benefit_id=f"ben_{i}",
                kind=kind,
                label=f"ben_{i}",
                eligibility=Eligibility(mccs=(mcc,)),
                rate_bp=rng.randrange(0, 1_000) if kind == KIND_EARN else 0,
                capacity_minor=rng.choice((None, rng.randrange(1, 500_000))),
                flat_minor=rng.randrange(0, 100_000) if kind == KIND_PROTECTION else 0,
                exclusivity_group=group,
                requires_enrollment=not enrolled,
                enrolled=enrolled,
            )
        )
    manifest = _manifest(*benefits)

    result = allocate(manifest, cart)
    asserted = result.witness.realized_minor()

    verification = verify_witness(
        witness=result.witness, manifest=manifest, cart=cart, asserted_minor=asserted
    )
    assert verification.ok, [f.to_dict() for f in verification.failures]
    assert verification.supports_assertion
    assert asserted <= naive_sum(manifest, cart)


def test_allocation_is_deterministic() -> None:
    """The witness is hashed into a signed receipt, so it must be byte-identical on replay."""
    cart = _cart(
        _line("sku_b", 500_000, 5812, "dining"),
        _line("sku_a", 500_000, 5812, "dining"),
    )
    manifest = _manifest(
        _credit("ben_z", 400_000, 5812),
        _credit("ben_a", 400_000, 5812),
    )
    first = allocate(manifest, cart).witness.to_dict(currency=cart.currency)
    second = allocate(manifest, cart).witness.to_dict(currency=cart.currency)
    assert first == second
