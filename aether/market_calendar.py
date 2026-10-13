"""NYSE trading calendar: the full-day closures and the last-session lookup.

One home for the holiday list. ai_portfolio_game re-exports NYSE_HOLIDAYS (is_market_hours and
the stale-workbook gate use it), and aether.options uses last_session_on_or_before to date and
settle weekly covered calls.
"""

import datetime

# Full-day NYSE closures.
NYSE_HOLIDAYS = frozenset({
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth National Independence Day
    "2026-07-03",  # Independence Day (Observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day

    # Future Years Support (2027)
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"
})


def is_session(day: datetime.date) -> bool:
    """True when NYSE trades on `day` (a weekday that isn't a full-day closure)."""
    return day.weekday() < 5 and day.isoformat() not in NYSE_HOLIDAYS


def last_session_on_or_before(day: datetime.date) -> datetime.date:
    """`day` itself when NYSE trades on it, else the latest earlier trading day.

    A weekly option whose Friday is a holiday expires on the session before it (e.g. Thursday
    2026-12-24 for Christmas 2026-12-25)."""
    while not is_session(day):
        day -= datetime.timedelta(days=1)
    return day
