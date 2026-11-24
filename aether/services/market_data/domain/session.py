"""Market session rules (R&D #78 S5). Pure: no I/O, no live calls.

Moved as-is from ``ai_portfolio_game.is_market_open`` / ``is_market_hours``. There are two
different gates, kept as they are:

- ``regular_hours(now)``: the run gate. Weekday, an NYSE session day (``is_session``), and
  9:30-16:00 New York time. It returns ``(open, message)``.
- ``in_trading_window(now)``: the first half of "market hours". 6:30-13:15 Los Angeles time,
  a weekday, and a date not in ``NYSE_HOLIDAYS``. The second half is the live E*TRADE clock,
  which the service asks (``SessionService.market_hours``).
"""
import datetime

import pytz

from aether.market_calendar import NYSE_HOLIDAYS, is_session

ET = pytz.timezone("America/New_York")
LA = pytz.timezone("America/Los_Angeles")


def regular_hours(now: datetime.datetime) -> tuple:
    """US market session gate: weekday, not an NYSE full-day holiday, 9:30 AM - 4:00 PM ET."""
    now = now.astimezone(ET)
    if now.weekday() >= 5:
        return False, "Market is closed (Weekend)."
    if not is_session(now.date()):
        return False, f"Market is closed (NYSE holiday {now.date().isoformat()})."

    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    if market_open <= now <= market_close:
        return True, "Market is Open."
    return False, f"Market is Closed. (Current EST: {now.strftime('%H:%M')})"


def in_trading_window(now: datetime.datetime) -> bool:
    """6:30 AM - 1:15 PM Los Angeles time, a weekday, and not a listed NYSE holiday."""
    now_la = now.astimezone(LA)
    start_time = now_la.replace(hour=6, minute=30, second=0, microsecond=0)
    end_time = now_la.replace(hour=13, minute=15, second=0, microsecond=0)
    if not (start_time <= now_la <= end_time):
        return False
    if now_la.weekday() in (5, 6):
        return False
    return now_la.strftime("%Y-%m-%d") not in NYSE_HOLIDAYS
