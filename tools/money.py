"""tools/money.py - the one place every reason string formats an amount.

Every human-facing amount in this repo goes through :func:`fmt` so it always
carries the hotel's own currency (`config/hotel.yaml: hotel.currency`) instead
of a hardcoded EUR - see factory/workflows/build-repo.md section 5, "Money
strings use hotel.currency". Test with a non-euro persona (GBP, NOK) before
you trust a reason string in a screenshot.
"""

from __future__ import annotations


def fmt(amount: float | int | None, currency: str = "EUR") -> str:
    """``1719.0, "EUR"`` -> ``"EUR 1,719.00"``. Never raises on a bad input."""
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        value = 0.0
    return f"{currency} {value:,.2f}"


def fmt_signed(amount: float | int | None, currency: str = "EUR") -> str:
    """Same as :func:`fmt` but always carries a leading +/- sign."""
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        value = 0.0
    sign = "+" if value >= 0 else "-"
    return f"{sign}{currency} {abs(value):,.2f}"


def fmt_pct(pct: float | int | None) -> str:
    try:
        value = float(pct or 0)
    except (TypeError, ValueError):
        value = 0.0
    return f"{value:+.1f}%"
