"""
Guards against the PROD watchdog hang (2026-09-22 .. 10-08).

On PROD every watchdog run that took the singleton lock stopped at
"Healing/Upgrading scheduled task: AnalyzeFinData_Morning" and never logged the
schtasks result. The run held Data/watchdog_run.lock for 8h to 4 days, and every
later hourly run exited with "another instance is already running". Preflight
still passed, because the blocked runs kept watchdog_agent.log fresh.

Only the subprocess boundary is mocked; the real heal / lock / preflight code runs.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
import preflight_validator
import watchdog

_REPO = Path(__file__).resolve().parent.parent
_OK = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")


class TestHealTasksNeverBlocks(unittest.TestCase):
    def _heal(self, run_side_effect, tasks=("AnalyzeFinData_Morning", "AnalyzeFinData_Evening")):
        with mock.patch("subprocess.run", side_effect=run_side_effect) as run, \
             mock.patch.object(watchdog.ctypes, "windll", create=True) as windll, \
             mock.patch("watchdog._log") as log:
            windll.shell32.IsUserAnAdmin.return_value = 1
            watchdog.heal_tasks(list(tasks))
        return run, log

    def test_every_subprocess_call_has_timeout_and_no_stdin(self):
        run, _ = self._heal(lambda *a, **k: _OK)
        self.assertGreater(run.call_count, 0)
        for call in run.call_args_list:
            self.assertIsNotNone(call.kwargs.get("timeout"), f"no timeout: {call.args[0][:3]}")
            self.assertIs(call.kwargs.get("stdin"), subprocess.DEVNULL, f"stdin inherited: {call.args[0][:3]}")

    def test_hung_schtasks_is_logged_and_next_task_still_healed(self):
        def side_effect(args, **kwargs):
            if args[0] == "schtasks" and "\\AnalyzeFinData_Morning" in args:
                raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
            return _OK
        run, log = self._heal(side_effect)
        errors = " ".join(str(c.args[0]) for c in log.error.call_args_list)
        self.assertIn("AnalyzeFinData_Morning", errors)
        self.assertIn("timed out", errors)
        healed = [c.args[0] for c in run.call_args_list if c.args[0][0] == "schtasks"]
        self.assertTrue(any("\\AnalyzeFinData_Evening" in a for a in healed))


class TestSingletonLockReclaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock = Path(self.tmp.name) / "watchdog_run.lock"
        self.addCleanup(self.tmp.cleanup)

    def _write_lock(self, pid, age_min):
        self.lock.write_text(str(pid))
        t = time.time() - age_min * 60
        os.utime(self.lock, (t, t))

    def _acquire(self, pid_running=True, cmdline="python.exe watchdog.py"):
        def fake_run(args, **kwargs):
            if args[0] == "powershell" and "Win32_Process" in args[-1]:
                return subprocess.CompletedProcess(args, 0, stdout=cmdline + "\n", stderr="")
            return _OK
        with mock.patch.object(watchdog, "WATCHDOG_LOCK_FILE", self.lock), \
             mock.patch("watchdog.is_pid_running", return_value=pid_running), \
             mock.patch("subprocess.run", side_effect=fake_run) as run, \
             mock.patch("watchdog.atexit.register"), \
             mock.patch("watchdog._log") as log:
            got = watchdog._acquire_singleton_lock()
        return got, run, log

    def test_stale_lock_whose_pid_is_now_another_python_is_reclaimed_without_kill(self):
        """Windows reuses PIDs. After a reboot the lock's PID can belong to server.py or the game;
        killing that (with /T) mid-write is worse than the hang."""
        self._write_lock(4242, age_min=watchdog.WATCHDOG_LOCK_MAX_AGE_MIN + 5)
        got, run, _ = self._acquire(cmdline="python.exe ai_portfolio_game.py")
        self.assertTrue(got)
        self.assertEqual(self.lock.read_text(), str(os.getpid()))
        self.assertEqual([c for c in run.call_args_list if c.args[0][0] == "taskkill"], [])

    def test_fresh_lock_held_by_live_pid_blocks(self):
        self._write_lock(4242, age_min=10)
        got, run, _ = self._acquire()
        self.assertFalse(got)
        self.assertEqual(self.lock.read_text(), "4242")
        run.assert_not_called()

    def test_stale_lock_held_by_live_pid_is_killed_and_reclaimed(self):
        self._write_lock(4242, age_min=watchdog.WATCHDOG_LOCK_MAX_AGE_MIN + 5)
        got, run, log = self._acquire()
        self.assertTrue(got)
        self.assertEqual(self.lock.read_text(), str(os.getpid()))
        kill = [c.args[0] for c in run.call_args_list if c.args[0][0] == "taskkill"]
        self.assertEqual(kill, [["taskkill", "/F", "/T", "/PID", "4242"]])
        self.assertTrue(any("4242" in str(c.args[0]) for c in log.error.call_args_list))

    def test_lock_of_dead_pid_is_reclaimed_without_kill(self):
        self._write_lock(4242, age_min=10)
        got, run, _ = self._acquire(pid_running=False)
        self.assertTrue(got)
        run.assert_not_called()


class TestPreflightSeesHungWatchdog(unittest.TestCase):
    def test_stale_lock_fails_even_when_log_is_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            log_dir = base / "Data" / "logs" / "agent_runs"
            log_dir.mkdir(parents=True)
            (log_dir / "watchdog_agent.log").write_text("blocked: another instance is already running\n")
            lock = base / "Data" / "watchdog_run.lock"
            lock.write_text("4242")
            t = time.time() - (watchdog.WATCHDOG_LOCK_MAX_AGE_MIN + 5) * 60
            os.utime(lock, (t, t))
            with mock.patch.object(preflight_validator.sys, "platform", "linux"), \
                 mock.patch.object(preflight_validator, "_log"):
                ok, issues = preflight_validator.check_watchdog_health(base)
        self.assertFalse(ok)
        self.assertTrue(any("watchdog_run.lock" in i for i in issues), issues)

    def test_no_lock_and_fresh_log_passes(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            log_dir = base / "Data" / "logs" / "agent_runs"
            log_dir.mkdir(parents=True)
            (log_dir / "watchdog_agent.log").write_text("cycle done\n")
            with mock.patch.object(preflight_validator.sys, "platform", "linux"), \
                 mock.patch.object(preflight_validator, "_log"):
                ok, issues = preflight_validator.check_watchdog_health(base)
        self.assertTrue(ok, issues)


class TestBackupEntryPointImports(unittest.TestCase):
    def test_runs_from_scheduler_without_repo_root_on_path(self):
        """PROD: 27/27 runs died with ModuleNotFoundError: No module named 'watchdog'.

        `python scripts/utils/run_data_backup.py` puts only scripts/utils on sys.path. Load the
        script the same way (isolated mode, cwd outside the repo, not as __main__ so no sync runs).
        """
        script = _REPO / "scripts" / "utils" / "run_data_backup.py"
        probe = (
            "import runpy, sys; "
            f"sys.path.insert(0, {str(script.parent)!r}); "
            f"runpy.run_path({str(script)!r}, run_name='probe'); print('IMPORT_OK')"
        )
        with tempfile.TemporaryDirectory() as cwd:
            res = subprocess.run([sys.executable, "-I", "-c", probe], cwd=cwd,
                                 capture_output=True, text=True, timeout=120,
                                 stdin=subprocess.DEVNULL)
        self.assertIn("IMPORT_OK", res.stdout, res.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
