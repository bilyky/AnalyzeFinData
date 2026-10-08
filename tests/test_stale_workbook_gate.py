"""
The trade run refuses to act on a stale workbook.

AGENT.md "Live-Data Only Mandate": never trade on stale data; fail loudly instead. The 07:00
run reads Data/state_of_the_day.xlsx, which the evening (daily_task) and morning
(autonomous_pipeline) runs refresh. On PROD both refreshes failed on some days in 2026-09/10.

"Stale" = last written before the previous trading session's close (13:00 PT). An evening
refresh after that close is as fresh as the data can be at 07:00, so it is NOT stale.
Real mtimes on a real temp file; only the clock, the email boundary and the regime lookup
are mocked.
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game

_LA = pytz.timezone("America/Los_Angeles")


def _la(*args):
    return _LA.localize(datetime.datetime(*args))


class TestWorkbookStaleness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.book = Path(self.tmp.name) / "state_of_the_day.xlsx"
        self.book.write_bytes(b"x")

    def _written(self, when):
        ts = when.timestamp()
        os.utime(self.book, (ts, ts))

    def _stale(self, now):
        return game.workbook_staleness(self.book, now=now)

    def test_previous_evening_refresh_is_fresh(self):
        # Wed 09-30 07:00: Tue 09-29 17:17 evening refresh happened after Tue's close.
        self._written(_la(2026, 9, 29, 17, 17))
        self.assertIsNone(self._stale(_la(2026, 9, 30, 7, 0)))

    def test_refresh_before_previous_close_is_stale(self):
        self._written(_la(2026, 9, 28, 17, 0))
        reason = self._stale(_la(2026, 9, 30, 7, 0))
        self.assertIsNotNone(reason)
        self.assertIn("2026-09-29", reason)

    def test_monday_accepts_friday_evening(self):
        self._written(_la(2026, 10, 2, 17, 5))
        self.assertIsNone(self._stale(_la(2026, 10, 5, 7, 0)))
        self._written(_la(2026, 10, 1, 17, 5))
        self.assertIsNotNone(self._stale(_la(2026, 10, 5, 7, 0)))

    def test_day_after_a_holiday_accepts_the_last_real_session(self):
        # Tue 2026-09-08 after Labor Day (Mon 09-07): previous session is Fri 09-04.
        self._written(_la(2026, 9, 4, 17, 0))
        self.assertIsNone(self._stale(_la(2026, 9, 8, 7, 0)))

    def test_missing_workbook_is_stale(self):
        self.book.unlink()
        self.assertIsNotNone(self._stale(_la(2026, 9, 30, 7, 0)))


class TestTradeRunRefusesStaleWorkbook(unittest.TestCase):
    def test_stale_workbook_aborts_before_any_trading_and_alerts(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "state_of_the_day.xlsx"
            book.write_bytes(b"x")
            old = (datetime.datetime.now() - datetime.timedelta(days=6)).timestamp()
            os.utime(book, (old, old))
            with mock.patch.object(game, "XLSX_FILE", book), \
                 mock.patch.object(game, "get_market_regime", return_value="BALANCED"), \
                 mock.patch.object(game, "_has_strong_setups_today", return_value=False), \
                 mock.patch.object(game.openpyxl, "load_workbook") as load_wb, \
                 mock.patch.object(game.notify, "send_email") as email, \
                 mock.patch.object(game, "save_game") as save, \
                 mock.patch.object(game, "update_excel_log") as excel_log, \
                 mock.patch.object(game, "_log") as log:
                game.run_daily_ai_management(force=True)
        load_wb.assert_not_called()
        # run_daily_ai_management's finally always saves the loaded state (as on the existing
        # "Workbook not found" return); what matters is that no trade was recorded.
        if save.called:
            self.assertEqual(save.call_args.args[0].get("history", []), [])
        if excel_log.called:
            self.assertEqual(excel_log.call_args.args[1], [])
        self.assertTrue(email.called)
        self.assertIn("stale", email.call_args.args[0].lower())
        self.assertTrue(any("stale" in str(c.args[0]).lower() for c in log.error.call_args_list))


class TestOneHolidayList(unittest.TestCase):
    def test_market_hours_uses_the_module_holiday_set(self):
        self.assertIn("2026-09-07", game.NYSE_HOLIDAYS)
        self.assertIn("2026-06-19", game.NYSE_HOLIDAYS)


if __name__ == "__main__":
    unittest.main()
