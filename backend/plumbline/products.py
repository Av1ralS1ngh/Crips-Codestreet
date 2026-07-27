"""Six card products, modelled from publicly published terms.

Every number here comes from terms the issuer publishes. Each one carries a comment naming
what it was read off, because a judge who recognises a real card term and finds it wrong
has been handed a reason to disbelieve everything else on the slide.

What this module is careful NOT to claim:

  * No live Offers feed. Amex Offers, targeted merchant offers and enrollment-gated
    promotions are declared as CONSIDERED_BUT_UNPRICED. We model no access to member
    targeting and assert none.
  * No real member state. Every remaining balance is SYNTHETIC — a plausible mid-year
    position for a demo persona, labelled as such on the manifest `source` and again in
    each affected benefit's `note`. A production overlay reads the issuer's own state.
  * No acceptance predicate. Nothing here says where a card will be refused. An
    issuer-signed field naming where its own instrument is declined is a machine-readable
    instruction to route away from that issuer, with the issuer's signature on the reason.
    We built one, removed it, and this paragraph is the record of the removal. Acceptance
    belongs in the agent's routing layer, from data the agent already holds.
  * No annual fee in the per-cart number. `ProductProfile.annual_fee_minor` is carried for
    display only. A per-cart valuation says nothing about whether a card earns its fee, and
    a system that quietly netted the fee against one cart would be lying about both.

**Points are converted to money by a policy, and the policy is not the issuer's.** The
manifest signs the fact "4 points per dollar at restaurants." What a point is worth is a
`PointValuation` — owned by the cardholder or their agent, hashed into the receipt, never
issuer-endorsed. It is a distinct thing from `evaluate.ValuationPolicy`, which is the rule
that RANKS instruments; this one only prices a point. `DEFAULT_VALUATION` deliberately uses each program's published redemption
floor rather than a transfer-partner estimate, because a transfer valuation needs an
itinerary-specific assumption this system does not have.

Modelling fidelity, stated rather than hidden:

  * HDFC awards points in whole ₹150 blocks. A linear basis-point rate cannot reproduce
    block rounding, so `hdfc_infinia`'s modelled rate can exceed the block-rounded figure
    on a single line — by strictly less than one block's worth of points. The bound is
    asserted by test (`test_products.py::test_hdfc_linear_rate_stays_within_one_block`).
  * Insurance-style protections are never priced. Pricing contingent cover requires a
    claims-probability assumption; this system does not have one and will not invent one.
    They are declared as CONSIDERED_BUT_UNPRICED so a receipt proves the agent saw them.
  * Where two credits might or might not stack on one line under an issuer's terms, they
    are modelled as mutually exclusive. If they do stack, this understates, which is the
    safe direction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from caveat.money import CURRENCY_SYMBOLS, UnknownCurrencyError
from caveat.money import fmt_currency as _fmt_currency

from .manifest import (
    KIND_CREDIT,
    KIND_EARN,
    KIND_PROTECTION,
    KIND_UNPRICED,
    WINDOW_ANNUAL,
    WINDOW_MONTHLY,
    WINDOW_NONE,
    WINDOW_QUARTERLY,
    WINDOW_SEMIANNUAL,
    Benefit,
    Eligibility,
    Manifest,
    ManifestError,
    SignedManifest,
    build_manifest,
    canonical_json,
    sign_manifest,
)

# --------------------------------------------------------------------------------------
# Provenance labels. Every manifest built here carries one; nothing here is unlabelled.
# --------------------------------------------------------------------------------------

SOURCE_PUBLISHED_TERMS = (
    "publicly published card terms as at {as_of}, modelled; remaining balances are "
    "SYNTHETIC member state, not live issuer data"
)

SYNTHETIC_STATE = "SYNTHETIC member state"

# Attached to any benefit whose availability depends on a member action we do not observe.
NOT_ENROLLED_NOTE = (
    "enrollment required and not enrolled in the {state}; declared so the receipt proves "
    "the agent saw it and scored it at zero"
)

WINDOW_CLOSED_NOTE = "{window} window closed at the modelled clock; {state}"

UNPRICED_RATIONALE_CONTINGENT = (
    "declared and considered, deliberately not scored: pricing contingent cover needs a "
    "claims-probability assumption this system does not have and will not invent"
)

UNPRICED_RATIONALE_NON_NUMERIC = (
    "declared and considered, deliberately not scored: service, access and status are "
    "real value that no integer on this receipt claims to capture"
)

UNPRICED_RATIONALE_NO_FEED = (
    "declared and considered, deliberately not scored: member-targeted and "
    "enrollment-gated, and we model no live Offers feed and claim no access to one"
)

# --------------------------------------------------------------------------------------
# Currency. Both supported currencies have exactly 100 minor units to the major unit,
# which is what `PointValuation.earn_rate_bp` assumes when it converts points to a rate.
# --------------------------------------------------------------------------------------

USD = "USD"
INR = "INR"

# The table lives in `caveat.money` so the witness verifier can reach it without importing
# this catalogue. Re-exported here because the currency vocabulary reads as a product fact.
CURRENCY_SYMBOL: Mapping[str, str] = CURRENCY_SYMBOLS
MINOR_UNITS_PER_MAJOR = 100


def fmt_currency(minor: int, currency: str) -> str:
    """Render minor units with the right symbol.

    Digit grouping comes from `caveat.money.fmt_money`, which groups the Indian way
    (last three digits, then pairs). The two conventions agree below one hundred thousand
    major units, which covers every figure in this repo's demo carts.

    An unknown currency raises `ManifestError` here rather than the kernel's own
    `UnknownCurrencyError`, because at this layer the caller is holding a manifest and a
    manifest naming an unrenderable currency is a manifest defect.
    """
    try:
        return _fmt_currency(minor, currency)
    except UnknownCurrencyError as exc:
        raise ManifestError(str(exc)) from exc


# --------------------------------------------------------------------------------------
# Category vocabulary. Cart lines and benefit eligibility must agree on these strings, so
# they live here as constants rather than as literals scattered across scenarios.
# --------------------------------------------------------------------------------------

CAT_AIRFARE = "airfare"
CAT_AIRLINE_INCIDENTAL = "airline_incidental"
CAT_PREPAID_HOTEL_AMEX = "prepaid_hotel_amex_travel"
CAT_HOTEL_DIRECT = "hotel_direct"
CAT_CHASE_TRAVEL = "chase_travel"
CAT_DINING = "dining"
CAT_RESY_DINING = "resy_dining"
CAT_GOLD_DINING_PARTNER = "gold_dining_partner"
CAT_CSR_EXCLUSIVE_TABLE = "csr_exclusive_table"
CAT_DUNKIN = "dunkin"
CAT_US_SUPERMARKET = "us_supermarket"
CAT_UBER = "uber"
CAT_LYFT = "lyft"
CAT_DIGITAL_ENTERTAINMENT = "digital_entertainment"
CAT_LULULEMON = "lululemon"
CAT_SMARTBUY_HOTEL = "smartbuy_hotel"
CAT_SMARTBUY_FLIGHT = "smartbuy_flight"
CAT_INDIA_RETAIL = "india_retail"
CAT_FUEL = "fuel"
# Spend billed to an Indian card but incurred abroad. It is a property of the LINE, not an
# undeclared term hiding inside a benefit: the Amex India cards publish a 3X overseas rate,
# and a manifest may only honour a condition its own vocabulary can express.
CAT_OVERSEAS = "overseas_spend"

# Restaurants, in the sense the US card terms use: "dining worldwide" / "restaurants
# worldwide" reaches delivery partners and coffee chains, not just white-tablecloth rooms.
DINING_CATEGORIES = (
    CAT_DINING,
    CAT_RESY_DINING,
    CAT_GOLD_DINING_PARTNER,
    CAT_CSR_EXCLUSIVE_TABLE,
    CAT_DUNKIN,
)

# HDFC pays no reward points at all on wallet loads, fuel, rent, government payments or
# EMI conversions. `Eligibility` is an allow-list, so the exclusion is expressed by naming
# what DOES earn. A category nobody listed earns nothing, which understates and is safe.
HDFC_REWARDED_CATEGORIES = (
    CAT_SMARTBUY_HOTEL,
    CAT_SMARTBUY_FLIGHT,
    CAT_INDIA_RETAIL,
    CAT_DINING,
    # Eligible retail spend is not confined to the issuer's own portal: a hotel or a flight
    # booked direct, and spend incurred abroad, earn the same base rate under the published
    # terms. Omitting them would understate a competitor, which is safe, but it would also
    # make a channel-neutral comparison unavailable — and comparing a card only on its own
    # portal is the thing this system exists to stop.
    CAT_HOTEL_DIRECT,
    CAT_AIRFARE,
    CAT_OVERSEAS,
)

HDFC_UNREWARDED = ("wallet_load", CAT_FUEL, "rent", "government", "emi_conversion")

# The Amex India cards publish their exclusions as a list. `Eligibility` is an allow-list,
# so the exclusion is again expressed by naming what DOES earn the base rate. Fuel is on
# the excluded list and carries its own published rate, so it is absent here and declared
# as a separate benefit.
AMEX_IN_REWARDED_CATEGORIES = (
    CAT_DINING,
    CAT_HOTEL_DIRECT,
    CAT_AIRFARE,
    CAT_INDIA_RETAIL,
    CAT_OVERSEAS,
)

AMEX_IN_UNREWARDED = ("fuel", "insurance", "utilities", "cash_transaction", "emi_conversion")


# --------------------------------------------------------------------------------------
# Point valuation — cardholder policy, not issuer fact
# --------------------------------------------------------------------------------------

PROGRAM_MEMBERSHIP_REWARDS = "membership_rewards"
PROGRAM_ULTIMATE_REWARDS = "ultimate_rewards"
PROGRAM_HDFC_REWARD_POINTS = "hdfc_reward_points"
# The India programme is priced separately from the US one because it redeems in rupees at
# its own published rate. Sharing a key would silently value an Indian point at a US cent.
PROGRAM_MEMBERSHIP_REWARDS_INDIA = "membership_rewards_india"


class PointValuationError(ValueError):
    """Raised when a policy cannot price a program it was asked about."""


@dataclass(frozen=True)
class PointValuation:
    """What a loyalty point is worth, in minor units, under one named policy.

    Distinct from `evaluate.ValuationPolicy`, which is the cardholder's ranking rule.
    This object answers only "what is a point worth", and it is an input to building a
    manifest rather than an input to ranking one.

    This is the object CLAUDE.md keeps out of the issuer's signature. The manifest carries
    the signed fact "4 points per dollar"; this carries the unsigned opinion "a point is
    worth a cent." The receipt records `policy_hash()` so a reader can see exactly which
    opinion produced the ranking, and so that changing the opinion changes the hash.

    Rates are keyed by program and denominated as "minor units for ten thousand points",
    which keeps every conversion in integers. 10,000 Membership Rewards points valued at
    10,000 cents is one cent per point.
    """

    policy_id: str
    basis: str
    rates: tuple[tuple[str, int], ...]

    def programs(self) -> tuple[str, ...]:
        return tuple(program for program, _ in self.rates)

    def minor_per_10000(self, program: str) -> int:
        for name, value in self.rates:
            if name == program:
                return value
        raise PointValuationError(
            f"policy {self.policy_id!r} prices no program named {program!r}; it prices "
            f"{', '.join(self.programs()) or '(nothing)'}. Add a rate for it, with the "
            f"published redemption it is read off, before valuing a card that earns it."
        )

    def earn_rate_bp(self, program: str, points: int, per_major_units: int = 1) -> int:
        """Basis points of spend returned as value, for `points` per `per_major_units`.

            value of the points, in minor units   = points * minor_per_10000 / 10_000
            spend that earned them, in minor units = per_major_units * 100
            rate_bp = 10_000 * value / spend      = points * minor_per_10000
                                                    ----------------------------
                                                       per_major_units * 100

        Floor division rounds the modelled rate DOWN. A rate that is too low understates
        the card, which is the only safe direction for this system to round.
        """
        if points < 0:
            raise PointValuationError(
                f"points must be >= 0, got {points}; a negative earn rate would make the "
                f"naive sum smaller than the witness it is supposed to bound"
            )
        if per_major_units < 1:
            raise PointValuationError(
                f"per_major_units must be >= 1, got {per_major_units}; it is the spend "
                f"block that earns `points` (1 for '4x per dollar', 150 for "
                f"'5 points per 150 rupees')"
            )
        return (points * self.minor_per_10000(program)) // (
            per_major_units * MINOR_UNITS_PER_MAJOR
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "basis": self.basis,
            "rates": [{"program": p, "minor_per_10000_points": v} for p, v in self.rates],
        }

    def policy_hash(self) -> str:
        """Recorded in the receipt. Not issuer-endorsed — that is the entire point."""
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()


# Each rate is a floor a member can reach without an itinerary-specific assumption.
DEFAULT_VALUATION = PointValuation(
    policy_id="plumbline/valuation/published-redemption-floor/1",
    basis=(
        "Each program is valued at a redemption its issuer publishes and a member can "
        "reach without an award-availability assumption. Membership Rewards at 1.00 cent "
        "per point (Pay with Points for flights on amextravel.com). Ultimate Rewards at "
        "1.00 cent per point (Chase cash-back redemption). HDFC Reward Points at 1.00 "
        "rupee per point (SmartBuy flight and hotel redemption; note HDFC's cashback "
        "redemption is 0.30 rupees and is NOT what this policy uses). Transfer-partner "
        "valuations, commonly quoted well above these, are deliberately excluded: they "
        "depend on a specific itinerary and award seat. This policy belongs to the "
        "cardholder or the agent, never to an issuer."
    ),
    rates=(
        (PROGRAM_HDFC_REWARD_POINTS, 1_000_000),  # 10,000 RP = ₹10,000 = 1,000,000 paise
        (PROGRAM_MEMBERSHIP_REWARDS, 10_000),  # 10,000 MR = $100.00 = 10,000 cents
        (PROGRAM_MEMBERSHIP_REWARDS_INDIA, 250_000),  # 10,000 MR = ₹2,500 = 250,000 paise
        (PROGRAM_ULTIMATE_REWARDS, 10_000),  # 10,000 UR = $100.00 = 10,000 cents
    ),
)

# Stated here rather than buried in the basis string, because it is the single most
# consequential asymmetry in this policy and it decides rankings.
#
# HDFC Reward Points are valued at ₹1.00, their published SmartBuy flight-and-hotel
# redemption. Amex India Membership Rewards are valued at ₹0.25, the published Pay with
# Points rate that applies to any spend. Those are the two programmes' floors under the
# same rule — a redemption the issuer publishes and a member can reach without an
# award-availability assumption — but they are reached through different channels, and
# HDFC's is the richer one. Amex India publishes voucher redemptions above ₹0.25; they are
# excluded here for exactly the reason transfer partners are excluded from the US rates,
# namely that they need a catalogue assumption this system does not have.
#
# The consequence is that a card earning 5 RP per ₹150 outranks one earning 1 MR per ₹40 on
# raw arithmetic. That result belongs to this policy, not to any issuer: the policy hash
# travels in every receipt, and a reader who prices a point differently gets a different
# ranking from the same signed facts. That is the mechanism working, not a defect in it.
POINT_VALUATION_ASYMMETRY = (
    "HDFC Reward Points are priced at their SmartBuy travel redemption (₹1.00) and Amex "
    "India Membership Rewards at their Pay with Points rate (₹0.25). Both are published "
    "floors reachable without an award-availability assumption, and they are not equally "
    "generous. The ranking that follows is the cardholder's policy, recorded by hash and "
    "endorsed by no issuer."
)


def _as_of(issued_at: int) -> str:
    return datetime.fromtimestamp(issued_at, tz=timezone.utc).strftime("%Y-%m-%d")


def _source(issued_at: int) -> str:
    return SOURCE_PUBLISHED_TERMS.format(as_of=_as_of(issued_at))


def _earn(
    benefit_id: str,
    label: str,
    *,
    rate_bp: int,
    group: str,
    categories: Sequence[str] = (),
    capacity_minor: int | None = None,
    window: str = WINDOW_NONE,
    note: str = "",
) -> Benefit:
    return Benefit(
        benefit_id=benefit_id,
        kind=KIND_EARN,
        label=label,
        eligibility=Eligibility(categories=tuple(categories)),
        rate_bp=rate_bp,
        capacity_minor=capacity_minor,
        exclusivity_group=group,
        window=window,
        note=note,
    )


def _credit(
    benefit_id: str,
    label: str,
    *,
    capacity_minor: int,
    categories: Sequence[str],
    window: str,
    group: str | None = None,
    requires_enrollment: bool = True,
    enrolled: bool = True,
    note: str = "",
) -> Benefit:
    return Benefit(
        benefit_id=benefit_id,
        kind=KIND_CREDIT,
        label=label,
        eligibility=Eligibility(categories=tuple(categories)),
        capacity_minor=capacity_minor,
        exclusivity_group=group,
        window=window,
        requires_enrollment=requires_enrollment,
        enrolled=enrolled,
        note=note or SYNTHETIC_STATE,
    )


def _unpriced(benefit_id: str, label: str, note: str) -> Benefit:
    return Benefit(benefit_id=benefit_id, kind=KIND_UNPRICED, label=label, note=note)


# --------------------------------------------------------------------------------------
# 1. The Platinum Card from American Express (US)
#
# Published terms read for this manifest, all current for the 2026 card:
#   annual fee                $895
#   5x Membership Rewards     flights booked direct with the airline or on amextravel.com,
#                             on up to $500,000 of such spend per calendar year, then 1x
#   5x Membership Rewards     prepaid hotels booked on amextravel.com
#   1x Membership Rewards     everything else
#   $600 hotel credit         two $300 semi-annual credits (Jan–Jun, Jul–Dec) on prepaid
#                             Fine Hotels + Resorts or The Hotel Collection bookings
#   $400 Resy credit          up to $100 per calendar quarter, enrollment required
#   $200 airline fee credit   incidental fees on one selected airline, enrollment required
#   $300 digital entertainment  $25 per month, enrollment required
#   $200 Uber Cash            $15 per month, $35 in December, enrollment required
#   $300 lululemon credit     $75 per calendar quarter, enrollment required
#   FHR property credit       $100 on-property experience credit per qualifying stay
# --------------------------------------------------------------------------------------

AMEX_PLATINUM_ID = "amex-platinum-us-2026"
_PLAT_EARN = "amex_plat:earn"
_PLAT_HOTEL_CREDIT = "amex_plat:hotel_credit"
_PLAT_RESY_CREDIT = "amex_plat:resy_credit"


def amex_platinum_us(
    issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> Manifest:
    """The Platinum Card from American Express, US, 2026 terms.

    The three earn benefits share one exclusivity group because 5x REPLACES the 1x base
    rate; it is not added to it. Modelling them as additive is the single most common way a
    per-line valuation overstates a premium card, and it is the first thing the
    overstatement demo shows.
    """
    mr = PROGRAM_MEMBERSHIP_REWARDS
    rate_5x = valuation.earn_rate_bp(mr, 5)
    rate_1x = valuation.earn_rate_bp(mr, 1)
    # 5x on up to $500,000 of flights per calendar year. The manifest carries earn caps as
    # VALUE headroom, so the published spend cap converts once, here: 50,000,000 cents of
    # qualifying spend at `rate_5x` basis points.
    flight_cap_value = (50_000_000 * rate_5x) // 10_000

    benefits = [
        _earn(
            "amex_plat_earn_flights_5x",
            "5x Membership Rewards on flights booked direct or on amextravel.com",
            rate_bp=rate_5x,
            group=_PLAT_EARN,
            categories=(CAT_AIRFARE,),
            capacity_minor=flight_cap_value,
            window=WINDOW_ANNUAL,
            note=(
                f"published cap is $500,000 of qualifying flight spend per calendar year, "
                f"carried here as {fmt_currency(flight_cap_value, USD)} of value headroom; "
                f"{SYNTHETIC_STATE} shows the cap untouched"
            ),
        ),
        _earn(
            "amex_plat_earn_hotels_5x",
            "5x Membership Rewards on prepaid hotels booked on amextravel.com",
            rate_bp=rate_5x,
            group=_PLAT_EARN,
            categories=(CAT_PREPAID_HOTEL_AMEX,),
        ),
        _earn(
            "amex_plat_earn_base_1x",
            "1x Membership Rewards on all other purchases",
            rate_bp=rate_1x,
            group=_PLAT_EARN,
            note="shares an exclusivity group with the bonus rates: 5x replaces 1x, never adds to it",
        ),
        _credit(
            "amex_plat_credit_hotel_h1",
            "$300 prepaid hotel credit, January–June",
            capacity_minor=0,
            categories=(CAT_PREPAID_HOTEL_AMEX,),
            window=WINDOW_SEMIANNUAL,
            group=_PLAT_HOTEL_CREDIT,
            requires_enrollment=False,
            note=WINDOW_CLOSED_NOTE.format(window="January–June", state=SYNTHETIC_STATE),
        ),
        _credit(
            "amex_plat_credit_hotel_h2",
            "$300 prepaid hotel credit, July–December",
            capacity_minor=30_000,  # $300.00 — the live half of the published $600
            categories=(CAT_PREPAID_HOTEL_AMEX,),
            window=WINDOW_SEMIANNUAL,
            group=_PLAT_HOTEL_CREDIT,
            requires_enrollment=False,
            note=(
                f"{SYNTHETIC_STATE}: full $300 remaining. The two halves share an "
                f"exclusivity group so one booking can never draw both — the published "
                f"$600 is two windows, not one balance"
            ),
        ),
        _credit(
            "amex_plat_credit_resy_q2",
            "$100 Resy credit, April–June quarter",
            capacity_minor=0,
            categories=(CAT_RESY_DINING,),
            window=WINDOW_QUARTERLY,
            group=_PLAT_RESY_CREDIT,
            note=WINDOW_CLOSED_NOTE.format(window="April–June", state=SYNTHETIC_STATE),
        ),
        _credit(
            "amex_plat_credit_resy_q3",
            "$100 Resy credit, July–September quarter",
            capacity_minor=10_000,  # $100.00 — one quarter of the published $400
            categories=(CAT_RESY_DINING,),
            window=WINDOW_QUARTERLY,
            group=_PLAT_RESY_CREDIT,
            note=f"{SYNTHETIC_STATE}: full $100 remaining in the live quarter",
        ),
        _credit(
            "amex_plat_credit_airline_fee",
            "$200 airline fee credit on the selected airline",
            capacity_minor=20_000,  # $200.00 per calendar year
            categories=(CAT_AIRLINE_INCIDENTAL,),
            window=WINDOW_ANNUAL,
            note=(
                f"{SYNTHETIC_STATE}: full $200 remaining. Covers incidental fees — checked "
                f"bags, seat selection — and not the fare itself, which is why it does not "
                f"admit an airfare line"
            ),
        ),
        _credit(
            "amex_plat_credit_digital_entertainment",
            "$25 monthly digital entertainment credit",
            capacity_minor=2_500,  # $25.00 per month of the published $300 per year
            categories=(CAT_DIGITAL_ENTERTAINMENT,),
            window=WINDOW_MONTHLY,
            note=f"{SYNTHETIC_STATE}: full $25 remaining this month",
        ),
        _credit(
            "amex_plat_credit_uber_cash",
            "$15 monthly Uber Cash",
            capacity_minor=1_500,  # $15.00/month; the published $200/year includes $35 in December
            categories=(CAT_UBER,),
            window=WINDOW_MONTHLY,
            note=f"{SYNTHETIC_STATE}: full $15 remaining this month",
        ),
        _credit(
            "amex_plat_credit_lululemon",
            "$75 quarterly lululemon credit",
            capacity_minor=7_500,  # $75.00 per quarter of the published $300 per year
            categories=(CAT_LULULEMON,),
            window=WINDOW_QUARTERLY,
            enrolled=False,
            note=NOT_ENROLLED_NOTE.format(state=SYNTHETIC_STATE),
        ),
        Benefit(
            benefit_id="amex_plat_fhr_property_credit",
            kind=KIND_PROTECTION,
            label="$100 Fine Hotels + Resorts property credit",
            eligibility=Eligibility(categories=(CAT_PREPAID_HOTEL_AMEX,)),
            flat_minor=10_000,  # $100.00, granted once per qualifying stay
            capacity_minor=10_000,
            note=(
                "published as a flat on-property credit on a qualifying Fine Hotels + "
                "Resorts booking, so it is a definite amount rather than contingent cover. "
                "Capacity equals the flat amount because it is granted once per stay. It "
                "is realized only if spent on property; that haircut belongs to the "
                "valuation policy, not to the issuer's signature"
            ),
        ),
        _unpriced(
            "amex_plat_lounge_access",
            "Centurion Lounge, Priority Pass Select and Delta Sky Club access",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "amex_plat_hotel_status",
            "Hilton Honors Gold and Marriott Bonvoy Gold status",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "amex_plat_service",
            "Platinum Concierge, Global Dining Access by Resy, Fine Hotels + Resorts room "
            "upgrade, daily breakfast for two and late checkout",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "amex_plat_protections",
            "Purchase protection, extended warranty, return protection, trip cancellation "
            "and interruption insurance, trip delay insurance, baggage insurance and cell "
            "phone protection",
            UNPRICED_RATIONALE_CONTINGENT,
        ),
        _unpriced(
            "amex_plat_offers",
            "Amex Offers",
            UNPRICED_RATIONALE_NO_FEED,
        ),
    ]
    return build_manifest(
        manifest_id=AMEX_PLATINUM_ID,
        issuer="American Express",
        product="The Platinum Card (US)",
        benefits=benefits,
        issued_at=issued_at,
        currency=USD,
        source=_source(issued_at),
    )


# --------------------------------------------------------------------------------------
# 2. American Express Gold Card (US)
#
# Published terms read for this manifest, current after the April 2026 refresh:
#   annual fee                $325
#   4x Membership Rewards     restaurants worldwide, up to $50,000 per calendar year, then 1x
#   4x Membership Rewards     US supermarkets, up to $25,000 per calendar year, then 1x
#   5x Membership Rewards     prepaid hotels booked on amextravel.com
#   3x Membership Rewards     flights booked direct with the airline or on amextravel.com
#   1x Membership Rewards     everything else
#   $120 dining credit        $10 per month at Grubhub, Five Guys, Cheesecake Factory,
#                             Buffalo Wild Wings and Wonder; enrollment required
#   $120 Uber Cash            $10 per month; enrollment required
#   $100 Resy credit          $50 each half of the calendar year; enrollment required
#   $84 Dunkin' credit        $7 per month; enrollment required
#   $100 Hotel Collection     experience credit on a qualifying stay of two nights or more
# --------------------------------------------------------------------------------------

AMEX_GOLD_ID = "amex-gold-us-2026"
_GOLD_EARN = "amex_gold:earn"


def amex_gold_us(issued_at: int, valuation: PointValuation = DEFAULT_VALUATION) -> Manifest:
    """American Express Gold Card, US, 2026 terms.

    This is the manifest that exercises a partially-consumed annual cap. Both 4x categories
    carry synthetic mid-year headroom, and an earn benefit is never applied fractionally, so
    a line whose bonus would exceed the remaining headroom falls back to the 1x base rate
    rather than being prorated. That understates and is safe.
    """
    mr = PROGRAM_MEMBERSHIP_REWARDS
    rate_5x = valuation.earn_rate_bp(mr, 5)
    rate_4x = valuation.earn_rate_bp(mr, 4)
    rate_3x = valuation.earn_rate_bp(mr, 3)
    rate_1x = valuation.earn_rate_bp(mr, 1)

    # Synthetic mid-year position: $31,400 of the $50,000 restaurant cap and $9,700 of the
    # $25,000 supermarket cap already spent. Headroom is carried as value, not as spend.
    restaurant_headroom = ((5_000_000 - 3_140_000) * rate_4x) // 10_000
    supermarket_headroom = ((2_500_000 - 970_000) * rate_4x) // 10_000

    benefits = [
        _earn(
            "amex_gold_earn_restaurants_4x",
            "4x Membership Rewards at restaurants worldwide",
            rate_bp=rate_4x,
            group=_GOLD_EARN,
            categories=DINING_CATEGORIES,
            capacity_minor=restaurant_headroom,
            window=WINDOW_ANNUAL,
            note=(
                f"published cap is $50,000 of restaurant spend per calendar year, then 1x. "
                f"{SYNTHETIC_STATE}: $31,400 already spent, leaving "
                f"{fmt_currency(restaurant_headroom, USD)} of value headroom"
            ),
        ),
        _earn(
            "amex_gold_earn_supermarkets_4x",
            "4x Membership Rewards at US supermarkets",
            rate_bp=rate_4x,
            group=_GOLD_EARN,
            categories=(CAT_US_SUPERMARKET,),
            capacity_minor=supermarket_headroom,
            window=WINDOW_ANNUAL,
            note=(
                f"published cap is $25,000 of US supermarket spend per calendar year, then "
                f"1x. {SYNTHETIC_STATE}: $9,700 already spent, leaving "
                f"{fmt_currency(supermarket_headroom, USD)} of value headroom"
            ),
        ),
        _earn(
            "amex_gold_earn_hotels_5x",
            "5x Membership Rewards on prepaid hotels booked on amextravel.com",
            rate_bp=rate_5x,
            group=_GOLD_EARN,
            categories=(CAT_PREPAID_HOTEL_AMEX,),
        ),
        _earn(
            "amex_gold_earn_flights_3x",
            "3x Membership Rewards on flights booked direct or on amextravel.com",
            rate_bp=rate_3x,
            group=_GOLD_EARN,
            categories=(CAT_AIRFARE,),
        ),
        _earn(
            "amex_gold_earn_base_1x",
            "1x Membership Rewards on all other purchases",
            rate_bp=rate_1x,
            group=_GOLD_EARN,
            note="shares an exclusivity group with the bonus rates: 4x replaces 1x, never adds to it",
        ),
        _credit(
            "amex_gold_credit_dining",
            "$10 monthly dining credit at Grubhub, Five Guys, Cheesecake Factory, Buffalo "
            "Wild Wings and Wonder",
            capacity_minor=1_000,  # $10.00 per month of the published $120 per year
            categories=(CAT_GOLD_DINING_PARTNER,),
            window=WINDOW_MONTHLY,
            note=f"{SYNTHETIC_STATE}: full $10 remaining this month",
        ),
        _credit(
            "amex_gold_credit_uber_cash",
            "$10 monthly Uber Cash",
            capacity_minor=1_000,  # $10.00 per month of the published $120 per year
            categories=(CAT_UBER,),
            window=WINDOW_MONTHLY,
            note=f"{SYNTHETIC_STATE}: full $10 remaining this month",
        ),
        _credit(
            "amex_gold_credit_resy",
            "$50 semi-annual Resy credit",
            capacity_minor=5_000,  # $50.00 per half of the published $100 per year
            categories=(CAT_RESY_DINING,),
            window=WINDOW_SEMIANNUAL,
            note=f"{SYNTHETIC_STATE}: full $50 remaining in the live half",
        ),
        _credit(
            "amex_gold_credit_dunkin",
            "$7 monthly Dunkin' credit",
            capacity_minor=700,  # $7.00 per month of the published $84 per year
            categories=(CAT_DUNKIN,),
            window=WINDOW_MONTHLY,
            note=f"{SYNTHETIC_STATE}: full $7 remaining this month",
        ),
        Benefit(
            benefit_id="amex_gold_the_hotel_collection_credit",
            kind=KIND_PROTECTION,
            label="$100 The Hotel Collection experience credit",
            eligibility=Eligibility(categories=(CAT_PREPAID_HOTEL_AMEX,)),
            flat_minor=10_000,  # $100.00, on a qualifying stay of two nights or more
            capacity_minor=10_000,
            note=(
                "published as a flat experience credit on a qualifying The Hotel Collection "
                "booking of two nights or more. A definite published amount, not contingent "
                "cover, which is why it is priced at all"
            ),
        ),
        _unpriced(
            "amex_gold_no_lounge_access",
            "No airport lounge access",
            "declared so the comparison is honest: the Gold Card carries no lounge benefit, "
            "and a candidate set that silently omitted the absence would flatter it",
        ),
        _unpriced(
            "amex_gold_service",
            "Global Dining Access by Resy and Amex Experiences presale access",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "amex_gold_protections",
            "Purchase protection, extended warranty, return protection, baggage insurance "
            "and trip delay insurance",
            UNPRICED_RATIONALE_CONTINGENT,
        ),
        _unpriced("amex_gold_offers", "Amex Offers", UNPRICED_RATIONALE_NO_FEED),
    ]
    return build_manifest(
        manifest_id=AMEX_GOLD_ID,
        issuer="American Express",
        product="American Express Gold Card (US)",
        benefits=benefits,
        issued_at=issued_at,
        currency=USD,
        source=_source(issued_at),
    )


# --------------------------------------------------------------------------------------
# 3. Chase Sapphire Reserve
#
# Published terms read for this manifest, current for the card as relaunched in 2025:
#   annual fee                $795
#   8x Ultimate Rewards       purchases through Chase Travel
#   4x Ultimate Rewards       flights booked direct with the airline
#   4x Ultimate Rewards       hotels booked direct with the property
#   5x Ultimate Rewards       Lyft rides
#   3x Ultimate Rewards       dining worldwide
#   1x Ultimate Rewards       everything else
#   $300 travel credit        applied to travel purchases each calendar year
#   $500 The Edit credit      up to $250 per prepaid The Edit booking through Chase Travel,
#                             for the first two eligible bookings of the calendar year
#   $300 dining credit        Sapphire Reserve Exclusive Tables, $150 January–June and
#                             $150 July–December
#   $120 Lyft credit          $10 per month in-app
# --------------------------------------------------------------------------------------

CHASE_SAPPHIRE_RESERVE_ID = "chase-sapphire-reserve-2026"
_CSR_EARN = "csr:earn"
_CSR_TRAVEL_CREDIT = "csr:travel_credit"
_CSR_DINING_CREDIT = "csr:dining_credit"


def chase_sapphire_reserve(
    issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> Manifest:
    """Chase Sapphire Reserve, 2026 terms.

    The $300 travel credit and both $250 The Edit credits are modelled as MUTUALLY EXCLUSIVE
    on a single line. Chase's terms may well allow the travel credit and an Edit credit to
    stack on one booking; if they do, this manifest understates the card, and understating is
    the only direction this system is allowed to be wrong in. The choice is recorded here
    rather than buried.

    The per-booking and per-half structure is not a detail. A published annual total is a
    sum over windows or over bookings, and collapsing it into one balance lets a single cart
    line draw the whole year. That overstates, and it overstates a competitor's card, which
    is a way to lose this argument entirely.
    """
    ur = PROGRAM_ULTIMATE_REWARDS
    rate_8x = valuation.earn_rate_bp(ur, 8)
    rate_5x = valuation.earn_rate_bp(ur, 5)
    rate_4x = valuation.earn_rate_bp(ur, 4)
    rate_3x = valuation.earn_rate_bp(ur, 3)
    rate_1x = valuation.earn_rate_bp(ur, 1)

    benefits = [
        _earn(
            "csr_earn_chase_travel_8x",
            "8x Ultimate Rewards on Chase Travel purchases",
            rate_bp=rate_8x,
            group=_CSR_EARN,
            categories=(CAT_CHASE_TRAVEL,),
        ),
        _earn(
            "csr_earn_flights_direct_4x",
            "4x Ultimate Rewards on flights booked direct with the airline",
            rate_bp=rate_4x,
            group=_CSR_EARN,
            categories=(CAT_AIRFARE,),
        ),
        _earn(
            "csr_earn_hotels_direct_4x",
            "4x Ultimate Rewards on hotels booked direct with the property",
            rate_bp=rate_4x,
            group=_CSR_EARN,
            categories=(CAT_HOTEL_DIRECT,),
        ),
        _earn(
            "csr_earn_lyft_5x",
            "5x Ultimate Rewards on Lyft rides",
            rate_bp=rate_5x,
            group=_CSR_EARN,
            categories=(CAT_LYFT,),
        ),
        _earn(
            "csr_earn_dining_3x",
            "3x Ultimate Rewards on dining worldwide",
            rate_bp=rate_3x,
            group=_CSR_EARN,
            categories=DINING_CATEGORIES,
        ),
        _earn(
            "csr_earn_base_1x",
            "1x Ultimate Rewards on all other purchases",
            rate_bp=rate_1x,
            group=_CSR_EARN,
            note="shares an exclusivity group with the bonus rates: 8x replaces 1x, never adds to it",
        ),
        _credit(
            "csr_credit_travel",
            "$300 annual travel credit",
            capacity_minor=30_000,  # $300.00 per calendar year
            categories=(
                CAT_AIRFARE,
                CAT_AIRLINE_INCIDENTAL,
                CAT_HOTEL_DIRECT,
                CAT_CHASE_TRAVEL,
            ),
            window=WINDOW_ANNUAL,
            group=_CSR_TRAVEL_CREDIT,
            requires_enrollment=False,
            note=(
                f"{SYNTHETIC_STATE}: full $300 remaining. Applies automatically to travel "
                f"purchases, so no enrollment gate"
            ),
        ),
        # The published $500 is TWO $250 credits, each redeemable against one prepaid The
        # Edit booking — it is not a single $500 balance one booking can drain. Modelling it
        # as one bucket let a single line draw $500, which the card cannot deliver, and an
        # overstatement of a competitor is still an overstatement. Both halves sit in the
        # travel-credit exclusivity group, so one line draws at most one of them; a cart
        # with two Edit bookings can still reach $500.
        _credit(
            "csr_credit_the_edit_1",
            "$250 The Edit hotel credit, first eligible booking",
            capacity_minor=25_000,  # $250.00 — one of the two published per-booking credits
            categories=(CAT_CHASE_TRAVEL,),
            window=WINDOW_ANNUAL,
            group=_CSR_TRAVEL_CREDIT,
            requires_enrollment=False,
            note=(
                f"{SYNTHETIC_STATE}: full $250 remaining. Published as up to $250 per "
                f"prepaid The Edit booking, for the first two eligible bookings of the "
                f"calendar year — $500 is two bookings, not one balance"
            ),
        ),
        _credit(
            "csr_credit_the_edit_2",
            "$250 The Edit hotel credit, second eligible booking",
            capacity_minor=25_000,  # $250.00 — the second per-booking credit
            categories=(CAT_CHASE_TRAVEL,),
            window=WINDOW_ANNUAL,
            group=_CSR_TRAVEL_CREDIT,
            requires_enrollment=False,
            note=(
                f"{SYNTHETIC_STATE}: full $250 remaining. Shares an exclusivity group with "
                f"the other Edit credit and with the $300 travel credit, so no single "
                f"booking draws two of them"
            ),
        ),
        # Published as $150 January–June and $150 July–December, not $300 against one cart.
        # The closed half is declared at zero rather than omitted so the derivation shows
        # the benefit was seen and why it yielded nothing.
        _credit(
            "csr_credit_dining_h1",
            "$150 dining credit at Sapphire Reserve Exclusive Tables, January–June",
            capacity_minor=0,
            categories=(CAT_CSR_EXCLUSIVE_TABLE,),
            window=WINDOW_SEMIANNUAL,
            group=_CSR_DINING_CREDIT,
            requires_enrollment=False,
            note=WINDOW_CLOSED_NOTE.format(window="January–June", state=SYNTHETIC_STATE),
        ),
        _credit(
            "csr_credit_dining_h2",
            "$150 dining credit at Sapphire Reserve Exclusive Tables, July–December",
            capacity_minor=15_000,  # $150.00 — the live half of the published $300
            categories=(CAT_CSR_EXCLUSIVE_TABLE,),
            window=WINDOW_SEMIANNUAL,
            group=_CSR_DINING_CREDIT,
            requires_enrollment=False,
            note=(
                f"{SYNTHETIC_STATE}: full $150 remaining in the live half. The two halves "
                f"share an exclusivity group so one dinner can never draw both"
            ),
        ),
        _credit(
            "csr_credit_lyft",
            "$10 monthly Lyft in-app credit",
            capacity_minor=1_000,  # $10.00 per month of the published $120 per year
            categories=(CAT_LYFT,),
            window=WINDOW_MONTHLY,
            requires_enrollment=False,
            note=f"{SYNTHETIC_STATE}: full $10 remaining this month",
        ),
        _unpriced(
            "csr_lounge_access",
            "Priority Pass Select and Chase Sapphire Lounge by The Club access",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "csr_hotel_status",
            "IHG One Rewards Platinum Elite status",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "csr_exclusive_tables_access",
            "Sapphire Reserve Exclusive Tables peak-hour reservation access",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "csr_protections",
            "Trip cancellation and interruption insurance, trip delay reimbursement, "
            "primary auto rental collision damage waiver, purchase protection and extended "
            "warranty",
            UNPRICED_RATIONALE_CONTINGENT,
        ),
        _unpriced(
            "csr_global_entry_credit",
            "Global Entry, TSA PreCheck or NEXUS application fee credit",
            "declared and considered, deliberately not scored: the published credit runs on "
            "a four-year window, and amortising it into a single cart would put a number on "
            "this receipt that no allocation on this cart could realize",
        ),
    ]
    return build_manifest(
        manifest_id=CHASE_SAPPHIRE_RESERVE_ID,
        issuer="JPMorgan Chase",
        product="Chase Sapphire Reserve",
        benefits=benefits,
        issued_at=issued_at,
        currency=USD,
        source=_source(issued_at),
    )


# --------------------------------------------------------------------------------------
# 4. HDFC Bank Infinia Metal Edition (India)
#
# Published terms read for this manifest, current for 2026:
#   joining / renewal fee     ₹12,500 plus GST, renewal waived at ₹10 lakh of annual spend
#   base earn                 5 Reward Points per ₹150 of eligible retail spend
#   SmartBuy accelerated      up to 10X on hotels and 5X on flights booked through SmartBuy,
#                             under one overall cap of 15,000 Reward Points per calendar
#                             month across all accelerated SmartBuy categories
#   redemption                1 RP = ₹1 against SmartBuy flight and hotel bookings
#   no reward points          wallet loads, fuel, rent, government payments, EMI conversions
#   lounge access             unlimited domestic and international, primary and add-on
# --------------------------------------------------------------------------------------

HDFC_INFINIA_ID = "hdfc-infinia-metal-2026"
_INFINIA_EARN = "hdfc_infinia:earn"

# HDFC awards points in whole ₹150 blocks. `Benefit.value_for_line` applies a linear rate,
# which cannot express block rounding, so the modelled rate can exceed the block-rounded
# figure on a single line — by strictly less than one block's worth of points. Both the
# derivation and that bound are asserted in test_products.py.
HDFC_POINT_BLOCK_MAJOR = 150
HDFC_BASE_POINTS_PER_BLOCK = 5
HDFC_SMARTBUY_HOTEL_MULTIPLE = 10
HDFC_SMARTBUY_FLIGHT_MULTIPLE = 5
HDFC_SMARTBUY_MONTHLY_POINT_CAP = 15_000


def hdfc_infinia(issued_at: int, valuation: PointValuation = DEFAULT_VALUATION) -> Manifest:
    """HDFC Bank Infinia Metal Edition, India, 2026 terms.

    Two modelling decisions worth stating, because both are places a careless manifest would
    overstate:

    The SmartBuy accelerators draw on ONE shared pool of 15,000 Reward Points per calendar
    month. `Benefit.capacity_minor` is per benefit, so splitting that pool across the hotel
    and flight accelerators as two independent capacities would let a cart spend it twice.
    All remaining headroom is therefore carried on the hotel accelerator and the flight
    accelerator is declared with zero headroom at this clock.

    Base earn and the SmartBuy accelerator share an exclusivity group. A SmartBuy booking
    earns the accelerated rate INSTEAD of the base rate, not on top of it.
    """
    rp = PROGRAM_HDFC_REWARD_POINTS
    base_bp = valuation.earn_rate_bp(
        rp, HDFC_BASE_POINTS_PER_BLOCK, per_major_units=HDFC_POINT_BLOCK_MAJOR
    )
    hotel_bp = valuation.earn_rate_bp(
        rp,
        HDFC_BASE_POINTS_PER_BLOCK * HDFC_SMARTBUY_HOTEL_MULTIPLE,
        per_major_units=HDFC_POINT_BLOCK_MAJOR,
    )
    flight_bp = valuation.earn_rate_bp(
        rp,
        HDFC_BASE_POINTS_PER_BLOCK * HDFC_SMARTBUY_FLIGHT_MULTIPLE,
        per_major_units=HDFC_POINT_BLOCK_MAJOR,
    )
    # Synthetic mid-month position: 5,800 of the 15,000 monthly accelerated points drawn.
    smartbuy_headroom = (
        (HDFC_SMARTBUY_MONTHLY_POINT_CAP - 5_800) * valuation.minor_per_10000(rp)
    ) // 10_000

    benefits = [
        _earn(
            "hdfc_infinia_earn_base",
            "5 Reward Points per ₹150 of eligible retail spend",
            rate_bp=base_bp,
            group=_INFINIA_EARN,
            categories=HDFC_REWARDED_CATEGORIES,
            note=(
                f"5 RP per ₹150 at 1 RP = ₹1 on SmartBuy travel redemption is "
                f"{base_bp / 100:g}% and rounds down to {base_bp} bp. Points accrue in whole "
                f"₹150 blocks, which a linear rate cannot express; the modelled rate can "
                f"exceed the block-rounded figure by strictly less than "
                f"{HDFC_BASE_POINTS_PER_BLOCK} RP on one line. Eligibility is an allow-list "
                f"because the published terms pay nothing on "
                f"{', '.join(HDFC_UNREWARDED)}"
            ),
        ),
        _earn(
            "hdfc_infinia_earn_smartbuy_hotel_10x",
            "10X Reward Points on hotels booked through SmartBuy",
            rate_bp=hotel_bp,
            group=_INFINIA_EARN,
            categories=(CAT_SMARTBUY_HOTEL,),
            capacity_minor=smartbuy_headroom,
            window=WINDOW_MONTHLY,
            note=(
                f"10X of the 5 RP per ₹150 base is 50 RP per ₹150, which rounds down to "
                f"{hotel_bp} bp. The published cap is {HDFC_SMARTBUY_MONTHLY_POINT_CAP:,} "
                f"accelerated Reward Points per calendar month, shared across SmartBuy "
                f"categories. {SYNTHETIC_STATE}: 5,800 already drawn, leaving "
                f"{fmt_currency(smartbuy_headroom, INR)}"
            ),
        ),
        _earn(
            "hdfc_infinia_earn_smartbuy_flight_5x",
            "5X Reward Points on flights booked through SmartBuy",
            rate_bp=flight_bp,
            group=_INFINIA_EARN,
            categories=(CAT_SMARTBUY_FLIGHT,),
            capacity_minor=0,
            window=WINDOW_MONTHLY,
            note=(
                f"5X of the base is 25 RP per ₹150, which rounds down to {flight_bp} bp. "
                f"Declared with zero headroom because the monthly accelerated pool is SHARED "
                f"with the hotel accelerator and is carried there in full: giving each "
                f"accelerator its own copy of one pool would let a cart spend it twice"
            ),
        ),
        _unpriced(
            "hdfc_infinia_lounge_access",
            "Unlimited domestic and international airport lounge access via Priority Pass, "
            "for the primary and add-on cardholders, with no spend condition",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "hdfc_infinia_forex",
            "2% foreign currency markup",
            "declared and considered, deliberately not scored: a markup is a cost avoided "
            "relative to another card, not value this cart realizes, and netting it here "
            "would make the number a comparison rather than an allocation",
        ),
        _unpriced(
            "hdfc_infinia_fee_waiver",
            "Renewal fee waived at ₹10 lakh of annual spend",
            "declared and considered, deliberately not scored: it turns on a full year of "
            "spend, and no allocation on one cart can realize it",
        ),
        _unpriced(
            "hdfc_infinia_service",
            "24x7 concierge and Club Marriott membership",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
    ]
    return build_manifest(
        manifest_id=HDFC_INFINIA_ID,
        issuer="HDFC Bank",
        product="Infinia Metal Edition",
        benefits=benefits,
        issued_at=issued_at,
        currency=INR,
        source=_source(issued_at),
    )


# --------------------------------------------------------------------------------------
# 5. The Platinum Card from American Express (India)
#
# Published terms read for this manifest, from americanexpress.com/in, current for 2026:
#   annual fee                INR 66,000 plus applicable taxes
#   1 Membership Rewards      per INR 40 spent, excluding fuel, insurance, utilities, cash
#                             transactions and EMI conversion at point of sale
#   5 Membership Rewards      per INR 100 spent on fuel, capped at 5,000 Points per
#                             calendar month
#   3X Membership Rewards     on spend incurred abroad
#   Taj Epicure Plus          a MEMBERSHIP the Cardmember enrols into. It delivers
#                             percentage discounts — 25% off food and beverage at
#                             participating Taj, SeleQtions, Vivanta, Gateway and Claridges
#                             hotels, 25% off Qmin delivery, 20% off Jiva Spa. It is NOT a
#                             rupee credit and no rupee figure is published for it.
#   welcome gift              on net spend of INR 50,000 in the first two months
#   renewal benefit           vouchers up to INR 35,000 on INR 20 lakh of spend in the
#                             membership year
#   lounge access             1,550+ lounges, including Centurion and Priority Pass
#
# The one term this card is most often misquoted on is Taj Epicure, and the misquote is
# always the same shape: a membership reported as a rupee credit. It is declared here as
# CONSIDERED_BUT_UNPRICED with the correction spelled out, because a judge who recognises
# their own product's terms and finds them wrong has been handed a reason to disbelieve
# every other number on the screen.
# --------------------------------------------------------------------------------------

AMEX_PLATINUM_INDIA_ID = "amex-platinum-charge-in-2026"
_PLAT_IN_EARN = "amex_in_plat:earn"

# Published: 5 Membership Rewards Points per INR 100 of fuel spend, capped at 5,000 Points
# per calendar month. The cap is on POINTS, so it converts to value exactly once, here.
AMEX_IN_FUEL_POINTS_PER_BLOCK = 5
AMEX_IN_FUEL_BLOCK_MAJOR = 100
AMEX_IN_FUEL_MONTHLY_POINT_CAP = 5_000
AMEX_IN_FUEL_POINTS_DRAWN = 2_000  # synthetic mid-month position

AMEX_IN_BASE_POINTS_PER_BLOCK = 1
AMEX_IN_BASE_BLOCK_MAJOR = 40
AMEX_IN_OVERSEAS_MULTIPLE = 3


def amex_platinum_charge_india(
    issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> Manifest:
    """The Platinum Card from American Express, India, 2026 terms.

    Three earn benefits in one exclusivity group, for the same reason the US Platinum has
    three in one: the overseas 3X replaces the base rate rather than adding to it, and the
    fuel rate applies where the base rate is excluded. Modelling any of them as additive
    would overstate, and overstating is the one error this system may never make.

    Almost everything this card is bought for is CONSIDERED_BUT_UNPRICED — the lounges, the
    Taj Epicure Plus membership, the hotel programmes, the elite tiers, the insurance. That
    is not an omission. A ₹66,000 card whose priced arithmetic is 0.62% of spend is exactly
    the case the unpriced declaration exists for: the receipt proves the agent saw all of
    it, and the integer never claims to be the whole worth of the card.
    """
    mr = PROGRAM_MEMBERSHIP_REWARDS_INDIA
    base_bp = valuation.earn_rate_bp(
        mr, AMEX_IN_BASE_POINTS_PER_BLOCK, per_major_units=AMEX_IN_BASE_BLOCK_MAJOR
    )
    overseas_bp = valuation.earn_rate_bp(
        mr,
        AMEX_IN_BASE_POINTS_PER_BLOCK * AMEX_IN_OVERSEAS_MULTIPLE,
        per_major_units=AMEX_IN_BASE_BLOCK_MAJOR,
    )
    fuel_bp = valuation.earn_rate_bp(
        mr, AMEX_IN_FUEL_POINTS_PER_BLOCK, per_major_units=AMEX_IN_FUEL_BLOCK_MAJOR
    )
    fuel_headroom = (
        (AMEX_IN_FUEL_MONTHLY_POINT_CAP - AMEX_IN_FUEL_POINTS_DRAWN)
        * valuation.minor_per_10000(mr)
    ) // 10_000

    benefits = [
        _earn(
            "amex_in_plat_earn_base",
            "1 Membership Rewards point for every ₹40 spent",
            rate_bp=base_bp,
            group=_PLAT_IN_EARN,
            categories=AMEX_IN_REWARDED_CATEGORIES,
            note=(
                f"1 MR per ₹40 at ₹0.25 per point is {base_bp / 100:g}% and rounds down to "
                f"{base_bp} bp. Eligibility is an allow-list because the published terms "
                f"exclude {', '.join(AMEX_IN_UNREWARDED)}; fuel is excluded here and carries "
                f"its own published rate below"
            ),
        ),
        _earn(
            "amex_in_plat_earn_overseas_3x",
            "3X Membership Rewards points on spend incurred abroad",
            rate_bp=overseas_bp,
            group=_PLAT_IN_EARN,
            categories=(CAT_OVERSEAS,),
            note=(
                "shares an exclusivity group with the base rate: 3X replaces 1X and is never "
                "added to it. Overseas-ness is carried on the cart line, so the manifest "
                "declares no condition its own vocabulary cannot express"
            ),
        ),
        _earn(
            "amex_in_plat_earn_fuel",
            "5 Membership Rewards points for every ₹100 spent on fuel",
            rate_bp=fuel_bp,
            group=_PLAT_IN_EARN,
            categories=(CAT_FUEL,),
            capacity_minor=fuel_headroom,
            window=WINDOW_MONTHLY,
            note=(
                f"published cap is {AMEX_IN_FUEL_MONTHLY_POINT_CAP:,} Membership Rewards "
                f"Points per calendar month, carried here as value headroom. "
                f"{SYNTHETIC_STATE}: {AMEX_IN_FUEL_POINTS_DRAWN:,} points already drawn, "
                f"leaving {fmt_currency(fuel_headroom, INR)}. An earn benefit is never "
                f"applied fractionally, so a fuel line whose earn exceeds the headroom is "
                f"dropped rather than clipped, which understates"
            ),
        ),
        _unpriced(
            "amex_in_plat_taj_epicure",
            "Taj Epicure Plus membership",
            "declared and considered, deliberately not scored, and stated precisely because "
            "it is the term this card is most often misquoted on: Taj Epicure Plus is a "
            "MEMBERSHIP the Cardmember enrols into, not a rupee credit. It delivers "
            "percentage privileges — 25% off food and beverage at participating Taj, "
            "SeleQtions, Vivanta, Gateway and Claridges hotels, 25% off Qmin delivery, 20% "
            "off Jiva Spa treatments. American Express publishes no rupee value for it, so "
            "this manifest asserts none",
        ),
        _unpriced(
            "amex_in_plat_district_dining_offer",
            "25% off dining on District by Zomato, Wednesdays",
            "declared and considered, deliberately not scored: the published offer is 25% "
            "off up to ₹5,000 per transaction and ₹10,000 per calendar month, but it "
            "requires a coupon code, runs only on one weekday and is time-boxed to a promo "
            "window. This manifest's vocabulary can express none of those three conditions, "
            "and a benefit priced without the conditions that gate it would overstate",
        ),
        _unpriced(
            "amex_in_plat_lounge_access",
            "1,550+ airport lounges worldwide, including The Centurion Lounge, Delta Sky "
            "Club and Priority Pass",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "amex_in_plat_hotel_programmes",
            "Fine Hotels + Resorts, The Hotel Collection, Taj and Oberoi partner rates, and "
            "Marriott Bonvoy and Hilton Honors Gold status",
            "declared and considered, deliberately not scored: the published figures are "
            "either an average value across stays or a discount off a rate this cart does "
            "not carry, and neither is an amount an allocation on this cart could realize",
        ),
        _unpriced(
            "amex_in_plat_milestones",
            "Welcome gift on ₹50,000 of net spend in the first two months, and renewal "
            "vouchers up to ₹35,000 on ₹20 lakh of spend in the membership year",
            "declared and considered, deliberately not scored: both turn on a spend total "
            "over a membership period, and no allocation on one cart can realize them",
        ),
        _unpriced(
            "amex_in_plat_rewards_xcelerator",
            "Rewards Xcelerator, up to 20X Membership Rewards at partner brands",
            UNPRICED_RATIONALE_NO_FEED,
        ),
        _unpriced(
            "amex_in_plat_protections",
            "Air accident cover, purchase protection, overseas medical and emergency "
            "assistance cover",
            UNPRICED_RATIONALE_CONTINGENT,
        ),
        _unpriced("amex_in_plat_offers", "Amex Offers", UNPRICED_RATIONALE_NO_FEED),
    ]
    return build_manifest(
        manifest_id=AMEX_PLATINUM_INDIA_ID,
        issuer="American Express",
        product="The Platinum Card (India)",
        benefits=benefits,
        issued_at=issued_at,
        currency=INR,
        source=_source(issued_at),
    )


# --------------------------------------------------------------------------------------
# 6. American Express Platinum Travel Credit Card (India)
#
# Published terms read for this manifest, from americanexpress.com/in, current for 2026:
#   first-year fee            INR 5,000 plus applicable taxes
#   renewal fee               INR 5,000 plus applicable taxes
#   1 Membership Rewards      per INR 50 spent, excluding fuel, insurance, utilities, cash
#                             transactions and EMI conversion at point of sale
#   welcome gift              10,000 Membership Rewards Points on INR 15,000 of spend in
#                             the first 90 days
#   milestones                7,500 bonus Points at INR 1.9 lakh, 10,000 at INR 4 lakh, and
#                             22,500 plus a Taj e-gift card worth INR 10,000 at INR 7 lakh,
#                             each measured over the membership year
#   lounge access             8 complimentary domestic visits a year, two per quarter, plus
#                             a complimentary Priority Pass membership
#
# Everything this card is marketed on is a milestone measured over a membership year. None
# of it can be realized by an allocation on one cart, so none of it is priced — which is
# precisely why this card is in the candidate set: it is the honest case where the priced
# arithmetic and the reason a member holds the card come apart.
# --------------------------------------------------------------------------------------

AMEX_PLATINUM_TRAVEL_INDIA_ID = "amex-platinum-travel-in-2026"
_PLAT_TRAVEL_IN_EARN = "amex_in_travel:earn"

AMEX_IN_TRAVEL_POINTS_PER_BLOCK = 1
AMEX_IN_TRAVEL_BLOCK_MAJOR = 50


def amex_platinum_travel_india(
    issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> Manifest:
    """American Express Platinum Travel Credit Card, India, 2026 terms."""
    mr = PROGRAM_MEMBERSHIP_REWARDS_INDIA
    base_bp = valuation.earn_rate_bp(
        mr, AMEX_IN_TRAVEL_POINTS_PER_BLOCK, per_major_units=AMEX_IN_TRAVEL_BLOCK_MAJOR
    )

    benefits = [
        _earn(
            "amex_in_travel_earn_base",
            "1 Membership Rewards point for every ₹50 spent",
            rate_bp=base_bp,
            group=_PLAT_TRAVEL_IN_EARN,
            categories=AMEX_IN_REWARDED_CATEGORIES,
            note=(
                f"1 MR per ₹50 at ₹0.25 per point is {base_bp / 100:g}% and rounds down to "
                f"{base_bp} bp. Eligibility is an allow-list because the published terms "
                f"exclude {', '.join(AMEX_IN_UNREWARDED)}. This card publishes no fuel rate, "
                f"so fuel earns nothing here"
            ),
        ),
        _unpriced(
            "amex_in_travel_milestones",
            "7,500 bonus Membership Rewards Points at ₹1.9 lakh of spend, 10,000 at ₹4 "
            "lakh, and 22,500 plus a Taj e-gift card worth ₹10,000 at ₹7 lakh",
            "declared and considered, deliberately not scored: every threshold is measured "
            "over a membership year, and no allocation on one cart can realize any of them. "
            "This is the largest published number on the card and pricing it against a "
            "single cart would be the clearest possible way to overstate",
        ),
        _unpriced(
            "amex_in_travel_welcome",
            "Welcome gift of 10,000 Membership Rewards Points on ₹15,000 of spend in the "
            "first 90 days",
            "declared and considered, deliberately not scored: it is a one-time event over "
            "an enrolment window, not value this cart realizes",
        ),
        _unpriced(
            "amex_in_travel_lounge_access",
            "8 complimentary domestic lounge visits a year, limited to two per quarter, "
            "plus a complimentary Priority Pass membership",
            UNPRICED_RATIONALE_NON_NUMERIC,
        ),
        _unpriced(
            "amex_in_travel_protections",
            "Travel and purchase protections",
            UNPRICED_RATIONALE_CONTINGENT,
        ),
        _unpriced("amex_in_travel_offers", "Amex Offers", UNPRICED_RATIONALE_NO_FEED),
    ]
    return build_manifest(
        manifest_id=AMEX_PLATINUM_TRAVEL_INDIA_ID,
        issuer="American Express",
        product="Platinum Travel Credit Card (India)",
        benefits=benefits,
        issued_at=issued_at,
        currency=INR,
        source=_source(issued_at),
    )


# --------------------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductProfile:
    """A product's identity and the facts that sit outside any one cart.

    The annual fee lives here and never inside a manifest, because a benefit is something a
    cart can realize and a fee is not. A per-cart valuation that quietly netted the fee
    would be answering a question nobody asked.
    """

    manifest_id: str
    issuer: str
    product: str
    currency: str
    annual_fee_minor: int
    fee_note: str
    build: Callable[..., Manifest]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "issuer": self.issuer,
            "product": self.product,
            "currency": self.currency,
            "annual_fee_minor": self.annual_fee_minor,
            "annual_fee_display": fmt_currency(self.annual_fee_minor, self.currency),
            "fee_note": self.fee_note,
        }


PROFILES: tuple[ProductProfile, ...] = (
    ProductProfile(
        manifest_id=AMEX_PLATINUM_ID,
        issuer="American Express",
        product="The Platinum Card (US)",
        currency=USD,
        annual_fee_minor=89_500,  # $895.00
        fee_note="published annual fee; never netted against a cart valuation",
        build=amex_platinum_us,
    ),
    ProductProfile(
        manifest_id=AMEX_GOLD_ID,
        issuer="American Express",
        product="American Express Gold Card (US)",
        currency=USD,
        annual_fee_minor=32_500,  # $325.00
        fee_note="published annual fee; never netted against a cart valuation",
        build=amex_gold_us,
    ),
    ProductProfile(
        manifest_id=CHASE_SAPPHIRE_RESERVE_ID,
        issuer="JPMorgan Chase",
        product="Chase Sapphire Reserve",
        currency=USD,
        annual_fee_minor=79_500,  # $795.00
        fee_note="published annual fee; never netted against a cart valuation",
        build=chase_sapphire_reserve,
    ),
    ProductProfile(
        manifest_id=HDFC_INFINIA_ID,
        issuer="HDFC Bank",
        product="Infinia Metal Edition",
        currency=INR,
        annual_fee_minor=1_250_000,  # ₹12,500.00, before GST
        fee_note=(
            "published joining and renewal fee, before GST; waived at ₹10 lakh of annual "
            "spend. Never netted against a cart valuation"
        ),
        build=hdfc_infinia,
    ),
    ProductProfile(
        manifest_id=AMEX_PLATINUM_INDIA_ID,
        issuer="American Express",
        product="The Platinum Card (India)",
        currency=INR,
        annual_fee_minor=6_600_000,  # ₹66,000.00, before applicable taxes
        fee_note=(
            "published annual fee, before applicable taxes. There is no spend-based waiver. "
            "Never netted against a cart valuation"
        ),
        build=amex_platinum_charge_india,
    ),
    ProductProfile(
        manifest_id=AMEX_PLATINUM_TRAVEL_INDIA_ID,
        issuer="American Express",
        product="Platinum Travel Credit Card (India)",
        currency=INR,
        annual_fee_minor=500_000,  # ₹5,000.00, before applicable taxes
        fee_note=(
            "published first-year and renewal fee, before applicable taxes. Never netted "
            "against a cart valuation"
        ),
        build=amex_platinum_travel_india,
    ),
)

PROFILE_BY_ID: Mapping[str, ProductProfile] = {p.manifest_id: p for p in PROFILES}


def profile(manifest_id: str) -> ProductProfile:
    try:
        return PROFILE_BY_ID[manifest_id]
    except KeyError as exc:
        raise ManifestError(
            f"no product profile for {manifest_id!r}; known: "
            f"{', '.join(sorted(PROFILE_BY_ID))}"
        ) from exc


def catalogue(
    issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> tuple[Manifest, ...]:
    """Every modelled product, in declaration order. Deterministic for a fixed clock."""
    return tuple(p.build(issued_at, valuation) for p in PROFILES)


def catalogue_by_id(
    issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> dict[str, Manifest]:
    return {m.manifest_id: m for m in catalogue(issued_at, valuation)}


def catalogue_for_currency(
    currency: str, issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> tuple[Manifest, ...]:
    """Products denominated in one currency.

    Ranking instruments across currencies would need an FX rate, which is a market price
    this system does not carry and would be pretending to know. A candidate set is
    therefore always single-currency.
    """
    if currency not in CURRENCY_SYMBOL:
        raise ManifestError(
            f"unknown currency {currency!r}; known: {', '.join(sorted(CURRENCY_SYMBOL))}"
        )
    return tuple(m for m in catalogue(issued_at, valuation) if m.currency == currency)


def sign_catalogue(
    issued_at: int,
    key: str | bytes,
    valuation: PointValuation = DEFAULT_VALUATION,
    key_id: str = "prototype-issuer",
) -> tuple[SignedManifest, ...]:
    """Sign every manifest under one prototype key.

    Production signs each issuer's manifest with that issuer's own key in an HSM. One key
    here is a demo convenience and nothing about canonicalisation or verification changes.
    """
    return tuple(sign_manifest(m, key, key_id=key_id) for m in catalogue(issued_at, valuation))


def unpriced_declarations(manifest: Manifest) -> tuple[dict[str, str], ...]:
    """The CONSIDERED_BUT_UNPRICED entries, shaped for a receipt.

    A receipt carries these so it can prove the agent saw the lounge, the concierge and the
    insurance, and that the integer never claimed to be the whole worth of the card.
    """
    return tuple(
        {"benefit_id": b.benefit_id, "label": b.label, "rationale": b.note}
        for b in manifest.unpriced()
    )


def describe_catalogue(
    issued_at: int, valuation: PointValuation = DEFAULT_VALUATION
) -> list[str]:
    """One line per product, for a terminal or a console panel."""
    out = []
    for manifest in catalogue(issued_at, valuation):
        prof = profile(manifest.manifest_id)
        out.append(
            f"{manifest.issuer} {manifest.product} — {len(manifest.priced())} priced, "
            f"{len(manifest.unpriced())} unpriced, fee "
            f"{fmt_currency(prof.annual_fee_minor, manifest.currency)}"
        )
    return out
