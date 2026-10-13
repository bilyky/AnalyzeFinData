"""One Data backup a day, after the day's data collection is finished.

PROD 2026-10-06..10-12: sync_data_folder() was called at the end of seven scripts (main.py,
autonomous_pipeline.py, ai_portfolio_game.py --run and --summary, external_intel.py,
daily_task.py, the backup task): two robocopies every morning around 05:50 (before the 06:30
market-hours guard), up to four in the evening, one at 18:00. Now only the AETHER_Data_Backup
task (nightly) syncs, and it first waits for any data-collecting script to finish.
"""
import ast
import datetime
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import daily_task
import run_data_backup
import watchdog
from aether import scheduled_tasks

_REPO = Path(__file__).resolve().parent.parent


def _callers_of_sync():
    out = []
    for path in _REPO.rglob("*.py"):
        rel = path.relative_to(_REPO).as_posix()
        if rel.startswith(("tests/", "venv", "_wt_", ".claude/")) or "/_wt_" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name == "sync_data_folder":
                    out.append(rel)
    return sorted(set(out))


class TestOnlyTheBackupTaskSyncs(unittest.TestCase):
    def test_sync_is_called_from_the_backup_entry_point_only(self):
        self.assertEqual(_callers_of_sync(), ["scripts/utils/run_data_backup.py"])

    def test_backup_runs_at_22_with_room_to_wait(self):
        t = {x["name"]: x for x in scheduled_tasks.tasks()}["AETHER_Data_Backup"]
        # 22:10, not 22:00: the hourly watchdog starts on the hour and writes under Data/ (review of #193).
        self.assertEqual(t["schedule"], {"type": "daily", "at": "22:10"})
        self.assertGreaterEqual(t["time_limit_min"] * 60,
                                run_data_backup.MAX_WAIT_MIN * 60 + watchdog.SYNC_TIMEOUT_S)


class TestBackupWaitsForCollection(unittest.TestCase):
    def _main(self, collectors, last_result="ok"):
        with mock.patch.object(run_data_backup, "running_collectors", side_effect=collectors) as rc, \
             mock.patch.object(run_data_backup.time, "sleep") as sleep, \
             mock.patch.object(run_data_backup.watchdog, "sync_data_folder", return_value=True) as sync, \
             mock.patch.object(run_data_backup.watchdog, "backup_status",
                               side_effect=[{"last_attempt": "before"},
                                            {"last_attempt": "now", "last_result": last_result}]), \
             mock.patch.object(run_data_backup, "_log") as log:
            rc_code = run_data_backup.main()
        return rc_code, rc, sleep, sync, log

    def test_waits_until_collectors_finish_then_syncs_once(self):
        code, rc, sleep, sync, log = self._main([["daily_task.py", "rapidapi.py"], ["rapidapi.py"], []])
        self.assertEqual(code, 0)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn("rapidapi.py", " ".join(str(c.args[0]) for c in log.info.call_args_list))

    def test_gives_up_waiting_at_the_deadline_and_still_backs_up(self):
        polls = run_data_backup.MAX_WAIT_MIN * 60 // run_data_backup.WAIT_STEP_S + 1
        code, rc, sleep, sync, log = self._main([["daily_task.py"]] * (polls + 5))
        self.assertEqual(sync.call_count, 1)
        self.assertTrue(log.warning.called)

    def test_a_skipped_sync_is_a_failed_backup(self):
        """sync_data_folder() returns True without recording an attempt inside market hours."""
        with mock.patch.object(run_data_backup, "running_collectors", return_value=[]), \
             mock.patch.object(run_data_backup.watchdog, "sync_data_folder", return_value=True), \
             mock.patch.object(run_data_backup.watchdog, "backup_status",
                               return_value={"last_attempt": "yesterday", "last_result": "ok"}), \
             mock.patch.object(run_data_backup, "_log"):
            self.assertEqual(run_data_backup.main(), 1)

    def test_an_offline_share_is_a_failed_backup(self):
        code, *_ = self._main([[]], last_result="offline")
        self.assertEqual(code, 1)


class TestRunningCollectors(unittest.TestCase):
    def test_finds_collector_scripts_but_not_itself(self):
        listing = "\n".join([
            f"{os.getpid()}|python.exe scripts/utils/run_data_backup.py",
            r"4100|C:\venv\python.exe daily_task.py",
            r"4200|C:\venv\python.exe C:\Develop\StockTrading\AnalyzeFinData\rapidapi.py",
            r"4300|C:\venv\python.exe server.py serve",
            r"4400|C:\venv\python.exe watchdog.py",
        ])
        res = subprocess.CompletedProcess([], 0, stdout=listing, stderr="")
        with mock.patch.object(run_data_backup.subprocess, "run", return_value=res) as run:
            got = run_data_backup.running_collectors()
        self.assertEqual(sorted(got), ["daily_task.py", "rapidapi.py"])
        self.assertIsNotNone(run.call_args.kwargs.get("timeout"))

    def test_a_failed_process_query_means_nothing_to_wait_for(self):
        with mock.patch.object(run_data_backup.subprocess, "run", side_effect=OSError("no powershell")):
            self.assertEqual(run_data_backup.running_collectors(), [])


class TestEveningEmailBackupNote(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 12, 17, 30)

    def test_recent_backup_needs_no_banner(self):
        html, subject = daily_task._backup_note({"last_success": "2026-10-11T22:05:00", "last_result": "ok"},
                                                self.NOW, "2026-10-12")
        self.assertEqual(html, "")
        self.assertNotIn("Backup", subject)

    def test_stale_or_missing_backup_is_flagged(self):
        for status in ({"last_success": "2026-10-10T22:05:00", "last_result": "offline"}, {}):
            with self.subTest(status=status):
                html, subject = daily_task._backup_note(status, self.NOW, "2026-10-12")
                self.assertIn("backup", html.lower())
                self.assertIn("Backup Stale", subject)


if __name__ == "__main__":
    unittest.main()
