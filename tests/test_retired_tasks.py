"""A retired task stays in the manifest but is never registered, run or audited.

PROD 2026-11-11: re-running register_agent_tasks.ps1 (the #251 deploy step) re-created the three
Gemini agent tasks the 11-02 runbook had disabled. They failed with HTTP 403 every day again. The
manifest had no way to say "keep this entry, but don't schedule it" (PROD review 2026-11-12).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import watchdog
from aether import scheduled_tasks as st

_REPO = Path(__file__).resolve().parent.parent
_PS1 = _REPO / "scripts" / "utils" / "register_agent_tasks.ps1"
_RETIRED = {"AETHER_DailyDriver", "AETHER_PostMarketSync", "AETHER_PostMarketReporter",
            "AETHER_RD_Scientist"}


def _task(name, **extra):
    return {"name": name, "schedule": {"type": "daily", "at": "07:05"}, "prompt": "x", "log": "x.log",
            "summary": "s", "description": "d", "time_limit_min": 15, **extra}


class RetiredManifestTest(unittest.TestCase):
    def test_the_three_gemini_tasks_are_retired(self):
        data = st.load()
        retired = {t["name"] for t in data["tasks"] if t.get("retired")}
        self.assertEqual(retired, _RETIRED)

    def test_active_lists_leave_retired_tasks_out(self):
        names = {t["name"] for t in st.tasks()}
        self.assertFalse(names & _RETIRED)
        self.assertIn("AETHER_ExecuteTrades", names)
        self.assertFalse(any(n.endswith(tuple(_RETIRED)) for n in st.full_names()))
        self.assertEqual({n.split("\\")[-1] for n in st.retired_names()}, _RETIRED)

    def test_describe_shows_the_reason(self):
        row = st.describe(next(t for t in st.load()["tasks"] if t["name"] == "AETHER_DailyDriver"))
        self.assertTrue(row["retired"])
        row = st.describe(next(t for t in st.load()["tasks"] if t["name"] == "AETHER_ExecuteTrades"))
        self.assertEqual(row["retired"], "")

    def test_retired_needs_a_reason(self):
        for bad in (True, "", 1):
            problems = st.validate({"folder": "\\A\\", "tasks": [_task("T", retired=bad)]})
            self.assertTrue(any("retired" in p for p in problems), (bad, problems))
        self.assertEqual(st.validate({"folder": "\\A\\", "tasks": [_task("T", retired="Gemini 403")]}), [])


class StrayCheckTest(unittest.TestCase):
    def test_a_retired_task_is_not_reported_as_a_stray(self):
        listing = ("TaskName: \\AETHER_Agents\\AETHER_DailyDriver\nStatus: Disabled\n\n"
                   "TaskName: \\AETHER_Agents\\AETHER_Unknown_Test\nStatus: Ready\n\n")
        res = subprocess.CompletedProcess([], 0, stdout=listing, stderr="")
        with mock.patch.object(watchdog.subprocess, "run", return_value=res), \
             self.assertLogs("aether.watchdog", level="WARNING") as logs:
            watchdog.purge_stray_tasks()
        out = "\n".join(logs.output)
        self.assertIn("AETHER_Unknown_Test", out)
        self.assertNotIn("AETHER_DailyDriver", out)


@unittest.skipUnless(sys.platform == "win32", "the registrar dry run needs Windows PowerShell")
class RegistrarDryRunTest(unittest.TestCase):
    @staticmethod
    def _dry_run(*extra):
        res = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(_PS1), "-DryRun", *extra], capture_output=True, text=True,
                             timeout=120, stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        return [json.loads(line) for line in res.stdout.splitlines() if line.strip()]

    def test_retired_tasks_are_disabled_not_built(self):
        lines = self._dry_run()
        built = {d["name"] for d in lines if "execute" in d}
        disabled = {d["name"]: d for d in lines if d.get("action") == "disable"}
        self.assertFalse(built & _RETIRED)
        self.assertEqual(set(disabled), _RETIRED)
        self.assertTrue(all(d["retired"] for d in disabled.values()))

    def test_only_a_retired_task_builds_nothing(self):
        lines = self._dry_run("-Only", "AETHER_DailyDriver")
        self.assertEqual([d.get("action") for d in lines], ["disable"])


if __name__ == "__main__":
    unittest.main()
