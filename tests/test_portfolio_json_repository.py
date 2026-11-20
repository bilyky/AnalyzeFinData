"""JsonPortfolioRepository: the game's load/save, moved into the portfolio service (R&D #78 S4).

The file behaviour is pinned through ``LegacyStateGateway``, the path the game uses.
``TestSafeWrite`` is F1 (no backup, no write; atomic write). ``TestNoSilentFreshState`` is F2
(a fresh portfolio only on a true first run). Everything runs in a temp dir; nothing touches
Data/.
"""
import datetime
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import ai_portfolio_game
import aether.bootstrap as boot_module
from aether.bootstrap import PortfolioFile, bootstrap
from aether.core import DEFAULT_PORTFOLIO, PortfolioId, StorageError
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



_REPO = "aether.services.portfolio.adapters.json_repository"


class TestSafeWrite(_Base):
    """F1: a save never overwrites the file without a backup, and never leaves it half-written."""

    def setUp(self):
        super().setUp()
        self.good = {"balance": 1.0, "positions": {}}
        self.file.write_text(json.dumps(self.good), encoding="utf-8")

    def _assert_refused(self, ctx, why):
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), self.good)  # last good state kept
        (unsaved,) = self.root.glob("ai_portfolio_game.json.unsaved-*")
        self.assertEqual(json.loads(unsaved.read_text(encoding="utf-8")), _STATE)  # nothing is lost
        self.assertIn(why, str(ctx.exception))
        self.assertIn("NOT saved", self.log.error.call_args[0][0])
        self.assertIn(str(unsaved), self.log.error.call_args[0][0])  # names the file to restore
        self.assertIn("README.md (When a run logs NOT saved)", self.log.error.call_args[0][0])
        self.assertEqual(list(self.root.glob("*.tmp")), [])  # no temp file left behind

    def test_a_failed_backup_stops_the_save(self):
        with mock.patch(f"{_REPO}.shutil.copy2", side_effect=OSError("disk full")):
            with self.assertRaises(StorageError) as ctx:
                self.store.save_state(_STATE)
        self._assert_refused(ctx, "backup of ai_portfolio_game.json failed (disk full)")

    def test_a_failed_backup_folder_stops_the_save(self):
        with mock.patch(f"{_REPO}.Path.mkdir", side_effect=PermissionError("denied")):
            with self.assertRaises(StorageError) as ctx:
                self.store.save_state(_STATE)
        self._assert_refused(ctx, "denied")

    def test_a_failed_write_leaves_the_file_whole(self):
        with mock.patch(f"{_REPO}.json.dump", side_effect=OSError("disk full mid-write")):
            with self.assertRaises(StorageError) as ctx:
                self.store.save_state(_STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), self.good)
        self.assertIn("disk full mid-write", str(ctx.exception))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_a_value_json_cannot_write_leaves_the_file_whole(self):
        # Before F1 the old open("w") had already truncated the file when json.dump raised.
        with self.assertRaises(StorageError):
            self.store.save_state({"balance": 2.0, "when": datetime.date(2026, 11, 19)})
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), self.good)

    def test_a_reader_holding_the_file_open_only_delays_the_save(self):
        # Real Windows behaviour: os.replace onto an open file raises PermissionError (WinError 5).
        reader = open(self.file, "r", encoding="utf-8")
        threading.Timer(1.0, reader.close).start()
        try:
            self.store.save_state(_STATE)
        finally:
            reader.close()
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        self.log.error.assert_not_called()

    def test_a_file_that_stays_locked_stops_the_save(self):
        with mock.patch(f"{_REPO}.os.replace", side_effect=PermissionError("locked")) as rep, \
                mock.patch(f"{_REPO}.time.sleep") as sleep:
            with self.assertRaises(StorageError) as ctx:
                self.store.save_state(_STATE)
        self.assertEqual(rep.call_count, 10)
        self.assertEqual(sleep.call_count, 9)
        self._assert_refused(ctx, "stayed locked")

    def test_a_failed_prune_does_not_stop_the_save(self):
        with mock.patch(f"{_REPO}.trash.soft_delete", side_effect=OSError("x")), \
                mock.patch.object(type(self.backups), "glob", side_effect=OSError("listing failed")):
            self.store.save_state(_STATE)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8")), _STATE)
        self.assertIn("prune failed: listing failed", self.log.warning.call_args[0][0])

    def test_the_backup_holds_the_previous_state(self):
        self.store.save_state(_STATE)
        (backup,) = self.backups.glob("ai_portfolio_game_*.json")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), self.good)


class TestNoSilentFreshState(_Base):
    """F2: a fresh $10k portfolio only on a true first run (no file and no backup)."""

    def _assert_refused(self, ctx, why):
        self.assertIn(why, str(ctx.exception))
        self.assertIn("Nothing was written", str(ctx.exception))
        self.assertIn("README.md (When the state cannot be loaded)", str(ctx.exception))
        self.assertIn("cannot be loaded", self.log.error.call_args[0][0])

    def test_corrupt_file_without_a_backup_stops_the_load(self):
        self.file.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StorageError) as ctx:
            self.store.load_state()
        self._assert_refused(ctx, "is not valid JSON")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "{not json")  # left as it was
        (kept,) = self.root.glob("ai_portfolio_game.json.corrupt-*")
        self.assertEqual(kept.read_text(encoding="utf-8"), "{not json")

    def test_corrupt_file_with_only_unreadable_backups_stops_the_load(self):
        self.file.write_text("{not json", encoding="utf-8")
        self._backup("20261101_090000", "{also broken", age_s=100)
        with self.assertRaises(StorageError) as ctx:
            self.store.load_state()
        self._assert_refused(ctx, "none of the 1 backups")

    def test_empty_file_without_a_backup_stops_the_load(self):
        self.file.write_text("", encoding="utf-8")
        with self.assertRaises(StorageError) as ctx:
            self.store.load_state()
        self._assert_refused(ctx, "ai_portfolio_game.json is empty")

    def test_missing_file_with_only_unreadable_backups_stops_the_load(self):
        self._backup("20261101_090000", "{broken", age_s=100)
        with self.assertRaises(StorageError) as ctx:
            self.store.load_state()
        self._assert_refused(ctx, "ai_portfolio_game.json is missing")
        self.assertFalse(self.file.exists())

    def test_true_first_run_is_still_fresh(self):
        for setup in ("no backup folder", "empty backup folder", "only other files in it"):
            with self.subTest(setup=setup):
                if setup != "no backup folder":
                    self.backups.mkdir(parents=True, exist_ok=True)
                if setup == "only other files in it":
                    (self.backups / "notes.txt").write_text("x", encoding="utf-8")
                self.assertEqual(self.store.load_state(), self._fresh())

    def test_a_restored_corrupt_file_is_kept_as_evidence(self):
        self.file.write_text("{not json", encoding="utf-8")
        self._backup("20261102_090000", json.dumps(_STATE), age_s=100)
        self.assertEqual(self.store.load_state(), _STATE)
        (kept,) = self.root.glob("ai_portfolio_game.json.corrupt-*")
        self.assertEqual(kept.read_text(encoding="utf-8"), "{not json")

    def test_a_daily_run_on_a_corrupt_file_stops_before_any_save(self):
        # The runner logs a non-RuntimeError at ERROR and does not re-raise (its pinned contract),
        # so the run ends there: no trade, no save, no Excel rows, and the file is left as it was.
        from aether.scenario import runners
        with tempfile.TemporaryDirectory() as d, mock.patch.object(boot_module, "DEFAULT_DATA_DIR", Path(d)),                 mock.patch.object(ai_portfolio_game, "is_market_open", return_value=(True, "open")),                 mock.patch.object(ai_portfolio_game, "save_game") as save,                 mock.patch.object(ai_portfolio_game, "update_excel_log") as excel,                 mock.patch.object(ai_portfolio_game, "_log") as log:
            (Path(d) / "ai_portfolio_game.json").write_text("{not json", encoding="utf-8")
            runners.run_daily_management(force=True)
            self.assertEqual((Path(d) / "ai_portfolio_game.json").read_text(encoding="utf-8"), "{not json")
        save.assert_not_called()
        excel.assert_not_called()
        self.assertIn("cannot be loaded", log.exception.call_args[0][0])


class TestRecoveryRunbook(unittest.TestCase):
    """#300 review: the ERROR line points at a runbook that exists and says what to do."""

    def test_runbook_section_exists(self):
        readme = (Path(__file__).resolve().parent.parent / "aether" / "services" / "portfolio"
                  / "README.md").read_text(encoding="utf-8")
        self.assertIn("When a run logs `NOT saved`", readme)
        self.assertIn("When the state cannot be loaded", readme)
        for step in ("Back up the current", ".unsaved-<time>", "Excel log is not rebuilt"):
            self.assertIn(step, readme)


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
