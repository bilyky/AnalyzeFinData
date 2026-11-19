"""JsonPortfolioRepository: the game's load/save, moved into the portfolio service (R&D #78 S4).

The file behaviour is pinned through ``LegacyStateGateway``, the path the game uses. These
tests pin today's behaviour, gaps included; the two marked F1 and F2 are rewritten in those
fix PRs. Everything runs in a temp dir; nothing touches Data/.
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
import aether.bootstrap as boot_module
from aether.bootstrap import PortfolioFile, bootstrap
from aether.core import DEFAULT_PORTFOLIO, PortfolioId
from aether.core import StorageError
from aether.services.portfolio.api import Portfolio, UnknownPortfolio

_STATE = {"balance": 812.5, "equity": 10450.0, "positions": {"AAA": {"qty": 3, "cost": 10.0, "stop_loss": 9.0}},
          "history": [{"date": "2026-11-02", "type": "BUY", "symbol": "AAA", "price": 10.0, "qty": 3}]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.file = self.root / "ai_portfolio_game.json"
        self.backups = self.root / "Backup" / "Game"
        self.log = mock.Mock()
        self.app = bootstrap(portfolio_files={DEFAULT_PORTFOLIO: PortfolioFile(self.file, self.backups)},
                             starting_capital=10000.0, log=self.log)
        self.store = self.app.portfolio_state

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
        self.assertEqual(self.store.load_state(), _STATE)
        self.log.warning.assert_not_called()

    def test_first_run_is_a_fresh_state_and_writes_nothing(self):
        fresh = self.store.load_state()
        self.assertEqual(fresh, self._fresh())
        self.assertEqual(list(fresh), list(self._fresh()))  # same key order as before
        self.assertFalse(self.file.exists())

    def test_missing_or_empty_file_restores_the_newest_readable_backup(self):
        for setup in ("missing", "empty"):
            with self.subTest(setup=setup):
                if setup == "empty":
                    self.file.write_text("", encoding="utf-8")
                self._backup("20261101_090000", json.dumps({"balance": 1.0}), age_s=300)
                self._backup("20261102_090000", json.dumps(_STATE), age_s=200)
                self._backup("20261103_090000", "{broken", age_s=100)  # newest, unreadable: skipped
                self.assertEqual(self.store.load_state(), _STATE)
                self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)  # copied back
                self.assertIn("missing or empty", self.log.warning.call_args[0][0])
                self.file.unlink()

    def test_corrupt_file_restores_the_newest_readable_backup(self):
        self.file.write_text("{not json", encoding="utf-8")
        self._backup("20261102_090000", json.dumps(_STATE), age_s=100)
        self.assertEqual(self.store.load_state(), _STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        messages = [c[0][0] for c in self.log.warning.call_args_list]
        self.assertIn("Error loading ai_portfolio_game.json", messages[0])
        self.assertIn("restored corrupted file from backup: ai_portfolio_game_20261102_090000.json", messages[1])

    def test_F2_corrupt_file_without_a_backup_becomes_a_fresh_state(self):
        # Today's behaviour, and the F2 bug: real positions are silently replaced by $10k cash.
        self.file.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load_state(), self._fresh())

    def test_load_returns_a_portfolio_object(self):
        self.file.write_text(json.dumps(_STATE), encoding="utf-8")
        p = self.app.portfolio.load(DEFAULT_PORTFOLIO)
        self.assertIsInstance(p, Portfolio)
        self.assertEqual((p.id, p.balance, p.positions["AAA"].qty), (DEFAULT_PORTFOLIO, 812.5, 3))

    def test_unknown_portfolio_is_refused(self):
        with self.assertRaises(UnknownPortfolio):
            self.app.portfolio.load(PortfolioId("other"))


class TestSave(_Base):
    def test_first_save_writes_without_a_backup(self):
        self.store.save_state(_STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        self.assertFalse(self.backups.exists())

    def test_bytes_match_the_old_json_dump(self):
        state = {"note": "café", "n": 1, "positions": {}, "zeta": [1, 2.5]}
        self.store.save_state(state)
        old = self.root / "old.json"
        with open(old, "w", encoding="utf-8") as f:  # what save_game wrote before
            json.dump(state, f, indent=4)
        self.assertEqual(self.file.read_bytes(), old.read_bytes())

    def test_save_backs_up_the_previous_file_first(self):
        self.file.write_text(json.dumps({"balance": 1.0}), encoding="utf-8")
        self.store.save_state(_STATE)
        (backup,) = self.backups.glob("ai_portfolio_game_*.json")
        self.assertRegex(backup.name, r"^ai_portfolio_game_\d{8}_\d{6}\.json$")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"balance": 1.0})
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)

    def test_keeps_the_newest_15_backups(self):
        self.file.write_text("{}", encoding="utf-8")
        old = [self._backup(f"2026100{i // 10}_0{i % 10}0000", "{}", age_s=10_000 - i) for i in range(16)]
        self.store.save_state(_STATE)
        left = set(self.backups.glob("ai_portfolio_game_*.json"))
        self.assertEqual(len(left), 15)
        self.assertNotIn(old[0], left)  # the two oldest go
        self.assertNotIn(old[1], left)

    def test_F1_a_failed_backup_still_writes(self):
        # Today's behaviour, and the F1 bug: the state is overwritten with no backup taken.
        self.file.write_text(json.dumps({"balance": 1.0}), encoding="utf-8")
        with mock.patch("aether.services.portfolio.adapters.json_repository.shutil.copy2",
                        side_effect=OSError("disk full")):
            self.store.save_state(_STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        self.assertIn("Game backup failed: disk full", self.log.warning.call_args[0][0])


class TestUnmappableStateIsQuarantined(_Base):
    """#295 review: a state the mapper refuses is kept in a file, logged, and the save fails."""

    def test_wrong_shape_goes_to_quarantine_and_raises(self):
        self.file.write_text(json.dumps(_STATE), encoding="utf-8")
        bad = dict(_STATE, queued_orders=None)
        with self.assertRaises(StorageError) as ctx:
            self.store.save_state(bad)
        (unsaved,) = self.root.glob("ai_portfolio_game.json.unsaved-*")
        self.assertRegex(unsaved.name, r"^ai_portfolio_game\.json\.unsaved-\d{8}_\d{6}$")
        self.assertEqual(json.loads(unsaved.read_text(encoding="utf-8")), bad)
        self.assertIn(str(unsaved), str(ctx.exception))
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)  # last good state untouched
        self.assertIn("NOT saved", self.log.error.call_args[0][0])

    def test_a_failed_quarantine_does_not_hide_the_error(self):
        with mock.patch.object(self.store, "_quarantine", side_effect=OSError("disk")):
            with self.assertRaises(StorageError) as ctx:
                self.store.save_state({"history": "x"})
        self.assertIn("history", str(ctx.exception))
        self.assertIn("quarantine write failed: disk", self.log.error.call_args[0][0])

    def test_values_json_cannot_write_are_still_kept(self):
        bad = {"positions": [], "when": datetime.date(2026, 11, 19)}
        with self.assertRaises(StorageError):
            self.store.save_state(bad)
        (unsaved,) = self.root.glob("ai_portfolio_game.json.unsaved-*")
        self.assertEqual(json.loads(unsaved.read_text(encoding="utf-8"))["when"], "datetime.date(2026, 11, 19)")


class TestGameUsesTheService(unittest.TestCase):
    """ai_portfolio_game.load_game/save_game go through bootstrap; the store is bootstrap's."""

    def test_wiring(self):
        with mock.patch.object(ai_portfolio_game, "bootstrap", wraps=ai_portfolio_game.bootstrap) as boot:
            ai_portfolio_game.load_game()
        boot.assert_called_once_with(log=ai_portfolio_game._log)

    def test_one_home_for_path_and_capital(self):
        self.assertFalse(hasattr(ai_portfolio_game, "AI_GAME_FILE"))
        self.assertFalse(hasattr(ai_portfolio_game, "GAME_BACKUP_DIR"))
        self.assertEqual(ai_portfolio_game.INITIAL_BALANCE, boot_module.STARTING_CAPITAL)

    def test_store_follows_bootstrap_at_call_time(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(boot_module, "DEFAULT_DATA_DIR", Path(d)):
            ai_portfolio_game.save_game(_STATE)
            self.assertEqual(ai_portfolio_game.load_game(), _STATE)
            self.assertTrue((Path(d) / "ai_portfolio_game.json").exists())


if __name__ == "__main__":
    unittest.main()
