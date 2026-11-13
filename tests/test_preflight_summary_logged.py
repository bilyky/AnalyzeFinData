"""The pre-flight writes its per-check verdict through the logger, not only to the console.

PROD 2026-11-11 05:30: the morning pipeline's pre-flight FAILED, but its console output reached no
log file, so which gateway failed is unknown (PROD review 2026-11-12). The summary now also goes to
aether.log / aether.jsonl.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
import preflight_validator as pv


def _run(chaikin=True, etrade=True, ai=True):
    with mock.patch.object(pv, "purge_browser_zombies"), \
         mock.patch.object(pv, "check_gmail_imap", return_value=True), \
         mock.patch.object(pv, "check_gmail_smtp", return_value=True), \
         mock.patch.object(pv, "check_chaikin_api", return_value=chaikin), \
         mock.patch.object(pv, "check_etrade_api", return_value=etrade), \
         mock.patch.object(pv, "check_file_and_directory_integrity", return_value=(True, [])), \
         mock.patch.object(pv, "check_active_locks", return_value=(True, [])), \
         mock.patch.object(pv, "check_watchdog_health", return_value=(True, [])), \
         mock.patch.object(pv, "check_scheduled_tasks_integrity", return_value=(True, [])), \
         mock.patch.object(pv, "check_pipeline_smoke_test", return_value=("SKIPPED", [])), \
         mock.patch.object(pv, "check_ai_providers", return_value=(ai, [] if ai else ["no strong tier"])), \
         mock.patch.object(pv.sys, "argv", ["preflight_validator.py"]):
        return pv.run_preflight_diagnostics()


class PreflightSummaryLoggedTest(unittest.TestCase):
    def test_a_failure_names_the_failed_check(self):
        with self.assertLogs("aether.preflight", level="WARNING") as logs:
            self.assertFalse(_run(chaikin=False))
        line = next(m for m in logs.output if "Pre-flight summary" in m)
        self.assertIn("Pre-flight summary: FAIL", line)
        failed = line.split("; FAIL: ", 1)[1].split(";", 1)[0]
        self.assertEqual(failed, "Chaikin PowerGauge API")           # only the failed checks
        self.assertIn("E*TRADE Brokerage OAuth=PASS", line)            # and the full roster

    def test_a_pass_is_one_info_line(self):
        with self.assertLogs("aether.preflight", level="INFO") as logs:
            self.assertTrue(_run())
        lines = [m for m in logs.output if "Pre-flight summary" in m]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("INFO"), lines[0])
        self.assertIn("PASS", lines[0])

    def test_an_advisory_warning_does_not_fail_the_run(self):
        with self.assertLogs("aether.preflight", level="INFO") as logs:
            self.assertTrue(_run(ai=False))
        line = next(m for m in logs.output if "Pre-flight summary" in m)
        self.assertIn("AI Providers (advisory)", line)


if __name__ == "__main__":
    unittest.main()
