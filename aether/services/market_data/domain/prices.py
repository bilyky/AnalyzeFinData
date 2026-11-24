"""Price rules (R&D #78 S5). Pure: no I/O.

Moved as-is from ``ai_portfolio_game.get_live_prices`` / ``get_json_prices_fallback``.
"""
import datetime


def missing(symbols, quotes: dict) -> list:
    """The symbols with no usable quote (absent, empty, zero or negative), in the given order."""
    return [s for s in symbols if s not in quotes or not quotes[s] or quotes[s] <= 0]


def cached_close_is_current(newest_date: str, today: datetime.date) -> bool:
    """A cached daily close counts as a price: today's, any close on a weekend, or one up to
    4 calendar days old (covers 3-day weekends and market holidays)."""
    is_weekend = today.weekday() in (5, 6)
    cutoff = (today - datetime.timedelta(days=4)).isoformat()
    return newest_date == str(today) or is_weekend or newest_date >= cutoff
