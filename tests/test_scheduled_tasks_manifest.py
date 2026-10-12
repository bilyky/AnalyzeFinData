"""One source for scheduled tasks: scripts/utils/scheduled_tasks.json.

The registrar, the watchdog audit, the preflight duplicate check and the System page all read
it; none keeps its own list. Before this, five places held five different lists (the System
page showed 6 of 14 tasks, with a wrong description for AETHER_DailyDriver).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import daily_task
import data_api
import pre_commit_validator
import preflight_validator
import rapidapi
import watchdog
from aether import scheduled_tasks as st

_REPO = Path(__file__).resolve().parent.parent
_PS1 = _REPO / "scripts" / "utils" / "register_agent_tasks.ps1"


class TestManifest(unittest.TestCase):
    def test_manifest_is_valid_and_complete(self):
        data = st.load()
        self.assertEqual(st.validate(data), [])
        names = [t["name"] for t in data["tasks"]]
        for core in ("AETHER_Watchdog", "AETHER_Morning", "AETHER_ExecuteTrades",
                     "AETHER_AftermarketReport", "AETHER_AI_Summary", "AETHER_StopMonitor"):
            self.assertIn(core, names)

    def test_scripts_exist_and_run_buttons_point_at_real_manual_tasks(self):
        manual_ids = {t["id"] for t in data_api.MANUAL_TASKS}
        for t in st.tasks():
            if "script" in t:
                self.assertTrue((_REPO / t["script"]).exists(), t["script"])
            if t.get("manual_run"):
                self.assertIn(t["manual_run"], manual_ids, t["name"])

    def test_validate_catches_bad_entries(self):
        bad = {"tasks": [{"name": "A", "schedule": {"type": "weekly", "at": "10:00"}, "log": "a",
                          "summary": "s", "description": "d", "time_limit_min": 5, "script": "x.py",
                          "prompt": "p"},
                         {"name": "A", "schedule": {"type": "monthly"}}]}
        problems = " | ".join(st.validate(bad))
        for want in ("duplicate task names", "exactly one of script / prompt",
                     "weekly schedule needs a day", "unknown schedule type", "missing log"):
            self.assertIn(want, problems)

    def test_readable_schedule_and_command(self):
        by = {t["name"]: t for t in st.tasks()}
        self.assertEqual(st.schedule_text(by["AETHER_Watchdog"]), "Hourly, 24/7")
        self.assertEqual(st.schedule_text(by["AETHER_StopMonitor"]), "Daily 06:45, every 30 min for 7 h")
        self.assertEqual(st.schedule_text(by["AETHER_RD_Scientist"]), "Weekly Saturday 10:00")
        self.assertEqual(st.command_text(by["AETHER_ExecuteTrades"]),
                         r"venv_new\Scripts\python.exe ai_portfolio_game.py --run")
        self.assertIn("run_agent.cmd", st.command_text(by["AETHER_DailyDriver"]))

    def test_daily_task_fits_its_time_limit(self):
        limit_s = {t["name"]: t for t in st.tasks()}["AETHER_AftermarketReport"]["time_limit_min"] * 60
        self.assertGreaterEqual(limit_s, daily_task.worst_case_runtime_seconds())
        self.assertGreater(daily_task.worst_case_runtime_seconds(), rapidapi.pass_timeout_seconds())


class TestEveryConsumerReadsTheManifest(unittest.TestCase):
    def test_watchdog_audits_the_manifest(self):
        self.assertEqual(watchdog._tasks(), st.full_names())
        self.assertFalse(hasattr(watchdog, "_TASK_DEFS"), "the watchdog must not keep its own registry")

    def test_registrar_has_no_task_list_of_its_own(self):
        ps1 = _PS1.read_text(encoding="utf-8")
        self.assertIn("scheduled_tasks.json", ps1)
        self.assertEqual(re.findall(r'Name\s*=\s*"AETHER_', ps1), [])

    def test_system_page_has_no_task_list_of_its_own(self):
        js = (_REPO / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("SCHED_DESCRIPTIONS", js)
        self.assertNotIn("SCHED_TO_MANUAL", js)
        self.assertEqual(re.findall(r'"AETHER_[A-Za-z_]+"\s*:', js), [])
        self.assertFalse(hasattr(data_api, "_KNOWN_TASKS"))

    def test_api_returns_every_task_with_its_parameters(self):
        with mock.patch.object(data_api, "_live_task_states", return_value={
                "AETHER_Watchdog": {"status": "Ready", "last_run": "x", "next_run": "y", "last_result": "0"}}):
            rows = data_api.read_scheduled_tasks()
        self.assertEqual([r["name"] for r in rows], [t["name"] for t in st.tasks()])
        wd = rows[0]
        self.assertEqual((wd["status"], wd["schedule"], wd["time_limit_min"]), ("Ready", "Hourly, 24/7", 15))
        for key in ("description", "command", "log", "run_as", "manual_run", "summary"):
            self.assertIn(key, wd)
        self.assertEqual(rows[1]["status"], "Not registered")

    def test_api_without_task_scheduler_still_lists_the_manifest(self):
        with mock.patch.object(data_api, "_live_task_states", return_value=None):
            rows = data_api.read_scheduled_tasks()
        self.assertEqual(len(rows), len(st.tasks()))
        self.assertTrue(all(r["status"] == "N/A" for r in rows))


class TestPreflightDuplicateKey(unittest.TestCase):
    def test_key_is_script_plus_args_or_skill(self):
        cases = {
            r'cmd.exe /c "cd /d "c:\repo" && venv_new\scripts\python.exe ai_portfolio_game.py --run >> "c:\log\x.log" 2>&1"': "ai_portfolio_game.py --run",
            r'"c:\repo\run_agent.cmd" "c:\py\python.exe" "c:\repo\ai_portfolio_game.py" --run': "ai_portfolio_game.py --run",
            r'cmd.exe /c "cd /d "c:\repo" && venv_new\scripts\python.exe ai_portfolio_game.py --summary >> "c:\l" 2>&1"': "ai_portfolio_game.py --summary",
            r'cmd.exe /c "cd /d "c:\r" && venv_new\scripts\python.exe scripts/monitoring/chaikin_reauth.py --notify-days 2.5 >> "c:\l" 2>&1"': "chaikin_reauth.py --notify-days 2.5",
            r'cmd.exe /c ""c:\r\run_agent.cmd" gemini -p "execute the automated skill defined in .claude/commands/daily-run.md" >> "c:\l" 2>&1"': "skill:daily-run",
        }
        for cmd, want in cases.items():
            with self.subTest(want=want):
                self.assertEqual(preflight_validator._run_key(cmd.lower()), want)


class TestBrokenManifestIsLoudNotFatal(unittest.TestCase):
    """Review of #187: the manifest was loaded at import time, so one bad edit (a missing
    'summary') raised ValueError on `import ai_portfolio_game` and stopped the trade run, the
    watchdog and the preflight. Now it fails loudly where it is used, and CI checks it."""

    def _broken(self, d):
        data = json.loads(st.MANIFEST.read_text(encoding="utf-8"))
        del data["tasks"][0]["summary"]
        path = Path(d) / "scheduled_tasks.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_watchdog_reports_a_broken_manifest_and_audits_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            bad = self._broken(d)
            with mock.patch.object(st, "MANIFEST", bad), mock.patch.object(watchdog, "TASKS", None),                  mock.patch("watchdog._log") as log:
                self.assertEqual(watchdog._tasks(), [])
        self.assertIn("missing summary", str(log.error.call_args.args[0]))

    def test_trade_run_still_imports_with_a_broken_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            bad = self._broken(d)
            env = dict(os.environ, AETHER_TASK_MANIFEST=str(bad))
            res = subprocess.run([sys.executable, "-c", "import ai_portfolio_game; print('IMPORT_OK')"],
                                 cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=180,
                                 stdin=subprocess.DEVNULL)
        self.assertIn("IMPORT_OK", res.stdout, res.stderr[-1500:])

    def test_ci_validator_blocks_a_broken_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            bad = self._broken(d)
            with mock.patch.object(pre_commit_validator, "TASK_MANIFEST_PATH", str(bad)):
                self.assertFalse(pre_commit_validator.check_task_manifest())
        self.assertTrue(pre_commit_validator.check_task_manifest())


@unittest.skipUnless(sys.platform == "win32", "the registrar dry run needs Windows PowerShell")
class TestRegistrarDryRun(unittest.TestCase):
    """Runs register_agent_tasks.ps1 -DryRun: it builds every trigger, action and principal and
    prints them, registering nothing."""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(_PS1), "-DryRun"], capture_output=True, text=True, timeout=120,
                             stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.built = {d["name"]: d for d in (json.loads(l) for l in res.stdout.splitlines() if l.strip())}

    def test_every_manifest_task_is_built_as_declared(self):
        tasks = {t["name"]: t for t in st.tasks()}
        self.assertEqual(set(self.built), set(tasks))
        for name, t in tasks.items():
            b = self.built[name]
            with self.subTest(task=name):
                self.assertEqual(b["execute"], "cmd.exe")
                if "script" in t:
                    self.assertIn(st.command_text(t), b["argument"])
                else:
                    self.assertIn("run_agent.cmd", b["argument"])
                self.assertIn(t["log"], b["argument"])
                self.assertEqual(b["interactive"], bool(t.get("interactive")))
                s = t["schedule"]
                if s["type"] == "hourly":
                    self.assertEqual(b["repeat"], "PT1H")
                elif s["type"] == "weekly":
                    self.assertIsNotNone(b["days_of_week"])
                else:
                    self.assertEqual(b["days_interval"], 1)
                if s.get("repeat_every_min"):
                    self.assertEqual(b["repeat"], f"PT{s['repeat_every_min']}M")

    def test_time_limits_match(self):
        def minutes(iso):
            h = re.search(r"(\d+)H", iso)
            m = re.search(r"(\d+)M", iso)
            return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
        for t in st.tasks():
            self.assertEqual(minutes(self.built[t["name"]]["time_limit"]), t["time_limit_min"], t["name"])

    def test_python_launch_keeps_exit_code_and_plain_log(self):
        """Run a built argument for real, with the script swapped for a probe."""
        arg = self.built["AETHER_Watchdog"]["argument"]
        with tempfile.TemporaryDirectory() as d:
            Path(d, "probe.py").write_text("import sys\nsys.stderr.write('a warning' + chr(10))\n"
                                          "sys.exit(int(sys.argv[1]))\n", encoding="utf-8")
            log = Path(d, "agent.log")
            for code in (0, 3):
                probe = (arg.replace(str(_REPO), d)
                         .replace(r"venv_new\Scripts\python.exe watchdog.py", f'"{sys.executable}" probe.py {code}')
                         .replace(str(Path(d) / "Data" / "logs" / "agent_runs" / "watchdog_agent.log"), str(log)))
                res = subprocess.run(f"cmd.exe {probe}", capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
                self.assertEqual(res.returncode, code, res.stderr)
            self.assertFalse(log.read_bytes().startswith(b"\xff\xfe"))


if __name__ == "__main__":
    unittest.main()
