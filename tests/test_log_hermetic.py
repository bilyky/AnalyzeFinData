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
import ai_portfolio_game
import autonomous_pipeline
import bootstrap_dna
import daily_task
import powergauge
import rapidapi
import watchdog
import workbook_read
from aether import config as aether_config
from aether import paths, risk_utils, run_guard, trash
import tests as harness
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
        files = [h.baseFilename for h in daily_task._log.handlers if isinstance(h, logging.FileHandler)]
        self.assertTrue(files)
        repo = _REPO_DATA.parent
        self.assertEqual([f for f in files if Path(f).resolve().parent == repo or _inside_repo_data(f)], [])



class TestGameStateRedirected(unittest.TestCase):
    """The game state file and its backups never resolve into the repo's Data/.

    save_game() backs the file up into GAME_BACKUP_DIR and then PRUNES that folder to
    the last 15 backups, so redirecting the file alone would delete real backups. Each
    behavioural test asserts the redirect first, so a broken harness fails here
    without ever writing a production file.
    """

    def _assert_redirected(self):
        for name, path in [("ai_portfolio_game.AI_GAME_FILE", ai_portfolio_game.AI_GAME_FILE),
                           ("ai_portfolio_game.GAME_BACKUP_DIR", ai_portfolio_game.GAME_BACKUP_DIR),
                           ("workbook_read.GAME_FILE", workbook_read.GAME_FILE),
                           ("bootstrap_dna.GAME_FILE", bootstrap_dna.GAME_FILE)]:
            self.assertFalse(_inside_repo_data(path), f"{name} -> {path}")

    def test_game_paths_redirected(self):
        self._assert_redirected()

    def test_save_and_load_stay_in_the_temp_dir(self):
        self._assert_redirected()  # must hold BEFORE anything is written
        state = {"balance": 123.0, "equity": 123.0, "positions": {}, "history": []}
        ai_portfolio_game.save_game(state)
        ai_portfolio_game.save_game(state)  # 2nd save backs up the 1st into GAME_BACKUP_DIR
        self.assertEqual(ai_portfolio_game.load_game()["balance"], 123.0)
        self.assertTrue(any(ai_portfolio_game.GAME_BACKUP_DIR.glob("ai_portfolio_game_*.json")))


class TestMarketDataCachesRedirected(unittest.TestCase):
    """Every consumer of Data/Symbol and Data/Symbol_full sees the temp cache in tests.

    On 2026-10-06 removing a worktree whose cache dirs were junctions into the main
    checkout deleted the real caches; the caches now resolve through
    aether.paths.cache_dir() (AETHER_CACHE_DIR), which the harness pins to temp first.
    """

    def test_cache_constants_point_outside_repo_data(self):
        for name, path in [("paths.symbol_dir()", paths.symbol_dir()),
                           ("paths.ohlcv_dir()", paths.ohlcv_dir()),
                           ("risk_utils.OHLCV_DIR", risk_utils.OHLCV_DIR),
                           ("rapidapi.OHLCV_DIR", rapidapi.OHLCV_DIR),
                           ("powergauge.OHLCV_DIR", powergauge.OHLCV_DIR),
                           ("ai_portfolio_game.SYMBOL_FULL_DIR", ai_portfolio_game.SYMBOL_FULL_DIR)]:
            with self.subTest(name=name):
                self.assertFalse(_inside_repo_data(path), f"{name} -> {path}")

class TestLocksTrashAndConfigRedirected(unittest.TestCase):
    """Singleton locks, the trash, the run-guard dir and the config stay out of the repo.

    run_watchdog() / autonomous_pipeline.main() overwrite their PID lock and
    force-delete it at exit, and run_watchdog() purges the trash, so before this
    guard a suite run deleted the REAL Data/watchdog_run.lock, pipeline_run.lock
    and every >30-day file in Data/.trash (proved by planting probe files). Tests
    also read the real config.json, whose E*TRADE TOTP secret changes which
    get_tokens path runs, so the suite behaved differently in the main checkout.
    """

    def test_lock_trash_and_run_guard_paths_redirected(self):
        for name, path in [("watchdog.WATCHDOG_LOCK_FILE", watchdog.WATCHDOG_LOCK_FILE),
                           ("watchdog.SELF_HEAL_LOCK", watchdog.SELF_HEAL_LOCK),
                           ("watchdog.SELF_HEAL_PROMPT_FILE", watchdog.SELF_HEAL_PROMPT_FILE),
                           ("watchdog.DATA_SENTINEL_FILE", watchdog.DATA_SENTINEL_FILE),
                           ("watchdog.BACKUP_STATUS_FILE", watchdog.BACKUP_STATUS_FILE),
                           ("watchdog.DATA_ALERT_MARKER", watchdog.DATA_ALERT_MARKER),
                           ("autonomous_pipeline.PIPELINE_LOCK_FILE", autonomous_pipeline.PIPELINE_LOCK_FILE),
                           ("trash.TRASH_DIR", trash.TRASH_DIR),
                           ("run_guard._DATA_DIR", run_guard._DATA_DIR)]:
            with self.subTest(name=name):
                self.assertFalse(_inside_repo_data(path), f"{name} -> {path}")

    def test_trash_purge_only_touches_the_temp_trash(self):
        self.assertFalse(_inside_repo_data(trash.TRASH_DIR))  # must hold BEFORE purging
        os.makedirs(trash.TRASH_DIR, exist_ok=True)
        probe = Path(trash.TRASH_DIR) / "20000101T000000.probe.json"
        probe.write_text("{}", encoding="utf-8")
        os.utime(probe, (946684800, 946684800))  # 2000-01-01, far past retention
        self.assertGreaterEqual(trash.purge_trash(), 1)
        self.assertFalse(probe.exists())

    @unittest.skipIf(os.getenv("AETHER_LIVE_TESTS"), "live mode uses the real config on purpose")
    def test_real_config_json_is_not_loaded(self):
        self.assertFalse(Path(aether_config._CFG_PATH).exists(), aether_config._CFG_PATH)
        self.assertEqual(aether_config.CFG.etrade_totp_secret, os.environ.get("ETRADE_TOTP_SECRET", ""))


class TestProcessSideEffectsBlocked(unittest.TestCase):
    def test_kill_game_launch_and_scheduler_changes_are_refused(self):
        # Checked against the guard itself, so a regression can never actually run one
        # of these (e.g. create a real scheduled task).
        for argv in (["taskkill", "/F", "/PID", "0"],
                     ["powershell", "-Command", "Get-Process python | Stop-Process -Force"],
                     [sys.executable, "ai_portfolio_game.py", "--report"],
                     "taskkill /F /PID 0",
                     ["schtasks", "/Create", "/TN", "AETHER_probe", "/TR", "x"],
                     ["schtasks", "/Delete", "/TN", "AETHER_probe", "/F"],
                     ["powershell", "-Command", "Register-ScheduledTask -TaskName AETHER_probe"],
                     ["powershell", "-Command", "Unregister-ScheduledTask -TaskName AETHER_probe"],
                     # the executable spelled as schtasks.exe, a full path, or a quoted path
                     ["schtasks.exe", "/Create", "/TN", "AETHER_probe", "/TR", "x"],
                     [r"C:\Windows\System32\schtasks.exe", "/Delete", "/TN", "AETHER_probe", "/F"],
                     r'"C:\Windows\System32\schtasks.exe" /Change /TN AETHER_probe /DISABLE',
                     "SCHTASKS.EXE  /RUN /TN AETHER_probe",
                     [r"C:\Windows\System32\taskkill.exe", "/PID", "0"]):
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
        for argv in (["schtasks", "/query", "/tn", "AETHER_probe"],
                     [r"C:\Windows\System32\schtasks.exe", "/Query", "/TN", "AETHER_probe"],
                     ["powershell", "-Command", "Get-ScheduledTask -TaskName AETHER_probe"]):
            with self.subTest(argv=argv):
                harness._guard_proc(argv)  # raises RuntimeError if refused


if __name__ == "__main__":
    unittest.main()
