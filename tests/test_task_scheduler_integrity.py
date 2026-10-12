"""Scheduled-task integrity: what the watchdog audits, how tasks launch Python, and what the
preflight treats as a watchdog failure.

PROD 2026-10-10 .. 10-12:
- the watchdog audited the old root tasks (AnalyzeFinData_*). Once those were deleted it logged
  "Detected missing tasks" and emailed a recovery report every hour;
- tasks launch Python through `powershell -Command "... >> log 2>&1"`. Windows PowerShell 5.1
  turns any stderr line of a native program into an error record, so the task's LastTaskResult
  was 1 whenever Python logged a warning, and the log was written as UTF-16;
- the preflight then read AETHER_Watchdog's LastTaskResult = 1 and aborted the 10-12 morning
  pipeline.
"""
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
import preflight_validator
import watchdog

_REPO = Path(__file__).resolve().parent.parent
_PS1 = _REPO / "scripts" / "utils" / "register_agent_tasks.ps1"
_OK = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
_DRIVER = r"\AETHER_Agents\AETHER_DailyDriver"


def _ps1_names():
    return set(re.findall(r'Name\s*=\s*"([^"]+)"', _PS1.read_text(encoding="utf-8")))


class TestWatchdogAuditsTheRegisteredTasks(unittest.TestCase):
    def test_audit_list_is_the_registrar_list_under_aether_agents(self):
        audited = set(watchdog.TASKS)
        self.assertEqual(audited, {f"\\AETHER_Agents\\{n}" for n in _ps1_names()})
        self.assertFalse([t for t in audited if "AnalyzeFinData_" in t], audited)
        for must in ("AETHER_Morning", "AETHER_ExecuteTrades", "AETHER_AftermarketReport", "AETHER_Watchdog"):
            self.assertIn(f"\\AETHER_Agents\\{must}", audited)

    def test_missing_agent_task_is_never_recreated_as_a_legacy_root_task(self):
        with mock.patch("subprocess.run", return_value=_OK) as run, \
             mock.patch.object(watchdog.ctypes, "windll", create=True) as windll, \
             mock.patch("watchdog._log") as log:
            windll.shell32.IsUserAnAdmin.return_value = 1
            watchdog.heal_tasks(list(watchdog.TASKS))
        creates = [c.args[0] for c in run.call_args_list if c.args and c.args[0][:2] == ["schtasks", "/create"]]
        self.assertEqual(creates, [])
        said = " ".join(str(c.args[0]) for c in log.warning.call_args_list)
        self.assertIn("register_agent_tasks.ps1", said)

    def test_legacy_root_tasks_are_reported_as_stray(self):
        listing = ("TaskName: \\AnalyzeFinData_AI_Game\nStatus: Ready\n\n"
                   "TaskName: \\AETHER_Agents\\AETHER_ExecuteTrades\nStatus: Ready\n\n"
                   "TaskName: \\Project_AETHER_Watchdog\nStatus: Ready\n\n")
        res = subprocess.CompletedProcess([], 0, stdout=listing, stderr="")
        with mock.patch("subprocess.run", return_value=res), mock.patch("watchdog._log") as log:
            watchdog.purge_stray_tasks()
        said = " ".join(str(c.args[0]) for c in log.warning.call_args_list)
        self.assertIn("AnalyzeFinData_AI_Game", said)
        self.assertIn("Project_AETHER_Watchdog", said)
        self.assertNotIn("AETHER_ExecuteTrades", said)

    def test_same_name_in_another_folder_is_a_stray(self):
        """Review of #186: a copy of an AETHER task at the root or in another folder is exactly
        the kind of duplicate behind the 10-12 double 05:30 / 07:00 runs; only the full
        \\AETHER_Agents\\ path is whitelisted."""
        listing = ("TaskName: \\AETHER_Agents\\AETHER_ExecuteTrades\nStatus: Ready\n\n"
                   "TaskName: \\AETHER_ExecuteTrades\nStatus: Ready\n\n"
                   "TaskName: \\Other\\AETHER_Morning\nStatus: Ready\n\n")
        res = subprocess.CompletedProcess([], 0, stdout=listing, stderr="")
        with mock.patch("subprocess.run", return_value=res), mock.patch("watchdog._log") as log:
            watchdog.purge_stray_tasks()
        stray_line = " ".join(str(c.args[0]) for c in log.warning.call_args_list if "Stray-Task" in str(c.args[0]))
        self.assertIn("'\\\\AETHER_ExecuteTrades'", stray_line)
        self.assertIn("Other", stray_line)
        self.assertNotIn("AETHER_Agents", stray_line)


class TestFailedTaskAlertOncePerRun(unittest.TestCase):
    """A task that keeps failing (e.g. the Gemini-CLI agents, 403 on every run) must be reported
    once per failed run, not on every hourly watchdog cycle."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(watchdog, "SCHEDULER_AUDIT_STATE", Path(self.tmp.name) / "audit.json")
        p.start()
        self.addCleanup(p.stop)

    def _audit(self, ps_stdout):
        def fake(args, **kwargs):
            if args[0] == "schtasks":
                return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout=ps_stdout, stderr="")
        with mock.patch.object(watchdog, "TASKS", [_DRIVER]), \
             mock.patch("subprocess.run", side_effect=fake), mock.patch("watchdog._log") as log:
            _missing, failed = watchdog.check_task_scheduler()
        return failed, log.error.call_count

    def test_same_failed_run_is_reported_once(self):
        self.assertEqual(self._audit("1|2026-10-11T07:05:00"), ([_DRIVER], 1))
        self.assertEqual(self._audit("1|2026-10-11T07:05:00"), ([], 0))

    def test_a_new_failed_run_is_reported_again(self):
        self._audit("1|2026-10-11T07:05:00")
        self.assertEqual(self._audit("1|2026-10-12T07:05:00")[1], 1)


class TestRegistrar(unittest.TestCase):
    def test_morning_pipeline_is_registered_with_room_to_finish(self):
        ps1 = _PS1.read_text(encoding="utf-8")
        entry = ps1[ps1.index('Name     = "AETHER_Morning"'):]
        entry = entry[:entry.index("}")]
        self.assertIn("autonomous_pipeline.py", entry)
        self.assertGreaterEqual(int(re.search(r"TimeLimitMin\s*=\s*(\d+)", entry).group(1)), 60)

    def test_python_tasks_launch_through_cmd_not_powershell(self):
        ps1 = _PS1.read_text(encoding="utf-8")
        branch = ps1[ps1.index("if ($T.Script) {"):ps1.index("} else {", ps1.index("if ($T.Script) {"))]
        self.assertIn('-Execute "cmd.exe"', branch)
        self.assertNotIn("powershell.exe", branch)

    @unittest.skipUnless(sys.platform == "win32", "cmd.exe launch check runs on Windows only")
    def test_cmd_launch_keeps_python_exit_code_and_plain_text_log(self):
        """Run the registrar's own -Argument template, with its PowerShell escapes resolved."""
        ps1 = _PS1.read_text(encoding="utf-8")
        branch = ps1[ps1.index("if ($T.Script) {"):]
        template = re.search(r'-Argument "(.*?)"\s*$', branch, re.M).group(1).replace('`"', '"')
        with tempfile.TemporaryDirectory() as d:
            Path(d, "warn_then.py").write_text(
                "import sys\nsys.stderr.write('a warning' + chr(10))\nprint('out')\n"
                "sys.exit(int(sys.argv[1]))\n", encoding="utf-8")
            log = Path(d, "agent.log")
            for code in (0, 3):
                script = f'"{sys.executable}" warn_then.py {code}'
                args = (template.replace("$RepoRoot", d).replace("$($T.Script)", script)
                        .replace("$LogFile", str(log)))
                res = subprocess.run(f"cmd.exe {args}", capture_output=True, timeout=60,
                                     stdin=subprocess.DEVNULL)
                self.assertEqual(res.returncode, code, res.stderr)
            raw = log.read_bytes()
            self.assertFalse(raw.startswith(b"\xff\xfe"), "log written as UTF-16")
            self.assertIn(b"a warning", raw)


class TestPreflightWatchdogResult(unittest.TestCase):
    def _health(self, last_result):
        csv_out = f'"TaskName","Last Result"\n"\\AETHER_Agents\\AETHER_Watchdog","{last_result}"\n'
        res = subprocess.CompletedProcess([], 0, stdout=csv_out, stderr="")
        with tempfile.TemporaryDirectory() as d:
            log_dir = Path(d) / "Data" / "logs" / "agent_runs"
            log_dir.mkdir(parents=True)
            (log_dir / "watchdog_agent.log").write_text("ok\n")
            with mock.patch.object(preflight_validator.sys, "platform", "win32"), \
                 mock.patch.object(preflight_validator.subprocess, "run", return_value=res), \
                 mock.patch.object(preflight_validator, "_log"):
                return preflight_validator.check_watchdog_health(Path(d))

    def test_refused_because_already_running_is_not_a_failure(self):
        ok, issues = self._health("-2147020576")   # 0x800710E0: instance already running
        self.assertTrue(ok, issues)

    def test_real_failure_still_fails(self):
        ok, issues = self._health("2")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()


class TestRecoveryReportWording(unittest.TestCase):
    """The watchdog no longer heals missing tasks (it only warns), so the recovery report must
    not say "Healed missing tasks" (AGENT.md: no greenwashing)."""

    @mock.patch("watchdog.subprocess.run", new=mock.MagicMock(return_value=subprocess.CompletedProcess([], 0, "", "")))
    @mock.patch("watchdog.notify.send_email")
    @mock.patch("watchdog.is_pid_running", return_value=False)
    @mock.patch("watchdog.check_data_freshness", return_value=None)
    @mock.patch("watchdog.check_logs", return_value=[])
    @mock.patch("watchdog.powergauge.ensure_valid_session", return_value={"jsessionid": "x"})
    @mock.patch("watchdog.etrade.scheduled_reauth", return_value={"ok": True, "reason": "renewed"})
    @mock.patch("watchdog.check_task_scheduler", return_value=([r"\AETHER_Agents\AETHER_AI_Summary"], []))
    @mock.patch("watchdog._log")
    def test_missing_task_is_reported_as_missing_not_healed(self, _log, _cts, _re, _ses, _logs, _fresh, _pid, email):
        watchdog.run_watchdog()
        self.assertTrue(email.called)
        html = email.call_args.args[1]
        self.assertNotIn("Healed missing tasks", html)
        self.assertIn("AETHER_AI_Summary", html)
        self.assertIn("register_agent_tasks.ps1", html)
