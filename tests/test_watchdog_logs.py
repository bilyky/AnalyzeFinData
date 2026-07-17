"""
Tests for watchdog log-scanning — plain text + structured JSONL.
No real log files written; all patched.
"""
import datetime
import json
import os
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import watchdog


NOW = datetime.datetime(2026, 7, 17, 15, 0, 0)
RECENT = "2026-07-17 14:59:00"   # 1 min ago — within window
STALE  = "2026-07-17 13:00:00"   # 2 h ago  — outside window


class TestCheckPlainLogs(unittest.TestCase):
    def _run(self, content):
        with mock.patch.object(watchdog, "LOG_FILES", [Path("/fake/aether.log")]), \
             mock.patch("builtins.open", mock.mock_open(read_data=content)), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch.object(Path, "exists", return_value=True):
            return watchdog._check_plain_logs(NOW)

    def test_recent_error_detected(self):
        line = f"[{RECENT}] ERROR something broke\n"
        errs = self._run(line)
        self.assertTrue(any("ERROR" in e for e in errs))

    def test_stale_error_ignored(self):
        line = f"[{STALE}] ERROR ancient news\n"
        errs = self._run(line)
        self.assertEqual(errs, [])

    def test_zero_errors_skipped(self):
        line = f"[{RECENT}] OHLCV: 1 recovered, 0 errors\n"
        errs = self._run(line)
        self.assertEqual(errs, [])

    def test_symbology_error_detected(self):
        line = f"[{RECENT}] [AETHER] SYMBOLOGY ERROR: dead ticker\n"
        errs = self._run(line)
        self.assertTrue(any("SYMBOLOGY ERROR" in e.upper() for e in errs))


class TestCheckStructuredLog(unittest.TestCase):
    def _jsonl(self, entries):
        return "\n".join(json.dumps(e) for e in entries) + "\n"

    def _run(self, entries):
        content = self._jsonl(entries)
        with mock.patch.object(watchdog, "AETHER_JSONL", Path("/fake/aether.jsonl")), \
             mock.patch.object(Path, "exists", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=content)):
            return watchdog._check_structured_log(NOW)

    def test_recent_error_detected(self):
        errs = self._run([{"ts": RECENT, "level": "ERROR", "module": "aether.server",
                           "msg": "context build failed", "extra": {}}])
        self.assertEqual(len(errs), 1)
        self.assertIn("context build failed", errs[0])

    def test_warning_not_included(self):
        errs = self._run([{"ts": RECENT, "level": "WARNING", "module": "aether.server",
                           "msg": "empty reply", "extra": {}}])
        self.assertEqual(errs, [])

    def test_stale_error_ignored(self):
        errs = self._run([{"ts": STALE, "level": "ERROR", "module": "aether.x",
                           "msg": "old error", "extra": {}}])
        self.assertEqual(errs, [])

    def test_exc_included_in_output(self):
        errs = self._run([{"ts": RECENT, "level": "ERROR", "module": "aether.pipeline",
                           "msg": "crash", "extra": {}, "exc": "Traceback...\nValueError: bad"}])
        self.assertTrue(any("ValueError" in e for e in errs))

    def test_malformed_line_skipped(self):
        content = "not json\n" + json.dumps({"ts": RECENT, "level": "ERROR",
                                              "module": "m", "msg": "ok"}) + "\n"
        with mock.patch.object(watchdog, "AETHER_JSONL", Path("/fake/aether.jsonl")), \
             mock.patch.object(Path, "exists", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=content)):
            errs = watchdog._check_structured_log(NOW)
        self.assertEqual(len(errs), 1)   # only the valid JSON entry


class TestCheckLogsIntegration(unittest.TestCase):
    def test_combines_plain_and_structured(self):
        plain_err = [f"[aether.log] [{RECENT}] ERROR plain"]
        struct_err = [f"[aether.server] structured error"]
        with mock.patch.object(watchdog, "_check_plain_logs", return_value=plain_err), \
             mock.patch.object(watchdog, "_check_structured_log", return_value=struct_err):
            errs = watchdog.check_logs()
        self.assertEqual(len(errs), 2)
        self.assertIn(plain_err[0], errs)
        self.assertIn(struct_err[0], errs)

    def test_no_errors_returns_empty(self):
        with mock.patch.object(watchdog, "_check_plain_logs", return_value=[]), \
             mock.patch.object(watchdog, "_check_structured_log", return_value=[]):
            self.assertEqual(watchdog.check_logs(), [])


if __name__ == "__main__":
    unittest.main()
