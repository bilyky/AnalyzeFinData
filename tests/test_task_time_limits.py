"""Task Scheduler runtime caps: the Evening task (daily_task.py) must outlive its OHLCV
recovery pass (~5 h worst case, rapidapi.pass_timeout_seconds); every other task keeps the
15-minute default. Drives the real watchdog.heal_tasks with schtasks/PowerShell mocked."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import daily_task
import rapidapi
import watchdog

_PS1 = os.path.join(os.path.dirname(__file__), "..", "scripts", "utils", "register_agent_tasks.ps1")


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

    def test_evening_task_outlives_the_whole_daily_task(self):
        # Every step timeout in daily_task.main(), not just the recovery pass: run_history +
        # main.py + backup sync + pass + slack. Raising a step budget past what the Evening limit
        # covers fails here instead of having Windows stop the task mid-pass.
        limit_s = self._limits(["AnalyzeFinData_Evening"])["AnalyzeFinData_Evening"] * 60
        self.assertGreaterEqual(limit_s, daily_task.worst_case_runtime_seconds())
        self.assertGreater(daily_task.worst_case_runtime_seconds(), rapidapi.pass_timeout_seconds())

    def test_ps1_registers_the_same_limit_for_the_daily_task(self):
        # register_agent_tasks.ps1 runs daily_task.py as AETHER_AftermarketReport; its
        # TimeLimitMin must match watchdog's Evening value so the two registrations can't drift.
        with open(_PS1, encoding="utf-8") as f:
            ps1 = f.read()
        entry = ps1[ps1.index('Name     = "AETHER_AftermarketReport"'):]
        entry = entry[:entry.index("}")]
        self.assertIn("daily_task.py", entry)
        self.assertIn(f"TimeLimitMin = {watchdog._TASK_TIME_LIMIT_MIN['AnalyzeFinData_Evening']}", entry)

    def test_other_tasks_keep_the_default(self):
        limits = self._limits(["AnalyzeFinData_Morning", "AnalyzeFinData_AI_Summary"])
        self.assertEqual(limits, {"AnalyzeFinData_Morning": 15, "AnalyzeFinData_AI_Summary": 15})


if __name__ == "__main__":
    unittest.main()
