"""The test harness keeps every log write out of the repo, and no test may kill a real
process or launch the real game.

aether.logger opens its rotating file handlers at import time (the module-level
``log = get_logger("aether")``), so redirecting ``_LOG_DIR`` afterwards is too late:
before this guard, every suite run appended its log lines to the real
Data/logs/aether.{log,jsonl}. autonomous_pipeline.log() also appends to its own
LOG_FILE_PATH (Data/autonomous_run.log, read by watchdog and the dashboard), which
was never redirected. daily_task.py attaches its own repo-root daily_task.log handler at
import. And an unmocked watchdog.run_watchdog() ran the live process supervisor
(taskkill of duplicate server.py / "orphaned" consoles) plus a real
`ai_portfolio_game.py --report` child.
"""
import logging
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import autonomous_pipeline
from aether import logger as aether_logger

_REPO_DATA = (Path(__file__).resolve().parent.parent / "Data").resolve()


def _inside_repo_data(path) -> bool:
    try:
        Path(path).resolve().relative_to(_REPO_DATA)
        return True
    except ValueError:
        return False


class TestLogPathsRedirected(unittest.TestCase):
    def test_no_aether_file_handler_writes_into_repo_data(self):
        files = [h.baseFilename for h in logging.getLogger("aether").handlers
                 if isinstance(h, logging.FileHandler)]
        self.assertTrue(files, "expected the aether logger to have file handlers")
        self.assertEqual([f for f in files if _inside_repo_data(f)], [])

    def test_log_dir_redirected(self):
        self.assertFalse(_inside_repo_data(aether_logger._LOG_DIR), aether_logger._LOG_DIR)

    def test_pipeline_legacy_log_redirected(self):
        self.assertFalse(_inside_repo_data(autonomous_pipeline.LOG_FILE_PATH),
                         autonomous_pipeline.LOG_FILE_PATH)

    def test_logging_leaves_repo_data_untouched(self):
        targets = [_REPO_DATA / "logs" / "aether.log", _REPO_DATA / "logs" / "aether.jsonl",
                   _REPO_DATA / "autonomous_run.log"]
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in targets}
        aether_logger.get_logger("hermetic_probe").info("hermetic probe")
        autonomous_pipeline.log("hermetic probe")
        for h in logging.getLogger("aether").handlers:
            h.flush()
        after = {p: (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in targets}
        self.assertEqual(after, before)

    def test_daily_task_logger_writes_outside_repo(self):
        import daily_task
        files = [h.baseFilename for h in daily_task._log.handlers if isinstance(h, logging.FileHandler)]
        self.assertTrue(files)
        repo = _REPO_DATA.parent
        self.assertEqual([f for f in files if Path(f).resolve().parent == repo or _inside_repo_data(f)], [])


class TestProcessSideEffectsBlocked(unittest.TestCase):
    def test_kill_game_launch_and_scheduler_changes_are_refused(self):
        # Checked against the guard itself, so a regression can never actually run one
        # of these (e.g. create a real scheduled task).
        import tests as harness
        for argv in (["taskkill", "/F", "/PID", "0"],
                     ["powershell", "-Command", "Get-Process python | Stop-Process -Force"],
                     [sys.executable, "ai_portfolio_game.py", "--report"],
                     "taskkill /F /PID 0",
                     ["schtasks", "/Create", "/TN", "AETHER_probe", "/TR", "x"],
                     ["schtasks", "/Delete", "/TN", "AETHER_probe", "/F"],
                     ["powershell", "-Command", "Register-ScheduledTask -TaskName AETHER_probe"],
                     ["powershell", "-Command", "Unregister-ScheduledTask -TaskName AETHER_probe"]):
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(RuntimeError, "Blocked real process side effect"):
                    harness._guard_proc(argv)

    def test_guard_is_wired_into_run_and_popen(self):
        # End to end through the real entry points, with the one argv that is harmless
        # even if the guard were missing (PID 0 cannot be killed).
        for launch in (subprocess.run, subprocess.Popen):
            with self.subTest(launch=launch.__name__):
                with self.assertRaisesRegex(RuntimeError, "Blocked real process side effect"):
                    launch(["taskkill", "/PID", "0"])

    def test_ordinary_commands_still_run(self):
        out = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "ok")

    def test_read_only_scheduler_queries_are_not_refused(self):
        # Exercise the guard itself (nothing is executed): queries must pass through.
        import tests as harness
        for argv in (["schtasks", "/query", "/tn", "AETHER_probe"],
                     ["powershell", "-Command", "Get-ScheduledTask -TaskName AETHER_probe"]):
            with self.subTest(argv=argv):
                harness._guard_proc(argv)  # raises RuntimeError if refused


if __name__ == "__main__":
    unittest.main()
