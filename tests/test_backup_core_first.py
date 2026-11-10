"""The nightly backup copies the core state files first, then the full Data/ tree.

PROD 11-04 → 11-08: the full robocopy timed out every night (600 s, then 1500 s), so no state file was
backed up for five nights. The top-level files (game state, ledgers, workbook, config) are small, so
they go first with their own timeout and their own success time (PROD review 2026-11-09).
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import watchdog


def _ok(*_a, **_k):
    return subprocess.CompletedProcess([], 1, stdout="", stderr="")


class BackupCoreFirstTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.status_file = Path(self._td.name) / "backup_status.json"
        self._patches = [
            mock.patch.object(watchdog, "BACKUP_STATUS_FILE", self.status_file),
            mock.patch.object(watchdog, "is_market_hours", return_value=False),
            mock.patch("os.path.exists", return_value=True),
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "mkdir"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._td.cleanup()

    def _status(self):
        return json.loads(self.status_file.read_text(encoding="utf-8"))

    def test_core_files_go_first_without_recursion(self):
        with mock.patch("subprocess.run", side_effect=_ok) as run:
            self.assertTrue(watchdog.sync_data_folder())
        core_cmd, full_cmd = run.call_args_list[0].args[0], run.call_args_list[1].args[0]
        self.assertNotIn("/E", core_cmd)
        self.assertIn(f"/MAX:{watchdog.CORE_MAX_BYTES}", core_cmd)        # archives wait for the full pass
        self.assertIn("/E", full_cmd)
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], watchdog.CORE_SYNC_TIMEOUT_S)
        st = self._status()
        self.assertEqual(st["last_result"], "ok")
        self.assertIn("last_core_success", st)

    def test_full_timeout_still_records_the_core_copy(self):
        def run(cmd, **kw):
            if "/E" in cmd:
                raise subprocess.TimeoutExpired(cmd="robocopy", timeout=kw["timeout"])
            return _ok()
        with mock.patch("subprocess.run", side_effect=run), \
             self.assertLogs("aether.watchdog", level="WARNING") as logs:
            self.assertFalse(watchdog.sync_data_folder())
        st = self._status()
        self.assertEqual(st["last_result"], "timeout")
        self.assertIn("last_core_success", st)
        self.assertNotIn("last_success", st)
        self.assertTrue(any(f"{watchdog.SYNC_TIMEOUT_S}s" in m for m in logs.output), logs.output)
        self.assertFalse(any("600s" in m for m in logs.output), logs.output)

    def test_health_message_for_a_timeout_does_not_blame_the_share(self):
        self.status_file.write_text(json.dumps({
            "last_attempt": "2026-11-08T22:10:05", "last_result": "timeout",
            "last_success": "2026-11-03T22:12:09", "last_core_success": "2026-11-08T22:10:40"}),
            encoding="utf-8")
        msgs = watchdog.check_backup_health()
        self.assertEqual(len(msgs), 1)
        self.assertNotIn("unreachable", msgs[0])
        self.assertIn("2026-11-08T22:10:40", msgs[0])


if __name__ == "__main__":
    unittest.main()
