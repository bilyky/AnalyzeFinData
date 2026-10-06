"""Task Scheduler runtime caps: the Evening task (daily_task.py) must outlive its OHLCV
recovery pass (~5 h worst case, rapidapi.pass_timeout_seconds); every other task keeps the
15-minute default. Drives the real watchdog.heal_tasks with schtasks/PowerShell mocked."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rapidapi
import watchdog


class TestTaskTimeLimits(unittest.TestCase):
    def _limits(self, tasks):
        seen = []
        ok = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        admin = mock.Mock()
        admin.shell32.IsUserAnAdmin.return_value = 1
        with mock.patch.object(watchdog.ctypes, "windll", admin, create=True), \
             mock.patch.object(watchdog.os, "getlogin", return_value="tester"), \
             mock.patch.object(watchdog.subprocess, "run",
                               side_effect=lambda args, **k: seen.append(args) or ok):
            watchdog.heal_tasks(tasks)
        out = {}
        for args in seen:
            if args[0] == "powershell.exe":
                cmd = args[-1]
                task = cmd.split("-TaskName '")[1].split("'")[0]
                out[task] = int(cmd.split("New-TimeSpan -Minutes ")[1].split(")")[0])
        return out

    def test_evening_task_outlives_the_recovery_pass(self):
        limits = self._limits(["AnalyzeFinData_Evening"])
        self.assertGreaterEqual(limits["AnalyzeFinData_Evening"] * 60, rapidapi.pass_timeout_seconds())

    def test_other_tasks_keep_the_default(self):
        limits = self._limits(["AnalyzeFinData_Morning", "AnalyzeFinData_AI_Summary"])
        self.assertEqual(limits, {"AnalyzeFinData_Morning": 15, "AnalyzeFinData_AI_Summary": 15})


if __name__ == "__main__":
    unittest.main()
