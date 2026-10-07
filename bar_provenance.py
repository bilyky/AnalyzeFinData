"""
OHLCV bar provenance predicate.

Leaf module (stdlib only) so both the RapidAPI recovery layer and the pattern/volume
consumers can share one definition of "is this bar real?" without importing each other.

Provenance model for a bar in Data/Symbol_full/{sym}_daily.json:
  - ``provisional`` — Chaikin close-only placeholder (fake open/high/low, volume 0).
    Written by powergauge._append_ohlcv_entry; repaired by the RapidAPI recovery pass.
  - not provisional — real bar (volume > 0), trusted and usable by volume/range consumers.
"""
import datetime


def is_provisional(bar: dict) -> bool:
    """True if a bar is a Chaikin close-only placeholder (no real volume/range).

    Trusts the explicit ``provisional`` marker first; ``volume == 0`` is a fallback for
    bars written before the marker existed (real Alpha Vantage bars always carry
    volume > 0). Volume/range consumers (MFI, RBR) must skip these bars.
    """
    if not isinstance(bar, dict):
        return False
    if bar.get("provisional"):
        return True
    try:
        return float(bar.get("5. volume", 0) or 0) <= 0
    except (TypeError, ValueError):
        return False


def real_dates(ts: dict) -> list[str]:
    """Chronological dates of the REAL bars in a daily series (placeholders dropped).

    The shared filter for range/volume consumers (ATR, swing levels, candlestick and chart
    patterns): a placeholder anywhere in the window — not just a trailing one — is a flat
    zero-volume bar that shrinks true range, so it must never enter those calculations.
    """
    return sorted(d for d, bar in ts.items() if not is_provisional(bar))


def is_weekend(date_str: str) -> bool:
    """True for a Saturday/Sunday 'YYYY-MM-DD' — never a US equity session."""
    return datetime.date.fromisoformat(date_str).weekday() >= 5
