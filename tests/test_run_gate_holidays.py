"""The daily run's first gate, is_market_open(), knows NYSE holidays (R&D #78 fix F3).

The 07:00 PT `--run` (10:00 ET) gates on is_market_open() before anything else. It used to
check only weekends and 9:30-16:00 ET, so on a weekday holiday such as Thanksgiving it said
"Market is Open." and the run went on to load state and fetch prices on a closed market.
"""
import datetime as _real_datetime
import os
import sys
import unittest
from unittest import mock

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402

_ET = pytz.timezone("America/New_York")


def _clock(y, m, d, hh, mm):
    """A `datetime` module stand-in whose now(tz) is the given New York wall time."""
    fixed = _ET.localize(_real_datetime.datetime(y, m, d, hh, mm))

    class _DT(_real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    return mock.Mock(wraps=_real_datetime, datetime=_DT, date=_real_datetime.date,
                     timedelta=_real_datetime.timedelta)


def _gate(y, m, d, hh=10, mm=0):
    with mock.patch.object(game, "datetime", _clock(y, m, d, hh, mm)):
        return game.is_market_open()


class TestHolidayGate(unittest.TestCase):
    def test_thanksgiving_2026_is_closed(self):
        ok, msg = _gate(2026, 11, 26)
        self.assertFalse(ok)
        self.assertIn("holiday", msg.lower())

    def test_every_listed_weekday_holiday_is_closed(self):
        for iso in sorted(game.NYSE_HOLIDAYS):
            day = _real_datetime.date.fromisoformat(iso)
            if day.weekday() >= 5:
                continue
            with self.subTest(day=iso):
                ok, _ = _gate(day.year, day.month, day.day)
                self.assertFalse(ok)


class TestUnchangedBehaviour(unittest.TestCase):
    def test_a_normal_session_day_is_open_in_hours(self):
        self.assertEqual(_gate(2026, 11, 25), (True, "Market is Open."))

    def test_weekend_message_is_unchanged(self):
        self.assertEqual(_gate(2026, 11, 28), (False, "Market is closed (Weekend)."))

    def test_outside_hours_on_a_session_day(self):
        ok, msg = _gate(2026, 11, 25, 8, 0)
        self.assertFalse(ok)
        self.assertEqual(msg, "Market is Closed. (Current EST: 08:00)")

    def test_day_after_thanksgiving_trades(self):
        # 2026-11-27 is an early-close day, not a full closure; at 10:00 ET it is open.
        self.assertTrue(_gate(2026, 11, 27)[0])


if __name__ == "__main__":
    unittest.main()
