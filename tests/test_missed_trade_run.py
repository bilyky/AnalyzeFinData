"""The watchdog alerts when a trading day has no scheduled trade run.

PROD 2026-11-11 (an NYSE session): the 07:00 AETHER_ExecuteTrades run never happened, and nothing
said so. The scheduler audit's schtasks queries timed out, and a failed check is silent
(PROD review 2026-11-12). The scheduled run writes Data/run_stamps/trade_execution.date
(aether.run_guard) on a clean exit; a manual --force run writes no stamp.
"""
import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytz

import watchdog

_PT = pytz.timezone("America/Los_Angeles")


def _at(y, m, d, hh, mm):
    return _PT.localize(datetime.datetime(y, m, d, hh, mm))


class MissedTradeRunTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.stamp = Path(self._td.name) / "run_stamps" / "trade_execution.date"
        self._p = mock.patch.object(watchdog, "TRADE_RUN_STAMP", self.stamp)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._td.cleanup()

    def _write(self, day):
        self.stamp.parent.mkdir(parents=True, exist_ok=True)
        self.stamp.write_text(day, encoding="utf-8")

    def test_a_session_day_without_the_run_alerts(self):
        self._write("2026-11-10")
        alerts = watchdog.check_trade_run_today(now=_at(2026, 11, 11, 8, 0))
        self.assertEqual(len(alerts), 1)
        self.assertIn("2026-11-11", alerts[0])
        self.assertIn("2026-11-10", alerts[0])
        self.assertIn("--run --force", alerts[0])

    def test_a_run_today_is_fine(self):
        self._write("2026-11-11")
        self.assertEqual(watchdog.check_trade_run_today(now=_at(2026, 11, 11, 8, 0)), [])

    def test_before_the_grace_time_nothing_is_due(self):
        self._write("2026-11-10")
        self.assertEqual(watchdog.check_trade_run_today(now=_at(2026, 11, 11, 7, 15)), [])

    def test_weekends_and_holidays_need_no_run(self):
        self._write("2026-11-10")
        self.assertEqual(watchdog.check_trade_run_today(now=_at(2026, 11, 14, 9, 0)), [])   # Saturday
        self.assertEqual(watchdog.check_trade_run_today(now=_at(2026, 11, 26, 9, 0)), [])   # Thanksgiving

    def test_no_stamp_ever_alerts(self):
        alerts = watchdog.check_trade_run_today(now=_at(2026, 11, 11, 8, 0))
        self.assertEqual(len(alerts), 1)
        self.assertIn("never", alerts[0])

    def test_wired_into_the_data_alerts(self):
        """run_watchdog sends it through send_data_alerts (once per alert per day)."""
        src = Path(watchdog.__file__).read_text(encoding="utf-8")
        self.assertIn("check_trade_run_today()", src.split("def run_watchdog", 1)[1])

    def test_the_stamp_path_matches_the_run_guard(self):
        from aether import run_guard
        guard = run_guard.DailyRunGuard("portfolio_state", stamp="trade_execution")
        self.assertEqual(Path(guard._stamp_path).name, "trade_execution.date")
        self.assertEqual(Path(guard._stamp_path).parent.name, "run_stamps")


if __name__ == "__main__":
    unittest.main()
