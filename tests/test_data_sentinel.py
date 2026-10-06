"""Loud detection of cache loss and stale backups (watchdog).

On 2026-10-06 the real Data/Symbol (551,396 files) and Data/Symbol_full (578) were
deleted and nothing alerted; the backup share had been unreachable, and
sync_data_folder() reports an offline share as success. These pin the two detectors:
  * check_data_sentinel(): a >20% drop in either cache vs the last run alerts, and the
    last good baseline is kept so the alert repeats until the data is restored;
  * check_backup_health(): no successful backup for BACKUP_STALE_DAYS alerts.
sync_data_folder()'s bool contract is unchanged; it only records its outcome.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import watchdog


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sentinel_"))
        self.cache = self.tmp / "cache"
        for name in ("Symbol", "Symbol_full"):
            (self.cache / name).mkdir(parents=True)
        self._patches = [
            mock.patch.dict(os.environ, {"AETHER_CACHE_DIR": str(self.cache)}),
            mock.patch.object(watchdog, "DATA_SENTINEL_FILE", self.tmp / "data_sentinel.json"),
            mock.patch.object(watchdog, "BACKUP_STATUS_FILE", self.tmp / "backup_status.json"),
            mock.patch.object(watchdog, "DATA_ALERT_MARKER", self.tmp / "data_alert_sent.json"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        for p in sorted(self.tmp.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        self.tmp.rmdir()

    def _fill(self, symbols=10, ohlcv=10):
        for name in ("Symbol", "Symbol_full"):
            for p in (self.cache / name).iterdir():
                p.rmdir() if p.is_dir() else p.unlink()
        for i in range(symbols):
            (self.cache / "Symbol" / f"S{i}").mkdir()
        for i in range(ohlcv):
            (self.cache / "Symbol_full" / f"S{i}_daily.json").write_text("{}", encoding="utf-8")


class TestDataSentinel(_Tmp):
    def test_first_run_records_a_baseline_without_alerting(self):
        self._fill(10, 10)
        self.assertEqual(watchdog.check_data_sentinel(), [])
        saved = json.loads(watchdog.DATA_SENTINEL_FILE.read_text(encoding="utf-8"))["counts"]
        self.assertEqual(saved, {"Symbol": 10, "Symbol_full": 10})

    def test_large_drop_alerts_and_keeps_the_good_baseline(self):
        self._fill(10, 10)
        watchdog.check_data_sentinel()
        self._fill(0, 10)  # the 2026-10-06 shape: a cache emptied
        alerts = watchdog.check_data_sentinel()
        self.assertEqual(len(alerts), 1)
        self.assertIn("Data/Symbol ", alerts[0] + " ")
        self.assertIn("10", alerts[0])
        self.assertIn("0", alerts[0])
        self.assertEqual(len(watchdog.check_data_sentinel()), 1, "must keep alerting until restored")

    def test_small_drop_does_not_alert_and_updates_the_baseline(self):
        self._fill(10, 10)
        watchdog.check_data_sentinel()
        self._fill(9, 10)
        self.assertEqual(watchdog.check_data_sentinel(), [])
        saved = json.loads(watchdog.DATA_SENTINEL_FILE.read_text(encoding="utf-8"))["counts"]
        self.assertEqual(saved["Symbol"], 9)


class TestBackupHealth(_Tmp):
    def _status(self, **kw):
        watchdog.BACKUP_STATUS_FILE.write_text(json.dumps(kw), encoding="utf-8")

    def test_never_backed_up_alerts(self):
        self.assertEqual(len(watchdog.check_backup_health()), 1)

    def test_recent_success_is_fine(self):
        self._status(last_success=(datetime.datetime.now() - datetime.timedelta(days=1)).isoformat())
        self.assertEqual(watchdog.check_backup_health(), [])

    def test_stale_success_alerts(self):
        old = datetime.datetime.now() - datetime.timedelta(days=watchdog.BACKUP_STALE_DAYS + 2)
        self._status(last_success=old.isoformat(), last_result="offline")
        alerts = watchdog.check_backup_health()
        self.assertEqual(len(alerts), 1)
        self.assertIn("offline", alerts[0])

    def test_offline_keeps_the_previous_success_time(self):
        self._status(last_success="2026-10-01T10:00:00")
        watchdog._record_backup_status("offline")
        saved = json.loads(watchdog.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_success"], "2026-10-01T10:00:00")
        self.assertEqual(saved["last_result"], "offline")

    def test_offline_share_still_returns_true_but_is_recorded(self):
        real_exists = os.path.exists

        def share_offline(p):  # only the UNC backup share is unreachable; Data/ itself is real
            return False if str(p).startswith("\\\\") else real_exists(p)

        with mock.patch.object(watchdog, "is_market_hours", return_value=False), \
             mock.patch.object(watchdog.os.path, "exists", side_effect=share_offline):
            self.assertTrue(watchdog.sync_data_folder())  # contract unchanged
        self.assertEqual(json.loads(watchdog.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))["last_result"], "offline")


class TestAlertThrottle(_Tmp):
    def test_alert_email_goes_out_at_most_once_a_day(self):
        with mock.patch.object(watchdog.notify, "send_email", return_value=True) as send:
            watchdog.send_data_alerts(["CRITICAL: x"])
            watchdog.send_data_alerts(["CRITICAL: x"])
        self.assertEqual(send.call_count, 1)

    def test_nothing_to_send_sends_nothing(self):
        with mock.patch.object(watchdog.notify, "send_email", return_value=True) as send:
            watchdog.send_data_alerts([])
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
