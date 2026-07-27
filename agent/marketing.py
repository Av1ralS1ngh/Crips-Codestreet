"""Product marketing copy — the only value signal an agent has without a value rail.

This module exists to make the control run honest. If the copy here were a strawman, the
control agent's mistake would be our fault rather than the ecosystem's, and the whole
contrast would be worthless.

So the rules this file follows:

  * Every headline figure is one the issuer itself puts on the page: the published annual
    fee, and the published annual credit totals summed the way the issuer sums them.
  * The positioning language is a faithful paraphrase of how each card is actually sold —
    flagship, premium, everyday — not a caricature.
  * Nothing is added that would help. There are no MCC-keyed earn rates in machine-readable
    form, no remaining balances, no exclusivity groups, no caps. That is not an omission we
    made; it is what a marketing page is.

Read the three entries in order and the inversion is visible before any code runs. Ranked
by headline generosity the order is Platinum, then Sapphire Reserve, then Gold — the biggest
fee and the biggest advertised credit stack first. On an ordinary week's basket the
deterministic evaluator produces the exact reverse. Illegible value is not premium value; to
a ranking machine it is absent value.

The copy is authored from published positioning. It is not scraped from any issuer's site,
and no claim is made that these are verbatim issuer words.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from plumbline.products import (
    AMEX_GOLD_ID,
    AMEX_PLATINUM_ID,
    CHASE_SAPPHIRE_RESERVE_ID,
)

# What a marketing page gives a ranking machine: a fee, a headline credit total, and prose.
# Recorded as a constant so the control run's disadvantage is stated rather than implied.
MARKETING_SIGNAL = (
    "annual fee, an advertised annual credit total, and prose. No merchant category codes, "
    "no remaining balances, no caps, no exclusivity groups, no per-line arithmetic."
)


@dataclass(frozen=True)
class MarketingCopy:
    """One product's page, as an agent without a value rail would read it."""

    instrument_id: str
    display_name: str
    headline: str
    body: str
    advertised_annual_fee: str
    advertised_credit_total: str

    def page_text(self) -> str:
        return (
            f"{self.display_name}\n"
            f"{self.headline}\n\n"
            f"{self.body}\n\n"
            f"Annual fee: {self.advertised_annual_fee}\n"
            f"Advertised annual statement credits: {self.advertised_credit_total}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "display_name": self.display_name,
            "headline": self.headline,
            "advertised_annual_fee": self.advertised_annual_fee,
            "advertised_credit_total": self.advertised_credit_total,
            "page_text": self.page_text(),
        }


PLATINUM_COPY = MarketingCopy(
    instrument_id=AMEX_PLATINUM_ID,
    display_name="The Platinum Card from American Express",
    headline="The flagship. Built for the way you travel and live.",
    body=(
        "Earn 5X Membership Rewards points on flights booked directly with airlines or with "
        "American Express Travel, and 5X on prepaid hotels booked through American Express "
        "Travel. Enjoy up to $600 in annual hotel credits, up to $400 in annual Resy dining "
        "credits, a $200 annual airline fee credit, up to $300 in annual digital "
        "entertainment credits, up to $200 in annual Uber Cash, and up to $300 in annual "
        "lululemon credits. Access more than 1,400 airport lounges worldwide, including The "
        "Centurion Lounge. Complimentary Hilton Honors Gold and Marriott Bonvoy Gold status. "
        "Fine Hotels + Resorts room upgrades, daily breakfast for two and late checkout. "
        "Platinum Concierge, on call."
    ),
    advertised_annual_fee="$895",
    advertised_credit_total="over $1,900 a year in statement credits",
)

GOLD_COPY = MarketingCopy(
    instrument_id=AMEX_GOLD_ID,
    display_name="American Express Gold Card",
    headline="For the way you eat, at home and out.",
    body=(
        "Earn 4X Membership Rewards points at restaurants worldwide and 4X at U.S. "
        "supermarkets, each up to an annual spending cap. Earn 5X on prepaid hotels and 3X "
        "on flights booked through American Express Travel. Enjoy up to $120 in annual "
        "dining credits, up to $120 in annual Uber Cash, up to $100 in annual Resy credits "
        "and up to $84 in annual Dunkin' credits. No airport lounge access."
    ),
    advertised_annual_fee="$325",
    advertised_credit_total="up to $424 a year in statement credits",
)

SAPPHIRE_COPY = MarketingCopy(
    instrument_id=CHASE_SAPPHIRE_RESERVE_ID,
    display_name="Chase Sapphire Reserve",
    headline="Reserve the extraordinary.",
    body=(
        "Earn 8X Ultimate Rewards points on purchases through Chase Travel, 4X on flights "
        "and hotels booked direct, 5X on Lyft rides and 3X on dining worldwide. Enjoy a $300 "
        "annual travel credit, up to $500 a year in The Edit hotel credits, up to $300 a "
        "year in Sapphire Reserve Exclusive Tables dining credits and up to $120 a year in "
        "Lyft credits. Priority Pass Select and Chase Sapphire Lounge access. IHG One Rewards "
        "Platinum Elite status. Primary auto rental collision damage waiver."
    ),
    advertised_annual_fee="$795",
    advertised_credit_total="up to $1,220 a year in statement credits",
)

COPY_BY_ID: Mapping[str, MarketingCopy] = {
    PLATINUM_COPY.instrument_id: PLATINUM_COPY,
    GOLD_COPY.instrument_id: GOLD_COPY,
    SAPPHIRE_COPY.instrument_id: SAPPHIRE_COPY,
}

# Declaration order is deliberately the order a comparison site would print: flagship first.
# The control agent is handed the industry's own default ordering, not a shuffled one.
ORDERED_IDS: tuple[str, ...] = (
    PLATINUM_COPY.instrument_id,
    SAPPHIRE_COPY.instrument_id,
    GOLD_COPY.instrument_id,
)


def listing() -> list[dict[str, str]]:
    """The catalogue as a marketing page lists it: name, fee, headline credit total."""
    return [
        {
            "instrument_id": COPY_BY_ID[i].instrument_id,
            "display_name": COPY_BY_ID[i].display_name,
            "headline": COPY_BY_ID[i].headline,
            "advertised_annual_fee": COPY_BY_ID[i].advertised_annual_fee,
            "advertised_credit_total": COPY_BY_ID[i].advertised_credit_total,
        }
        for i in ORDERED_IDS
    ]


def copy_for(token: str) -> MarketingCopy | None:
    """Resolve by instrument id or by a unique case-insensitive name match."""
    if token in COPY_BY_ID:
        return COPY_BY_ID[token]
    needle = token.strip().lower()
    hits = [c for c in COPY_BY_ID.values() if needle in c.display_name.lower()]
    return hits[0] if len(hits) == 1 else None
