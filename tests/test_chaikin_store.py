"""Shared Chaikin session (plan T0.7): opt-in, encryption, newest-wins, fallback, and powergauge's routing."""
import json
import os
import tempfile
import time
import types
import unittest
from unittest import mock

import powergauge as pg
from aether import chaikin_store as cs

SESSION = {"jsessionid": "sid-one", "jwttoken": "jwt-one", "uuid": "u"}
NEWER = {"jsessionid": "sid-two", "jwttoken": "jwt-two", "uuid": "u"}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "shared.db")
        self.file = os.path.join(self._tmp.name, "session.json")
        self.cfg = types.SimpleNamespace(database_url="sqlite:///" + self.db.replace("\\", "/"),
                                         etrade_production_secret="secret")
        cs._ENGINES.clear()
        cs._WARNED.clear()
        self.addCleanup(self._dispose)                                     # runs before the temp dir is removed
        self.env = mock.patch.dict(os.environ, {cs.ENV_VAR: "db"}, clear=False)
        os.environ.pop("DATABASE_URL", None)

    @staticmethod
    def _dispose():
        for eng in cs._ENGINES.values():
            eng.dispose()                                                  # Windows: release the SQLite file
        cs._ENGINES.clear()

    def save_file(self, data):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def load_file(self):
        try:
            with open(self.file, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {}


class TestOptIn(_Base):
    def test_off_by_default_the_file_only(self):
        os.environ.pop(cs.ENV_VAR, None)
        cs.save(SESSION, self.save_file, self.cfg)
        self.assertEqual(cs.load(self.load_file, self.file, self.cfg), SESSION)
        self.assertFalse(os.path.exists(self.db))                          # no database touched

    def test_a_database_url_alone_does_not_turn_it_on(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": self.cfg.database_url, cs.ENV_VAR: "file"}):
            cs.save(SESSION, self.save_file, self.cfg)
        self.assertFalse(os.path.exists(self.db))


class TestShared(_Base):
    def test_the_other_machine_loads_what_this_one_saved(self):
        with self.env:
            cs.save(SESSION, self.save_file, self.cfg)
            os.remove(self.file)                                          # dev: no session.json
            self.assertEqual(cs.load(self.load_file, self.file, self.cfg), SESSION)

    def test_the_row_is_encrypted(self):
        with self.env:
            cs.db_write(SESSION, self.cfg)
        with open(self.db, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"sid-one", raw)
        self.assertNotIn(b"jwt-one", raw)

    def test_a_different_key_cannot_read_the_row(self):
        with self.env:
            cs.db_write(SESSION, self.cfg)
            other = types.SimpleNamespace(database_url=self.cfg.database_url, etrade_production_secret="other")
            with self.assertRaises(cs.StoreUnavailable):
                cs.db_read(other)
            self.assertEqual(cs.load(lambda: {}, self.file, other), {})   # falls back, never garbage

    def test_newest_wins(self):
        with self.env:
            cs.db_write(NEWER, self.cfg, now=time.time() + 60)            # the row is newer than the file
            self.save_file(SESSION)
            self.assertEqual(cs.load(self.load_file, self.file, self.cfg), NEWER)
            cs.db_write(NEWER, self.cfg, now=time.time() - 3600)          # a stale row (written before an outage)
            self.assertEqual(cs.load(self.load_file, self.file, self.cfg), SESSION)

    def test_no_row_yet_uses_the_file(self):
        with self.env:
            self.save_file(SESSION)
            self.assertEqual(cs.load(self.load_file, self.file, self.cfg), SESSION)


class TestFallback(_Base):
    def test_database_down_falls_back_and_warns_once(self):
        bad = types.SimpleNamespace(database_url="sqlite:////no/such/dir/x.db", etrade_production_secret="s")
        with self.env, mock.patch.object(cs._log, "warning") as warn:
            cs.save(SESSION, self.save_file, bad)                          # the file is still written
            self.assertEqual(self.load_file(), SESSION)
            self.assertEqual(cs.load(self.load_file, self.file, bad), SESSION)
            cs.load(self.load_file, self.file, bad)
        self.assertEqual(warn.call_count, 2)                              # once for save, once for load

    def test_no_secret_no_url_is_unavailable(self):
        with self.env:
            with self.assertRaises(cs.StoreUnavailable):
                cs.db_write(SESSION, types.SimpleNamespace(database_url=self.cfg.database_url, etrade_production_secret=""))
            with self.assertRaises(cs.StoreUnavailable):
                cs.db_write(SESSION, types.SimpleNamespace(database_url="", etrade_production_secret="s"))


class TestStatus(_Base):
    def test_status_shows_ages_not_values(self):
        with self.env:
            self.save_file(SESSION)
            cs.db_write(NEWER, self.cfg, now=time.time() + 60)
            st = cs.status(self.file, self.cfg)
        self.assertEqual(st["load_uses"], "row")
        self.assertNotIn("sid", json.dumps(st))


class TestCommandLine(_Base):
    def test_seed_copies_the_file_and_status_reads_it(self):
        self.save_file(SESSION)
        with self.env, mock.patch.object(cs, "session_file", return_value=self.file), \
                mock.patch.object(cs, "_config", return_value=self.cfg), mock.patch("sys.stdout"):
            self.assertEqual(cs.main(["--seed"]), 0)
            self.assertEqual(cs.db_read(self.cfg)[0], SESSION)
            self.assertEqual(cs.main(["--status"]), 0)

    def test_seed_needs_the_opt_in_and_a_session(self):
        with mock.patch.object(cs, "session_file", return_value=self.file), mock.patch("sys.stderr"):
            os.environ.pop(cs.ENV_VAR, None)
            self.assertEqual(cs.main(["--seed"]), 2)
            with self.env:
                self.assertEqual(cs.main(["--seed"]), 2)                  # no session.json

    def test_the_session_file_is_powergauges(self):
        self.assertEqual(os.path.normcase(cs.session_file()), os.path.normcase(pg.SESSION_FILE))


class TestPowergaugeRouting(unittest.TestCase):
    def test_every_caller_goes_through_the_store(self):
        with mock.patch.object(pg.chaikin_store, "load", return_value=SESSION) as ld:
            self.assertEqual(pg._load_session_from_file(), SESSION)
        ld.assert_called_once_with(pg._load_session_file, pg.SESSION_FILE)
        with mock.patch.object(pg.chaikin_store, "save") as sv:
            pg._save_session_to_file(SESSION)
        sv.assert_called_once_with(SESSION, pg._save_session_file)


if __name__ == "__main__":
    unittest.main()
