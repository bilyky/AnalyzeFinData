"""
daily_task must not report success when a step failed.

PROD 2026-09-25 .. 10-07: run_history.py and main.py were killed at the 600 s cap on most
evenings (both were still logging progress seconds before the kill), and daily_task still
logged "Automation completed successfully." with exit code 0, so neither Task Scheduler nor
the watchdog saw a failure. Pins:
  - a timed-out or non-zero step is recorded, logged at ERROR, and named in the report;
  - main() returns 1 when any step failed (incl. the recovery pass), 0 when all passed;
  - run_history.py and main.py get their own, longer timeouts;
  - child output is decoded as UTF-8 (cp1252 crashed the reader thread on PROD).
Only the subprocess / email / sync / data-load boundaries are mocked.
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import daily_task

_ROW = {"symbol": "AAA", "pgr": "Bu", "setup": 1, "s10": 5.0, "br": 2.0, "l60": 3.0}


def _ok(args, **kwargs):
    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class TestRunCommandRecordsFailures(unittest.TestCase):
    def setUp(self):
        daily_task._failed_steps.clear()
        self.addCleanup(daily_task._failed_steps.clear)

    def test_timeout_is_recorded_and_logged_as_error(self):
        boom = subprocess.TimeoutExpired(["python", "run_history.py"], 600)
        with mock.patch.object(daily_task.subprocess, "run", side_effect=boom), \
             mock.patch.object(daily_task, "_log") as log:
            daily_task.run_command([sys.executable, "run_history.py", "5"], timeout=600)
        self.assertEqual(len(daily_task._failed_steps), 1)
        self.assertIn("run_history.py", daily_task._failed_steps[0])
        self.assertIn("timed out", daily_task._failed_steps[0])
        self.assertTrue(log.error.called)

    def test_nonzero_exit_is_recorded_and_logged_as_error(self):
        bad = subprocess.CompletedProcess([], 2, stdout="", stderr="Traceback ...")
        with mock.patch.object(daily_task.subprocess, "run", return_value=bad), \
             mock.patch.object(daily_task, "_log") as log:
            daily_task.run_command([sys.executable, "main.py"])
        self.assertEqual(len(daily_task._failed_steps), 1)
        self.assertIn("exit code 2", daily_task._failed_steps[0])
        self.assertTrue(log.error.called)

    def test_success_records_nothing_and_decodes_utf8(self):
        with mock.patch.object(daily_task.subprocess, "run", side_effect=_ok) as run:
            daily_task.run_command([sys.executable, "main.py"])
        self.assertEqual(daily_task._failed_steps, [])
        self.assertEqual(run.call_args.kwargs.get("encoding"), "utf-8")
        self.assertEqual(run.call_args.kwargs.get("errors"), "replace")


class TestMainReportsStepFailures(unittest.TestCase):
    def setUp(self):
        self.emails = []
        self.timeouts = {}
        self.fail = set()
        patches = [
            mock.patch.object(daily_task.os, "chdir"),
            mock.patch.object(daily_task.subprocess, "run", side_effect=self._run),
            mock.patch.object(daily_task, "get_description", return_value=""),
            mock.patch.object(daily_task, "get_all_data", return_value=[_ROW]),
            mock.patch.object(daily_task.watchdog, "sync_data_folder", return_value=True),
            mock.patch.object(daily_task.notify, "send_email",
                              side_effect=lambda subject, body, **k: self.emails.append((subject, body))),
            mock.patch.object(daily_task.CFG, "has_placeholders", False, create=True),
            mock.patch.object(sys, "argv", ["daily_task.py"]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.log = mock.patch.object(daily_task, "_log").start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, args, **kwargs):
        script = os.path.basename(args[1]) if len(args) > 1 else ""
        if script.endswith(".py"):
            self.timeouts[script] = kwargs.get("timeout")
        if script in self.fail:
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        return _ok(args)

    def _info_lines(self):
        return [str(c.args[0]) for c in self.log.info.call_args_list]

    def test_all_steps_pass_returns_zero_and_reports_success(self):
        rc = daily_task.main()
        self.assertEqual(rc, 0)
        self.assertIn("Automation completed successfully.", self._info_lines())
        self.assertNotIn("STEP FAILURE", self.emails[0][0])

    def test_timed_out_history_step_returns_one_and_is_in_the_report(self):
        self.fail = {"run_history.py"}
        rc = daily_task.main()
        self.assertEqual(rc, 1)
        self.assertNotIn("Automation completed successfully.", self._info_lines())
        subject, body = self.emails[0]
        self.assertIn("STEP FAILURE", subject)
        self.assertIn("run_history.py", body)

    def test_failed_recovery_pass_alone_still_returns_one(self):
        self.fail = {"rapidapi.py"}
        self.assertEqual(daily_task.main(), 1)

    def test_history_and_main_get_their_own_longer_timeouts(self):
        daily_task.main()
        self.assertEqual(self.timeouts["run_history.py"], daily_task.RUN_HISTORY_TIMEOUT_S)
        self.assertEqual(self.timeouts["main.py"], daily_task.MAIN_TIMEOUT_S)
        self.assertGreater(daily_task.RUN_HISTORY_TIMEOUT_S, 600)
        self.assertGreater(daily_task.MAIN_TIMEOUT_S, 600)


if __name__ == "__main__":
    unittest.main()
