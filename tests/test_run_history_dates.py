"""run_history: which days it may fetch. The Chaikin API serves current values only, so only
the most recent session is fetched; older missing days are reported, never filled with
today's data. The calendar is aether.market_calendar (no private holiday list)."""
import datetime
import sys
import unittest
from unittest import mock

import run_history
from aether import market_calendar

D = datetime.date


class TestCalendar(unittest.TestCase):
    def test_sessions_follow_the_shared_calendar(self):
        days = run_history.trading_days(3, today=D(2026, 11, 30))       # Monday after Thanksgiving
        self.assertEqual(days, [D(2026, 11, 30), D(2026, 11, 27), D(2026, 11, 25)])   # 11-27 trades

    def test_past_holidays_are_known(self):
        for d in ("2023-11-23", "2024-03-29", "2025-01-09", "2025-11-27"):
            self.assertFalse(market_calendar.is_session(D.fromisoformat(d)), d)
        self.assertTrue(market_calendar.is_session(D(2025, 11, 28)))    # day after Thanksgiving


class TestFetchable(unittest.TestCase):
    def test_only_the_most_recent_session(self):
        self.assertTrue(run_history.fetchable(D(2026, 11, 4), today=D(2026, 11, 4)))
        self.assertFalse(run_history.fetchable(D(2026, 11, 3), today=D(2026, 11, 4)))
        self.assertTrue(run_history.fetchable(D(2026, 10, 30), today=D(2026, 11, 1)))    # Fri, run on Sun
        self.assertTrue(run_history.fetchable(D(2026, 11, 25), today=D(2026, 11, 26)))   # Thanksgiving


class TestMainSkipsOldDays(unittest.TestCase):
    def test_old_missing_day_is_reported_not_fetched(self):
        today, old = D(2026, 11, 4), D(2026, 11, 3)
        calls = []
        with mock.patch.object(sys, "argv", ["run_history.py", "2"]), \
                mock.patch("run_history.load_symbols", return_value=["A", "B"]), \
                mock.patch("run_history.trading_days", return_value=[today, old]), \
                mock.patch("run_history.days_missing", side_effect=lambda syms, d: list(syms)), \
                mock.patch("run_history.fetchable", side_effect=lambda d: d == today), \
                mock.patch("run_history.powergauge.login", return_value={"jsessionid": "x"}), \
                mock.patch("run_history.powergauge.get_symbol_data",
                           side_effect=lambda s, d, p: calls.append((s, d)) or mock.MagicMock(price=1.0)), \
                mock.patch.dict("os.environ", {"CHAIKIN_WORKERS": "1"}), \
                self.assertLogs("aether.run_history", level="WARNING") as cm:
            run_history.main()
        self.assertEqual(sorted(calls), [("A", today), ("B", today)])
        self.assertIn("2026-11-03: 2 symbols have no snapshot. Not fetched", cm.output[0])


if __name__ == "__main__":
    unittest.main()
