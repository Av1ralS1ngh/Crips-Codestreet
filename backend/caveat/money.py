"""Money formatting, with no dependencies at all.

This is a separate module for one structural reason. `fmt_money` used to live in
constraints.py, which imports z3, so every module that wanted to render an amount pulled a
solver into the process — including the allocation verifier, whose entire claim is that a
counterparty can check an issuer's arithmetic without one. The claim was true of the
algorithm and false of the import graph, which is the kind of gap a reviewer finds by
grepping rather than by reading.

Keeping this module dependency-free is what makes that claim checkable; there is a test
that imports the valuation path in a clean interpreter and fails if z3 appears in
sys.modules.
"""

from __future__ import annotations

from typing import Mapping

RUPEE = "₹"
DOLLAR = "$"

# Every currency this repository is willing to render. Both have exactly 100 minor units to
# the major unit, which is what the point-valuation arithmetic assumes when it converts a
# points balance into a rate. Adding a currency means confirming that first.
CURRENCY_SYMBOLS: Mapping[str, str] = {"INR": RUPEE, "USD": DOLLAR}


class UnknownCurrencyError(ValueError):
    """A currency with no declared symbol.

    Raised rather than defaulted. A figure rendered under a guessed sign is worse than no
    figure: it is a wrong number that looks right.
    """


def symbol_for(currency: str) -> str:
    try:
        return CURRENCY_SYMBOLS[currency]
    except KeyError as exc:
        raise UnknownCurrencyError(
            f"unknown currency {currency!r}; known: {', '.join(sorted(CURRENCY_SYMBOLS))}. "
            f"Add it to CURRENCY_SYMBOLS and confirm it has 100 minor units to the major "
            f"unit before rendering anything in it."
        ) from exc


def fmt_currency(minor_units: int, currency: str) -> str:
    """Render integer minor units under the symbol the currency code names.

    This is the form every serializer in the valuation path uses. `fmt_money` below takes a
    symbol and defaults it, which is right for the kernel's own INR-denominated carts and
    silently wrong for anything else — so nothing that serializes a manifest-derived figure
    is allowed to reach it without naming the currency.
    """
    return fmt_money(minor_units, symbol_for(currency))


def fmt_money(minor_units: int, symbol: str = RUPEE) -> str:
    """Render integer minor units as currency. Presentation only — never for arithmetic."""
    negative = minor_units < 0
    whole, frac = divmod(abs(minor_units), 100)
    # Indian digit grouping: last three digits, then pairs.
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join([*groups, tail])
    out = f"{symbol}{s}.{frac:02d}" if frac else f"{symbol}{s}"
    return f"-{out}" if negative else out
