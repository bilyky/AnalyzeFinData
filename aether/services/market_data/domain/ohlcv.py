"""OHLCV cache rules (R&D #78 S5). Pure: no I/O.

Moved as-is from ``ai_portfolio_game`` (``_load_closes``, ``_cache_age_days``,
``_cache_stale``, ``_MAX_STALE_DAYS``).
"""
import datetime

MAX_STALE_DAYS = 10  # single source of truth for the OHLCV freshness horizon


def closes(series: dict) -> list:
    """The daily closes of a ``Time Series (Daily)`` mapping, oldest first."""
    return [float(series[d]["4. close"]) for d in sorted(series.keys())]


def age_days(newest_date: str, today: datetime.date) -> int:
    """Days between the newest bar and ``today``."""
    return (today - datetime.date.fromisoformat(newest_date)).days


def is_stale(age, max_stale_days: int = MAX_STALE_DAYS) -> bool:
    """A cache is stale when it has no bar (``age`` is None) or its newest bar is too old."""
    return age is None or age > max_stale_days
