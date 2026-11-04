"""NYSE trading calendar: the full-day closures and the last-session lookup.

One home for the holiday list. ai_portfolio_game re-exports NYSE_HOLIDAYS (is_market_hours and
the stale-workbook gate use it), and aether.options uses last_session_on_or_before to date and
settle weekly covered calls.
"""

import datetime

# Full-day NYSE closures. 2023-2025 were checked against the SPY bars in Data/Symbol_full
# (2026-11-04): no bar on any listed date, a bar on every other weekday.
NYSE_HOLIDAYS = frozenset({
    "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19",
    "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19",
    "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-01-09",  # 2025-01-09: national day of mourning (President Carter)
    "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",

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
