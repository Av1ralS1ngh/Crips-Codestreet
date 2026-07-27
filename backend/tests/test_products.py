"""Tests for the modelled card products and the valuation policy that prices their points.

Two things are being defended here.

The first is arithmetic honesty: every rate is checked against the published term it was
read off, so a term that is silently edited breaks a test rather than a pitch. The second
is the invariant that matters more than any single figure — a manifest must never let an
allocation realize more than the benefits it declares, and every witness produced from
these manifests must verify.
"""

from __future__ import annotations

import itertools
import random

import pytest

from caveat.cart import Cart, CartLine
from plumbline import products as P
from plumbline.allocate import allocate, naive_sum
from plumbline.manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    WINDOW_MONTHLY,
    ManifestError,
    canonical_json,
    verify_manifest,
)
from plumbline.witness import verify_witness

CLOCK = 1_785_110_400  # 2026-07-27T00:00:00Z


@pytest.fixture(scope="module")
def cards() -> dict:
    return P.catalogue_by_id(CLOCK)


# --------------------------------------------------------------------------------------
# The valuation policy
# --------------------------------------------------------------------------------------


def test_earn_rate_bp_matches_hand_arithmetic() -> None:
    v = P.DEFAULT_VALUATION
    mr = P.PROGRAM_MEMBERSHIP_REWARDS
    # 4 points per dollar at 1.00 cent each is 4 cents on 100 cents of spend: 400 bp.
    assert v.earn_rate_bp(mr, 4) == 400
    assert v.earn_rate_bp(mr, 1) == 100
    assert v.earn_rate_bp(mr, 5) == 500
    assert v.earn_rate_bp(P.PROGRAM_ULTIMATE_REWARDS, 8) == 800
    # 5 RP per Rs 150 at Re 1 each is Rs 5 on Rs 150: 3.33%, floored to 333 bp.
    rp = P.PROGRAM_HDFC_REWARD_POINTS
    assert v.earn_rate_bp(rp, 5, per_major_units=150) == 333
    assert v.earn_rate_bp(rp, 50, per_major_units=150) == 3333
    assert v.earn_rate_bp(rp, 25, per_major_units=150) == 1666
    # 1 MR per Rs 40 at Rs 0.25 each is Rs 0.25 on Rs 40: 0.625%, floored to 62 bp.
    mr_in = P.PROGRAM_MEMBERSHIP_REWARDS_INDIA
    assert v.earn_rate_bp(mr_in, 1, per_major_units=40) == 62
    assert v.earn_rate_bp(mr_in, 3, per_major_units=40) == 187
    assert v.earn_rate_bp(mr_in, 5, per_major_units=100) == 125
    assert v.earn_rate_bp(mr_in, 1, per_major_units=50) == 50
    # The two Membership Rewards programmes are priced separately. Sharing one key would
    # value an Indian point at a US cent and quietly divide every India rate by 25.
    assert v.minor_per_10000(mr_in) != v.minor_per_10000(mr)


def test_earn_rate_bp_rounds_down_never_up() -> None:
    """Floor division is the whole safety argument for the conversion; pin it."""
    v = P.DEFAULT_VALUATION
    rp = P.PROGRAM_HDFC_REWARD_POINTS
    exact_num = 5 * v.minor_per_10000(rp)
    exact_den = 150 * P.MINOR_UNITS_PER_MAJOR
    assert exact_num % exact_den != 0, "pick an input where rounding actually happens"
    assert v.earn_rate_bp(rp, 5, per_major_units=150) * exact_den <= exact_num


def test_unknown_program_names_what_is_priced() -> None:
    with pytest.raises(P.PointValuationError) as exc:
        P.DEFAULT_VALUATION.earn_rate_bp("avios", 3)
    assert "avios" in str(exc.value)
    assert P.PROGRAM_MEMBERSHIP_REWARDS in str(exc.value)


@pytest.mark.parametrize(
    "points, per_major",
    [(-1, 1), (4, 0), (4, -150)],
)
def test_earn_rate_bp_rejects_nonsense(points: int, per_major: int) -> None:
    with pytest.raises(P.PointValuationError):
        P.DEFAULT_VALUATION.earn_rate_bp(
            P.PROGRAM_MEMBERSHIP_REWARDS, points, per_major_units=per_major
        )


def test_policy_hash_is_stable_and_sensitive() -> None:
    a = P.DEFAULT_VALUATION.policy_hash()
    assert a == P.DEFAULT_VALUATION.policy_hash()
    richer = P.PointValuation(
        policy_id=P.DEFAULT_VALUATION.policy_id,
        basis=P.DEFAULT_VALUATION.basis,
        rates=tuple(
            (program, value * 2) for program, value in P.DEFAULT_VALUATION.rates
        ),
    )
    assert richer.policy_hash() != a


def test_a_richer_policy_produces_a_higher_rate() -> None:
    """The policy is the cardholder's, so changing it must actually move the number."""
    doubled = P.PointValuation(
        policy_id="test/doubled",
        basis="test only",
        rates=((P.PROGRAM_MEMBERSHIP_REWARDS, 20_000),),
    )
    assert doubled.earn_rate_bp(P.PROGRAM_MEMBERSHIP_REWARDS, 4) == 800
    plat = P.amex_platinum_us(CLOCK, doubled)
    base = P.amex_platinum_us(CLOCK)
    plat_rates = {b.benefit_id: b.rate_bp for b in plat.benefits if b.kind == KIND_EARN}
    base_rates = {b.benefit_id: b.rate_bp for b in base.benefits if b.kind == KIND_EARN}
    assert all(plat_rates[k] == 2 * base_rates[k] for k in base_rates)
    assert plat.content_hash() != base.content_hash()


# --------------------------------------------------------------------------------------
# Published terms, one assertion per number a judge might recognise
# --------------------------------------------------------------------------------------


def test_platinum_published_terms(cards: dict) -> None:
    m = cards[P.AMEX_PLATINUM_ID]
    by_id = {b.benefit_id: b for b in m.benefits}
    assert m.currency == P.USD
    assert P.profile(P.AMEX_PLATINUM_ID).annual_fee_minor == 89_500  # $895
    assert by_id["amex_plat_earn_flights_5x"].rate_bp == 500
    assert by_id["amex_plat_earn_hotels_5x"].rate_bp == 500
    assert by_id["amex_plat_earn_base_1x"].rate_bp == 100
    # $500,000 of flight spend at 5x and 1.00 cent per point is $25,000 of value headroom.
    assert by_id["amex_plat_earn_flights_5x"].capacity_minor == 2_500_000
    # The published $600 hotel credit is two $300 semi-annual windows, not one balance.
    assert by_id["amex_plat_credit_hotel_h2"].capacity_minor == 30_000
    assert (
        by_id["amex_plat_credit_hotel_h1"].exclusivity_group
        == by_id["amex_plat_credit_hotel_h2"].exclusivity_group
    )
    # The published $400 Resy credit is four $100 quarters.
    assert by_id["amex_plat_credit_resy_q3"].capacity_minor == 10_000
    assert by_id["amex_plat_credit_airline_fee"].capacity_minor == 20_000
    assert by_id["amex_plat_credit_digital_entertainment"].capacity_minor == 2_500  # $300/12
    assert by_id["amex_plat_credit_lululemon"].capacity_minor == 7_500  # $300/4
    assert by_id["amex_plat_fhr_property_credit"].flat_minor == 10_000


def test_gold_published_terms(cards: dict) -> None:
    m = cards[P.AMEX_GOLD_ID]
    by_id = {b.benefit_id: b for b in m.benefits}
    assert P.profile(P.AMEX_GOLD_ID).annual_fee_minor == 32_500  # $325
    assert by_id["amex_gold_earn_restaurants_4x"].rate_bp == 400
    assert by_id["amex_gold_earn_supermarkets_4x"].rate_bp == 400
    assert by_id["amex_gold_earn_hotels_5x"].rate_bp == 500
    assert by_id["amex_gold_earn_flights_3x"].rate_bp == 300
    assert by_id["amex_gold_earn_base_1x"].rate_bp == 100
    assert by_id["amex_gold_credit_dining"].capacity_minor == 1_000  # $120/12
    assert by_id["amex_gold_credit_uber_cash"].capacity_minor == 1_000  # $120/12
    assert by_id["amex_gold_credit_resy"].capacity_minor == 5_000  # $100/2
    assert by_id["amex_gold_credit_dunkin"].capacity_minor == 700  # $84/12


def test_gold_earn_caps_convert_spend_to_value(cards: dict) -> None:
    """The published caps are on SPEND; the manifest carries VALUE. Check the conversion."""
    by_id = {b.benefit_id: b for b in cards[P.AMEX_GOLD_ID].benefits}
    restaurants = by_id["amex_gold_earn_restaurants_4x"]
    supermarkets = by_id["amex_gold_earn_supermarkets_4x"]
    # $50,000 cap less $31,400 synthetic spend, at 4x and 1.00 cent, is $744 of headroom.
    assert restaurants.capacity_minor == ((5_000_000 - 3_140_000) * 400) // 10_000 == 74_400
    # $25,000 cap less $9,700 synthetic spend is $612.
    assert supermarkets.capacity_minor == ((2_500_000 - 970_000) * 400) // 10_000 == 61_200


def test_chase_sapphire_reserve_published_terms(cards: dict) -> None:
    m = cards[P.CHASE_SAPPHIRE_RESERVE_ID]
    by_id = {b.benefit_id: b for b in m.benefits}
    assert P.profile(P.CHASE_SAPPHIRE_RESERVE_ID).annual_fee_minor == 79_500  # $795
    assert by_id["csr_earn_chase_travel_8x"].rate_bp == 800
    assert by_id["csr_earn_flights_direct_4x"].rate_bp == 400
    assert by_id["csr_earn_hotels_direct_4x"].rate_bp == 400
    assert by_id["csr_earn_lyft_5x"].rate_bp == 500
    assert by_id["csr_earn_dining_3x"].rate_bp == 300
    assert by_id["csr_credit_travel"].capacity_minor == 30_000  # $300
    # The published $500 is two $250 per-booking credits, not one $500 balance.
    assert by_id["csr_credit_the_edit_1"].capacity_minor == 25_000  # $250
    assert by_id["csr_credit_the_edit_2"].capacity_minor == 25_000  # $250
    # The published $300 dining credit is $150 per half; only the live half carries balance.
    assert by_id["csr_credit_dining_h1"].capacity_minor == 0  # January–June, closed
    assert by_id["csr_credit_dining_h2"].capacity_minor == 15_000  # $150, July–December
    assert by_id["csr_credit_lyft"].capacity_minor == 1_000  # $120/12
    # The conservative reading: one booking draws at most one travel credit.
    assert (
        by_id["csr_credit_travel"].exclusivity_group
        == by_id["csr_credit_the_edit_1"].exclusivity_group
        == by_id["csr_credit_the_edit_2"].exclusivity_group
        is not None
    )
    assert (
        by_id["csr_credit_dining_h1"].exclusivity_group
        == by_id["csr_credit_dining_h2"].exclusivity_group
        is not None
    )


def test_no_single_line_can_draw_more_than_the_published_per_booking_credit() -> None:
    """The Edit publishes $250 per booking. One cart line must never realize $500.

    Regression for a manifest that carried the annual total as a single balance. The
    allocator was sound; the modelled capacity was not, and a sound allocator over a wrong
    capacity still signs a number the card cannot deliver.
    """
    manifest = P.chase_sapphire_reserve(CLOCK)
    cart = Cart(
        merchant="The Edit by Chase Travel",
        currency=P.USD,
        lines=(
            CartLine(
                sku="edit_stay",
                description="prepaid The Edit stay, one booking",
                amount=90_000,
                mcc=7011,
                category=P.CAT_CHASE_TRAVEL,
            ),
        ),
    )
    result = allocate(manifest, cart)
    per_edit = [
        a.value_minor
        for a in result.witness.assignments
        if a.benefit_id.startswith("csr_credit_the_edit")
    ]
    assert all(v <= 25_000 for v in per_edit), per_edit
    # One line draws at most one member of the travel-credit group, so the credit total on
    # a single booking cannot exceed the largest single credit in it.
    credits = [
        a.value_minor
        for a in result.witness.assignments
        if a.benefit_id.startswith("csr_credit_")
    ]
    assert sum(credits) <= 30_000, credits
    assert verify_witness(
        witness=result.witness,
        manifest=manifest,
        cart=cart,
        asserted_minor=result.witness.realized_minor(),
    ).ok


def test_the_published_edit_annual_total_stays_reachable_across_bookings() -> None:
    """Conservative must not mean wrong in the other direction: $500 stays reachable.

    Three lines, because the $300 travel credit shares the exclusivity group and greedily
    takes the first booking. Both $250 Edit credits then land on the remaining two.
    """
    manifest = P.chase_sapphire_reserve(CLOCK)
    cart = Cart(
        merchant="The Edit by Chase Travel",
        currency=P.USD,
        lines=tuple(
            CartLine(
                sku=f"edit_stay_{i}",
                description=f"prepaid The Edit stay, booking {i}",
                amount=90_000,
                mcc=7011,
                category=P.CAT_CHASE_TRAVEL,
            )
            for i in range(3)
        ),
    )
    result = allocate(manifest, cart)
    edit = sum(
        a.value_minor
        for a in result.witness.assignments
        if a.benefit_id.startswith("csr_credit_the_edit")
    )
    assert edit == 50_000
    # No line carries two credits from the shared group.
    by_line: dict[str, int] = {}
    for a in result.witness.assignments:
        if a.benefit_id.startswith("csr_credit_") and not a.benefit_id.endswith("lyft"):
            by_line[a.line_sku] = by_line.get(a.line_sku, 0) + 1
    assert all(n == 1 for n in by_line.values()), by_line


def test_hdfc_infinia_published_terms(cards: dict) -> None:
    m = cards[P.HDFC_INFINIA_ID]
    by_id = {b.benefit_id: b for b in m.benefits}
    assert m.currency == P.INR
    assert P.profile(P.HDFC_INFINIA_ID).annual_fee_minor == 1_250_000  # Rs 12,500 pre-GST
    assert by_id["hdfc_infinia_earn_base"].rate_bp == 333  # 5 RP / Rs 150 at Re 1 per RP
    assert by_id["hdfc_infinia_earn_smartbuy_hotel_10x"].rate_bp == 3333  # 10X of the base
    assert by_id["hdfc_infinia_earn_smartbuy_flight_5x"].rate_bp == 1666  # 5X of the base
    # 15,000 accelerated points a month, less 5,800 synthetic, at Re 1 per point.
    assert by_id["hdfc_infinia_earn_smartbuy_hotel_10x"].capacity_minor == 920_000


def test_hdfc_shared_monthly_pool_is_carried_once(cards: dict) -> None:
    """Two accelerators drawing on one pool must not each carry a copy of it."""
    by_id = {b.benefit_id: b for b in cards[P.HDFC_INFINIA_ID].benefits}
    hotel = by_id["hdfc_infinia_earn_smartbuy_hotel_10x"]
    flight = by_id["hdfc_infinia_earn_smartbuy_flight_5x"]
    pool = (
        P.HDFC_SMARTBUY_MONTHLY_POINT_CAP
        * P.DEFAULT_VALUATION.minor_per_10000(P.PROGRAM_HDFC_REWARD_POINTS)
    ) // 10_000
    assert (hotel.capacity_minor or 0) + (flight.capacity_minor or 0) <= pool
    assert not flight.available()


def test_hdfc_linear_rate_stays_within_one_block() -> None:
    """HDFC pays in whole Rs 150 blocks; a linear rate cannot, so bound the difference.

    The modelled rate may exceed the block-rounded figure, but by strictly less than one
    block's worth of points. That bound is the honest statement of the modelling gap, and
    it is asserted rather than asserted-in-prose.
    """
    rate_bp = P.DEFAULT_VALUATION.earn_rate_bp(
        P.PROGRAM_HDFC_REWARD_POINTS,
        P.HDFC_BASE_POINTS_PER_BLOCK,
        per_major_units=P.HDFC_POINT_BLOCK_MAJOR,
    )
    block_minor = P.HDFC_POINT_BLOCK_MAJOR * P.MINOR_UNITS_PER_MAJOR
    per_block_value = (
        P.HDFC_BASE_POINTS_PER_BLOCK
        * P.DEFAULT_VALUATION.minor_per_10000(P.PROGRAM_HDFC_REWARD_POINTS)
    ) // 10_000
    rng = random.Random(20260727)
    amounts = list(range(0, 60_000, 137)) + [rng.randrange(0, 50_000_000) for _ in range(4_000)]
    for amount in amounts:
        modelled = (amount * rate_bp) // 10_000
        actual = (amount // block_minor) * per_block_value
        assert modelled - actual < per_block_value, (amount, modelled, actual)


def test_amex_platinum_india_published_terms(cards: dict) -> None:
    """The India flagship. Every figure here is one an Amex India judge would recognise.

    This is the manifest the console renders under the words "American Express", so a wrong
    number here costs more than a wrong number anywhere else in the repository.
    """
    m = cards[P.AMEX_PLATINUM_INDIA_ID]
    by_id = {b.benefit_id: b for b in m.benefits}
    assert m.currency == P.INR
    assert m.issuer == "American Express"
    # Published annual fee is INR 66,000 plus applicable taxes, with no spend-based waiver.
    assert P.profile(P.AMEX_PLATINUM_INDIA_ID).annual_fee_minor == 6_600_000
    # 1 MR per Rs 40, 3X abroad, and 5 MR per Rs 100 on fuel, all at Rs 0.25 per point.
    assert by_id["amex_in_plat_earn_base"].rate_bp == 62
    assert by_id["amex_in_plat_earn_overseas_3x"].rate_bp == 187
    assert by_id["amex_in_plat_earn_fuel"].rate_bp == 125
    # The published fuel cap is 5,000 Points a calendar month, carried as value headroom.
    assert P.AMEX_IN_FUEL_MONTHLY_POINT_CAP == 5_000
    assert by_id["amex_in_plat_earn_fuel"].window == WINDOW_MONTHLY
    assert by_id["amex_in_plat_earn_fuel"].capacity_minor == (
        (5_000 - P.AMEX_IN_FUEL_POINTS_DRAWN) * 250_000
    ) // 10_000
    # Fuel is excluded from the base rate and carries its own, so the two must not overlap.
    assert P.CAT_FUEL not in by_id["amex_in_plat_earn_base"].eligibility.categories
    assert by_id["amex_in_plat_earn_fuel"].eligibility.categories == (P.CAT_FUEL,)


def test_taj_epicure_is_a_membership_and_is_never_priced(cards: dict) -> None:
    """The one term this card is most often misquoted on, pinned by test.

    Taj Epicure Plus is a membership the Cardmember enrols into, delivering percentage
    privileges. It is not a rupee credit, American Express publishes no rupee value for it,
    and a manifest that scored it would put a number on the receipt that no allocation could
    realize and that the issuer never published. A console fixture asserting a "Taj Epicure
    dining credit worth Rs 10,000" is exactly the defect this test exists to prevent.
    """
    m = cards[P.AMEX_PLATINUM_INDIA_ID]
    epicure = next(b for b in m.benefits if b.benefit_id == "amex_in_plat_taj_epicure")
    assert epicure.kind == KIND_UNPRICED
    assert epicure.rate_bp == 0 and epicure.flat_minor == 0 and epicure.capacity_minor is None
    assert "MEMBERSHIP" in epicure.note and "not a rupee credit" in epicure.note
    # And nothing anywhere on this card prices a Taj benefit under any other name.
    for b in m.benefits:
        if b.is_priced():
            assert "taj" not in b.label.lower(), b.benefit_id
            assert "epicure" not in b.label.lower(), b.benefit_id


def test_the_district_dining_offer_is_declared_and_not_priced(cards: dict) -> None:
    """A coupon-gated, one-weekday, time-boxed promotion is not a term this manifest can hold.

    The manifest vocabulary expresses eligibility, capacity, exclusivity and a reset window.
    It cannot express "Wednesdays only, with a coupon code, until December". A benefit priced
    without the conditions that gate it overstates, so it is declared and left unscored.
    """
    m = cards[P.AMEX_PLATINUM_INDIA_ID]
    offer = next(b for b in m.benefits if b.benefit_id == "amex_in_plat_district_dining_offer")
    assert offer.kind == KIND_UNPRICED
    assert "coupon code" in offer.note and "weekday" in offer.note


def test_amex_platinum_travel_india_published_terms(cards: dict) -> None:
    m = cards[P.AMEX_PLATINUM_TRAVEL_INDIA_ID]
    by_id = {b.benefit_id: b for b in m.benefits}
    assert m.currency == P.INR
    # Published first-year and renewal fee is INR 5,000 plus applicable taxes.
    assert P.profile(P.AMEX_PLATINUM_TRAVEL_INDIA_ID).annual_fee_minor == 500_000
    # 1 MR per Rs 50 at Rs 0.25 per point.
    assert by_id["amex_in_travel_earn_base"].rate_bp == 50
    # Every milestone is measured over a membership year, so none of them is priced.
    assert by_id["amex_in_travel_milestones"].kind == KIND_UNPRICED
    assert by_id["amex_in_travel_welcome"].kind == KIND_UNPRICED
    # This card publishes no fuel rate, so a fuel line must earn nothing on it.
    cart = Cart.of(
        "m_fuel",
        [CartLine("f1", "fuel", 480_000, 5541, P.CAT_FUEL)],
        currency=P.INR,
    )
    assert allocate(m, cart).witness.realized_minor() == 0
    assert naive_sum(m, cart) == 0


def test_the_india_cards_exclude_what_their_terms_exclude(cards: dict) -> None:
    """Insurance, utilities, cash and EMI earn nothing under the published India terms."""
    for manifest_id in (P.AMEX_PLATINUM_INDIA_ID, P.AMEX_PLATINUM_TRAVEL_INDIA_ID):
        m = cards[manifest_id]
        for category in ("insurance", "utilities", "cash_transaction", "emi_conversion"):
            cart = Cart.of(
                "m_x",
                [CartLine("l1", category, 1_500_000, 6012, category)],
                currency=P.INR,
            )
            assert allocate(m, cart).witness.realized_minor() == 0, (manifest_id, category)
            assert naive_sum(m, cart) == 0, (manifest_id, category)


def test_every_product_name_on_a_manifest_is_one_this_module_sourced(cards: dict) -> None:
    """Issuer and product on a manifest match the profile that declared them, and the
    provenance line is this module's own.

    The failure this guards is specific, and an adversarial reader found it in a console
    fixture: `American Express` beside a real product name, carrying terms nobody published
    and a source string claiming published-terms provenance for them. Card terms are built
    here, sourced here and pinned here; if a real product name reaches a screen, it reached
    it through this table.
    """
    for manifest_id, m in cards.items():
        prof = P.profile(manifest_id)
        assert (m.issuer, m.product) == (prof.issuer, prof.product)
        assert m.source == P._source(m.issued_at)


# --------------------------------------------------------------------------------------
# Structural rules every manifest must satisfy
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_every_manifest_is_labelled_and_signable(manifest_id: str, cards: dict) -> None:
    m = cards[manifest_id]
    assert "publicly published card terms" in m.source
    assert "SYNTHETIC member state" in m.source
    assert m.issued_at == CLOCK
    signed = next(
        s
        for s in P.sign_catalogue(CLOCK, "unit-test-key")
        if s.manifest.manifest_id == manifest_id
    )
    assert verify_manifest(signed, "unit-test-key")
    assert not verify_manifest(signed, "a-different-key")


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_no_acceptance_predicate_anywhere(manifest_id: str, cards: dict) -> None:
    """The deletion is deliberate and is defended here so it cannot creep back in.

    An issuer-signed field naming where its own instrument is refused is a machine-readable
    instruction to route away from that issuer. No field, no note and no label may carry it.
    """
    m = cards[manifest_id]
    # The signed body is the only thing a counterparty reads, so search all of it.
    signed_text = canonical_json(m.body()).decode("utf-8").lower()
    for token in (
        "acceptance",
        "accepted_at",
        "not_accepted",
        "declined_at",
        "refused_at",
        "coverage_gap",
        "route_away",
        "merchant_blocklist",
    ):
        assert token not in signed_text, f"{manifest_id} signs {token!r}"
    # Eligibility is an allow-list of where a benefit APPLIES. Nothing anywhere names a
    # place the instrument itself would be turned down.
    for b in m.benefits:
        assert not b.eligibility.merchants or all(
            isinstance(x, str) and x for x in b.eligibility.merchants
        )


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_every_manifest_declares_unpriced_value(manifest_id: str, cards: dict) -> None:
    """CONSIDERED_BUT_UNPRICED is how the receipt proves the agent saw the non-numeric."""
    m = cards[manifest_id]
    unpriced = m.unpriced()
    assert unpriced, f"{manifest_id} prices everything, which no card does"
    for b in unpriced:
        assert b.note, f"{b.benefit_id} is unpriced with no rationale"
        assert b.rate_bp == 0 and b.flat_minor == 0
    declarations = P.unpriced_declarations(m)
    assert len(declarations) == len(unpriced)
    assert all(d["rationale"] for d in declarations)


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_synthetic_balances_are_labelled(manifest_id: str, cards: dict) -> None:
    """A remaining balance is member state we invented. Every one of them says so."""
    for b in cards[manifest_id].benefits:
        if b.kind == KIND_CREDIT or (b.kind == KIND_EARN and b.capacity_minor is not None):
            assert P.SYNTHETIC_STATE in b.note or "shared" in b.note.lower(), b.benefit_id


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_base_and_bonus_earn_share_an_exclusivity_group(manifest_id: str, cards: dict) -> None:
    """5x replaces 1x; it is never added to it. Modelling them as additive is the single
    most common way a per-line valuation overstates a premium card."""
    earns = [b for b in cards[manifest_id].benefits if b.kind == KIND_EARN]
    groups = {b.exclusivity_group for b in earns}
    assert len(groups) == 1 and None not in groups, manifest_id


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_no_priced_benefit_is_unlabelled_or_negative(manifest_id: str, cards: dict) -> None:
    for b in cards[manifest_id].benefits:
        assert b.label, b.benefit_id
        assert b.rate_bp >= 0 and b.flat_minor >= 0
        assert b.capacity_minor is None or b.capacity_minor >= 0
        if b.kind == KIND_PROTECTION:
            # A protection is granted once per qualifying line, so its capacity must be a
            # multiple of the flat amount or it could never be granted at all.
            assert b.flat_minor > 0
            assert b.capacity_minor is None or b.capacity_minor >= b.flat_minor


def test_catalogue_is_deterministic_and_complete() -> None:
    first = P.catalogue(CLOCK)
    second = P.catalogue(CLOCK)
    assert [m.content_hash() for m in first] == [m.content_hash() for m in second]
    assert len(first) == len(P.PROFILES)
    # A floor, not an exact count. The catalogue grows as products are modelled, and a test
    # that must be edited to add a card gets edited without being read. What has to hold is
    # that every declared profile builds and none is silently dropped.
    assert len(first) >= 4
    assert {m.manifest_id for m in first} == set(P.PROFILE_BY_ID)


def test_catalogue_for_currency_splits_and_rejects_unknown() -> None:
    usd = P.catalogue_for_currency(P.USD, CLOCK)
    inr = P.catalogue_for_currency(P.INR, CLOCK)
    assert usd and inr, "both supported currencies must be modelled by some product"
    # The split is a partition: nothing lands in both buckets and nothing falls out of
    # both, which is the property that matters when a product is added.
    assert len(usd) + len(inr) == len(P.PROFILES)
    assert {m.currency for m in usd} == {P.USD}
    assert {m.currency for m in inr} == {P.INR}
    with pytest.raises(ManifestError):
        P.catalogue_for_currency("EUR", CLOCK)


def test_profile_lookup_errors_name_the_alternatives() -> None:
    with pytest.raises(ManifestError) as exc:
        P.profile("not-a-card")
    assert P.AMEX_PLATINUM_ID in str(exc.value)


def test_fmt_currency_uses_the_right_symbol_and_rejects_unknown() -> None:
    assert P.fmt_currency(124_000, P.USD) == "$1,240"
    assert P.fmt_currency(45_212, P.USD) == "$452.12"
    assert P.fmt_currency(2_650_000, P.INR) == "₹26,500"
    with pytest.raises(ManifestError):
        P.fmt_currency(100, "JPY")


def test_describe_catalogue_lists_every_product() -> None:
    lines = P.describe_catalogue(CLOCK)
    assert len(lines) == len(P.PROFILES)
    assert any("Infinia" in line for line in lines)
    assert all("fee" in line for line in lines)


def test_product_profile_to_dict_carries_the_fee_out_of_band() -> None:
    d = P.profile(P.AMEX_PLATINUM_ID).to_dict()
    assert d["annual_fee_display"] == "$895"
    assert "never netted" in d["fee_note"]
    # And the fee appears nowhere inside the manifest itself.
    assert "895" not in str(P.amex_platinum_us(CLOCK).body())


# --------------------------------------------------------------------------------------
# The invariant: no manifest here can produce a witness that fails verification
# --------------------------------------------------------------------------------------

_CATEGORIES = (
    P.CAT_AIRFARE,
    P.CAT_AIRLINE_INCIDENTAL,
    P.CAT_PREPAID_HOTEL_AMEX,
    P.CAT_HOTEL_DIRECT,
    P.CAT_CHASE_TRAVEL,
    P.CAT_DINING,
    P.CAT_RESY_DINING,
    P.CAT_GOLD_DINING_PARTNER,
    P.CAT_CSR_EXCLUSIVE_TABLE,
    P.CAT_DUNKIN,
    P.CAT_US_SUPERMARKET,
    P.CAT_UBER,
    P.CAT_LYFT,
    P.CAT_DIGITAL_ENTERTAINMENT,
    P.CAT_LULULEMON,
    P.CAT_SMARTBUY_HOTEL,
    P.CAT_SMARTBUY_FLIGHT,
    P.CAT_INDIA_RETAIL,
    P.CAT_FUEL,
    P.CAT_OVERSEAS,
)


def _random_cart(rng: random.Random, currency: str) -> Cart:
    n = rng.randrange(1, 8)
    lines = [
        CartLine(
            sku=f"sku_{i}",
            description=f"line {i}",
            amount=rng.randrange(0, 5_000_000 if currency == P.INR else 200_000),
            mcc=rng.choice((3000, 4722, 5411, 5651, 5732, 5812, 5815, 7011)),
            category=rng.choice(_CATEGORIES),
        )
        for i in range(n)
    ]
    return Cart.of(rng.choice(("m_a", "m_b", "amextravel.com")), lines, currency=currency)


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_witness_always_verifies_on_random_carts(manifest_id: str, cards: dict) -> None:
    """Property: the allocator's own witness verifies against the manifest, always.

    Randomised over cart shape, line amount, MCC and category. A manifest that could lead
    the allocator into an unverifiable witness is a manifest that could sign a number no
    counterparty can check.
    """
    m = cards[manifest_id]
    rng = random.Random(f"witness-{manifest_id}")
    for _ in range(400):
        cart = _random_cart(rng, m.currency)
        result = allocate(m, cart)
        asserted = result.witness.realized_minor()
        v = verify_witness(witness=result.witness, manifest=m, cart=cart, asserted_minor=asserted)
        assert v.ok, (cart.to_dict(), v.to_dict())
        assert v.supports_assertion
        assert v.realized_minor == asserted


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_naive_sum_never_understates_the_witness(manifest_id: str, cards: dict) -> None:
    """Property: the naive figure is an upper bound on the witness, never a lower one.

    If this ever inverted, the overstatement demonstration would be showing the opposite of
    what it claims, so it is asserted rather than assumed.
    """
    m = cards[manifest_id]
    rng = random.Random(f"naive-{manifest_id}")
    for _ in range(400):
        cart = _random_cart(rng, m.currency)
        assert naive_sum(m, cart) >= allocate(m, cart).witness.realized_minor()


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_no_credit_ever_offsets_more_than_its_line(manifest_id: str, cards: dict) -> None:
    """A statement credit reimburses spend. It can never reimburse spend not on the line."""
    m = cards[manifest_id]
    by_id = {b.benefit_id: b for b in m.benefits}
    rng = random.Random(f"offset-{manifest_id}")
    for _ in range(200):
        cart = _random_cart(rng, m.currency)
        by_sku = {line.sku: line for line in cart.lines}
        offset: dict[str, int] = {}
        for a in allocate(m, cart).witness.assignments:
            if by_id[a.benefit_id].kind == KIND_CREDIT:
                offset[a.line_sku] = offset.get(a.line_sku, 0) + a.value_minor
        for sku, total in offset.items():
            assert total <= by_sku[sku].amount


@pytest.mark.parametrize("manifest_id", [p.manifest_id for p in P.PROFILES])
def test_allocation_is_byte_stable_under_line_reordering(manifest_id: str, cards: dict) -> None:
    """The witness is hashed into a receipt, so cart line order must not move it."""
    m = cards[manifest_id]
    rng = random.Random(f"order-{manifest_id}")
    for _ in range(60):
        cart = _random_cart(rng, m.currency)
        shuffled = list(cart.lines)
        rng.shuffle(shuffled)
        a = allocate(m, cart).witness
        b = allocate(m, cart.with_lines(shuffled)).witness
        assert sorted(x.to_dict().items() for x in a.assignments) == sorted(
            x.to_dict().items() for x in b.assignments
        )


def test_unpriced_benefits_never_reach_an_allocation(cards: dict) -> None:
    """A declared-but-unpriced benefit is seen and never scored. Check it never assigns."""
    rng = random.Random("unpriced")
    for m in cards.values():
        unpriced_ids = {b.benefit_id for b in m.benefits if b.kind == KIND_UNPRICED}
        assert unpriced_ids
        for _ in range(80):
            cart = _random_cart(rng, m.currency)
            assigned = {a.benefit_id for a in allocate(m, cart).witness.assignments}
            assert not (assigned & unpriced_ids)


def test_enrollment_gate_scores_zero_and_is_still_declared(cards: dict) -> None:
    plat = cards[P.AMEX_PLATINUM_ID]
    lulu = next(b for b in plat.benefits if b.benefit_id == "amex_plat_credit_lululemon")
    assert lulu.requires_enrollment and not lulu.enrolled and not lulu.available()
    assert lulu in plat.benefits, "an unavailable benefit is still declared, not dropped"
    cart = Cart.of(
        "m_lulu",
        [CartLine("l1", "lululemon order", 12_800, 5651, P.CAT_LULULEMON)],
        currency=P.USD,
    )
    assigned = {a.benefit_id for a in allocate(plat, cart).witness.assignments}
    assert "amex_plat_credit_lululemon" not in assigned


def test_hdfc_excluded_categories_earn_nothing(cards: dict) -> None:
    """Wallet, fuel, rent, government and EMI earn no points under the published terms."""
    m = cards[P.HDFC_INFINIA_ID]
    for category in P.HDFC_UNREWARDED:
        cart = Cart.of(
            "m_x",
            [CartLine("l1", category, 1_500_000, 5541, category)],
            currency=P.INR,
        )
        assert allocate(m, cart).witness.realized_minor() == 0
        assert naive_sum(m, cart) == 0


def test_benefit_ids_are_globally_unique() -> None:
    """Receipts name benefits by id across instruments; a collision would misattribute."""
    ids = [b.benefit_id for m in P.catalogue(CLOCK) for b in m.benefits]
    duplicates = [i for i, count in itertools.groupby(sorted(ids)) if len(list(count)) > 1]
    assert not duplicates
