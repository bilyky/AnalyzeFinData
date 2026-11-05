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

    def test_timeout_logs_the_last_output_before_the_kill(self):
        # PROD 11-04: run_history.py hung 30 min and the log showed only the timeout.
        out = "\n".join(f"line {k}" for k in range(1, 31))
        boom = subprocess.TimeoutExpired(["python", "run_history.py"], 600, output=out, stderr=b"warn: stuck on EA")
        with mock.patch.object(daily_task.subprocess, "run", side_effect=boom), \
             mock.patch.object(daily_task, "_log") as log:
            daily_task.run_command([sys.executable, "run_history.py", "5"], timeout=600)
        msg = log.error.call_args.args[0]
        self.assertIn("Last output before the kill", msg)
        self.assertIn("line 30", msg)
        self.assertIn("warn: stuck on EA", msg)
        self.assertNotIn("line 1\n", msg)                 # only the tail

    def test_output_tail_decodes_bytes(self):
        # Review of #245: run_command is text-mode, but TimeoutExpired can carry bytes.
        tail = daily_task._output_tail(b"fetching 2026-11-04\n", "warn: slow")
        self.assertEqual(tail, "fetching 2026-11-04\nwarn: slow")
        self.assertNotIn("b'", tail)

    def test_timeout_without_output_says_so(self):
        boom = subprocess.TimeoutExpired(["python", "run_history.py"], 600)
        with mock.patch.object(daily_task.subprocess, "run", side_effect=boom), \
             mock.patch.object(daily_task, "_log") as log:
            daily_task.run_command([sys.executable, "run_history.py", "5"], timeout=600)
        self.assertIn("No output before the kill.", log.error.call_args.args[0])

    def test_a_real_killed_child_keeps_its_output(self):
        # No mocks: subprocess.run must hand back what the child printed before the kill.
        # The text is built at run time so it is not in the command line the message also echoes.
        code = "import time; print('fetch' + 'ing ' + str(2000 + 26), flush=True); time.sleep(30)"
        with mock.patch.object(daily_task, "_log") as log:
            daily_task.run_command([sys.executable, "-c", code], timeout=3)
        msg = log.error.call_args.args[0]
        self.assertIn("fetching 2026", msg.split("Last output before the kill:", 1)[-1])
        self.assertIn("Last output before the kill:", msg)

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
        self.fail_steps = set()
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
            mock.patch.object(daily_task, "build_context_pack"),   # pack step pinned elsewhere
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
        if script in self.fail_steps:
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        return _ok(args)

    def _info_lines(self):
        return [str(c.args[0]) for c in self.log.info.call_args_list]

    def test_all_steps_pass_returns_zero_and_reports_success(self):
        rc = daily_task.main()
        self.assertEqual(rc, 0)
        self.assertIn("Automation completed successfully.", self._info_lines())
        self.assertNotIn("STEP FAILURE", self.emails[0][0])

    def test_data_completeness_banner_reaches_the_email(self):
        with mock.patch.object(daily_task, "_data_completeness_banner", return_value="<div>DATA-BANNER</div>"):
            daily_task.main()
        subject, body = self.emails[0]
        self.assertTrue(subject.startswith("[DATA] "))
        self.assertIn("<body><div>DATA-BANNER</div>", body)

    def test_timed_out_history_step_returns_one_and_is_in_the_report(self):
        self.fail_steps = {"run_history.py"}
        rc = daily_task.main()
        self.assertEqual(rc, 1)
        self.assertNotIn("Automation completed successfully.", self._info_lines())
        subject, body = self.emails[0]
        self.assertIn("STEP FAILURE", subject)
        self.assertIn("run_history.py", body)

    def test_failed_recovery_pass_alone_still_returns_one(self):
        self.fail_steps = {"rapidapi.py"}
        self.assertEqual(daily_task.main(), 1)

    def test_history_and_main_get_their_own_longer_timeouts(self):
        daily_task.main()
        self.assertEqual(self.timeouts["run_history.py"], daily_task.RUN_HISTORY_TIMEOUT_S)
        self.assertEqual(self.timeouts["main.py"], daily_task.MAIN_TIMEOUT_S)
        self.assertGreater(daily_task.RUN_HISTORY_TIMEOUT_S, 600)
        self.assertGreater(daily_task.MAIN_TIMEOUT_S, 600)


if __name__ == "__main__":
    unittest.main()
