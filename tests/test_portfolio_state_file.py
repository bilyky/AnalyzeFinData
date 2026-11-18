"""JsonStateFile: the game's load/save, moved into the portfolio service (R&D #78 S4a).

These pin today's behaviour, gaps included. The two tests marked F1 and F2 describe what
those fixes change; they are rewritten in the fix PRs, not deleted.
Everything runs in a temp dir; nothing touches Data/.
"""
import datetime
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ai_portfolio_game
from aether.services.portfolio import api
from aether.services.portfolio.adapters.json_file import JsonStateFile

_STATE = {"balance": 812.5, "equity": 10450.0, "positions": {"AAA": {"shares": 3}}, "history": [{"type": "BUY"}]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.file = self.root / "ai_portfolio_game.json"
        self.backups = self.root / "Backup" / "Game"
        self.log = mock.Mock()
        self.store = JsonStateFile(self.file, self.backups, initial_balance=10000.0, log=self.log)

    def tearDown(self):
        self._tmp.cleanup()

    def _backup(self, name, text, age_s):
        self.backups.mkdir(parents=True, exist_ok=True)
        p = self.backups / f"ai_portfolio_game_{name}.json"
        p.write_text(text, encoding="utf-8")
        t = time.time() - age_s
        os.utime(p, (t, t))
        return p

    def _fresh(self):
        return {"balance": 10000.0, "equity": 10000.0, "positions": {}, "history": [],
                "start_date": str(datetime.date.today()), "profile": "BALANCED"}


class TestLoad(_Base):
    def test_reads_the_file(self):
        self.file.write_text(json.dumps(_STATE), encoding="utf-8")
        self.assertEqual(self.store.load(), _STATE)
        self.log.warning.assert_not_called()

    def test_first_run_is_a_fresh_state_and_writes_nothing(self):
        self.assertEqual(self.store.load(), self._fresh())
        self.assertFalse(self.file.exists())

    def test_missing_or_empty_file_restores_the_newest_readable_backup(self):
        for setup in ("missing", "empty"):
            with self.subTest(setup=setup):
                if setup == "empty":
                    self.file.write_text("", encoding="utf-8")
                self._backup("20261101_090000", json.dumps({"balance": 1.0}), age_s=300)
                self._backup("20261102_090000", json.dumps(_STATE), age_s=200)
                self._backup("20261103_090000", "{broken", age_s=100)  # newest, unreadable: skipped
                self.assertEqual(self.store.load(), _STATE)
                self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)  # copied back
                self.assertIn("missing or empty", self.log.warning.call_args[0][0])
                self.file.unlink()

    def test_corrupt_file_restores_the_newest_readable_backup(self):
        self.file.write_text("{not json", encoding="utf-8")
        self._backup("20261102_090000", json.dumps(_STATE), age_s=100)
        self.assertEqual(self.store.load(), _STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        messages = [c[0][0] for c in self.log.warning.call_args_list]
        self.assertIn("Error loading ai_portfolio_game.json", messages[0])
        self.assertIn("restored corrupted file from backup: ai_portfolio_game_20261102_090000.json", messages[1])

    def test_F2_corrupt_file_without_a_backup_becomes_a_fresh_state(self):
        # Today's behaviour, and the F2 bug: real positions are silently replaced by $10k cash.
        self.file.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load(), self._fresh())


class TestSave(_Base):
    def test_first_save_writes_without_a_backup(self):
        self.store.save(_STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        self.assertFalse(self.backups.exists())

    def test_format_is_indent_4_ascii(self):
        state = {"note": "café", "n": 1}
        self.store.save(state)
        self.assertEqual(self.file.read_text(encoding="utf-8").replace("\r\n", "\n"), json.dumps(state, indent=4))

    def test_save_backs_up_the_previous_file_first(self):
        self.file.write_text(json.dumps({"balance": 1.0}), encoding="utf-8")
        self.store.save(_STATE)
        (backup,) = self.backups.glob("ai_portfolio_game_*.json")
        self.assertRegex(backup.name, r"^ai_portfolio_game_\d{8}_\d{6}\.json$")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"balance": 1.0})
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)

    def test_keeps_the_newest_15_backups(self):
        self.file.write_text("{}", encoding="utf-8")
        old = [self._backup(f"2026100{i // 10}_0{i % 10}0000", "{}", age_s=10_000 - i) for i in range(16)]
        self.store.save(_STATE)
        left = set(self.backups.glob("ai_portfolio_game_*.json"))
        self.assertEqual(len(left), 15)
        self.assertNotIn(old[0], left)  # the two oldest go
        self.assertNotIn(old[1], left)

    def test_F1_a_failed_backup_still_writes(self):
        # Today's behaviour, and the F1 bug: the state is overwritten with no backup taken.
        self.file.write_text(json.dumps({"balance": 1.0}), encoding="utf-8")
        with mock.patch("aether.services.portfolio.adapters.json_file.shutil.copy2", side_effect=OSError("disk full")):
            self.store.save(_STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        self.assertIn("Game backup failed: disk full", self.log.warning.call_args[0][0])


class TestGameDelegates(unittest.TestCase):
    """ai_portfolio_game.load_game/save_game go through the service with the game's own paths."""

    def test_uses_the_game_paths_balance_and_logger(self):
        with mock.patch.object(api, "JsonStateFile") as cls:
            ai_portfolio_game.load_game()
            ai_portfolio_game.save_game(_STATE)
        for call in cls.call_args_list:
            self.assertEqual(call.args, (ai_portfolio_game.AI_GAME_FILE, ai_portfolio_game.GAME_BACKUP_DIR))
            self.assertEqual(call.kwargs, {"initial_balance": ai_portfolio_game.INITIAL_BALANCE,
                                           "log": ai_portfolio_game._log})
        cls.return_value.load.assert_called_once_with()
        cls.return_value.save.assert_called_once_with(_STATE)

    def test_paths_are_read_at_call_time(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ai_portfolio_game, "AI_GAME_FILE", Path(d) / "g.json"), \
                mock.patch.object(ai_portfolio_game, "GAME_BACKUP_DIR", Path(d) / "b"):
            ai_portfolio_game.save_game(_STATE)
            self.assertEqual(ai_portfolio_game.load_game(), _STATE)
            self.assertTrue((Path(d) / "g.json").exists())


if __name__ == "__main__":
    unittest.main()
