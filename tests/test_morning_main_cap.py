"""The morning pipeline gives main.py the same 30-min cap as the evening daily_task.

PROD 2026-10-13 05:42:59 -> 05:52:59: the morning pipeline killed main.py at its 600 s cap
and aborted (no morning refresh). main.py was at 478/549 symbols and in its last phase when
killed; it took 8m45s to get there. #176 raised the evening cap to 1800 s; the morning one was
still 600 s and its message blamed a "Playwright hang".
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import autonomous_pipeline as ap
import daily_task
from aether import scheduled_tasks


class TestMorningMainCap(unittest.TestCase):
    def test_same_cap_as_the_evening_run(self):
        self.assertEqual(ap.MAIN_TIMEOUT_S, daily_task.MAIN_TIMEOUT_S)
        self.assertGreater(ap.MAIN_TIMEOUT_S, 600)

    def test_morning_task_limit_covers_the_backfill_and_the_cap(self):
        morning = {t["name"]: t for t in scheduled_tasks.tasks()}["AETHER_Morning"]
        backfill_s = 600   # autonomous_pipeline's run_history.py cap
        self.assertGreaterEqual(morning["time_limit_min"] * 60, backfill_s + ap.MAIN_TIMEOUT_S + 600)

    def test_main_py_runs_with_the_cap(self):
        with mock.patch.object(ap.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
             mock.patch.object(ap, "log"):
            self.assertTrue(ap._refresh_workbook(no_email=True))
        self.assertEqual(run.call_args.kwargs["timeout"], ap.MAIN_TIMEOUT_S)

    def test_timeout_aborts_with_an_honest_message(self):
        boom = subprocess.TimeoutExpired(["python", "main.py"], ap.MAIN_TIMEOUT_S)
        with mock.patch.object(ap.subprocess, "run", side_effect=boom), \
             mock.patch.object(ap, "log") as log, \
             mock.patch.object(ap.notify, "send_email") as email:
            self.assertFalse(ap._refresh_workbook(no_email=False))
        text = " ".join(str(c.args[0]) for c in log.call_args_list)
        self.assertIn(f"{ap.MAIN_TIMEOUT_S}s", text)
        self.assertNotIn("Playwright", text)
        self.assertTrue(email.called)


if __name__ == "__main__":
    unittest.main()
