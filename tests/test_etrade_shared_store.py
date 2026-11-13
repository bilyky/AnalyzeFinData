"""One shared E*TRADE login for two machines (AETHER_ETRADE_STORE=db), with a file fallback.

PROD 2026-11-11/12: the dev dashboard and PROD used the same E*TRADE consumer key, and each one's
login killed the other's token (dev 08:02 → PROD rejected 08:12; dev 09:38 → PROD 09:45; PROD
09:45 → dev 10:06; dev 10:15 → PROD 10:45; …). With a shared store, a machine whose token is gone
loads the one the other machine minted instead of logging in again (PROD review 2026-11-13).

The database here is a temp SQLite file: the adapter uses portable SQL only. No network.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aether.etrade as et
from aether.config import CFG
from aether.etrade import store, store_db
from aether.etrade.store import StoreUnavailable
from aether.token_renewer import TokenRenewer

_TODAY = "2026-11-13"
_SECRET = "test-consumer-secret"


class _Machine:
    """One machine's local files (token, re-auth state, locks) in its own temp folder."""

    def __init__(self, root: Path, name: str):
        self.dir = root / name
        self.dir.mkdir(exist_ok=True)
        self.patches = [
            mock.patch.object(et, "_TOKEN_PATH", str(self.dir / "etrade_tokens.json")),
            mock.patch.object(et, "_REAUTH_STATE_PATH", str(self.dir / "etrade_reauth_state.json")),
            mock.patch.object(et, "_BROWSER_STATE_PATH", str(self.dir / "etrade_browser_state.json")),
            mock.patch.object(et, "_DATA_DIR", str(self.dir)),
        ]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._td.name)
        self.db_path = self.root / "shared.db"
        self.url = f"sqlite:///{self.db_path.as_posix()}"
        self._common = [
            mock.patch.object(et, "_et_today", return_value=_TODAY),
            mock.patch.object(CFG, "etrade_production_secret", _SECRET),
            mock.patch.dict(os.environ, {"AETHER_ETRADE_STORE": "db", "DATABASE_URL": self.url}),
        ]
        for p in self._common:
            p.start()
        store._WARNED.clear()

    def tearDown(self):
        for p in reversed(self._common):
            p.stop()
        for url in [u for u in store_db._ENGINES if u.startswith("sqlite")]:
            store_db._ENGINES.pop(url).dispose()
        store._WARNED.clear()
        self._td.cleanup()

    def db(self, key=_SECRET):
        return store_db.make_db_store(self.url, key_secret=key)

    @staticmethod
    def tok(n):
        return {"oauth_token": f"tok{n}", "oauth_token_secret": f"sec{n}"}


class DbAdapterTest(_Base):
    def test_token_round_trip_is_encrypted_at_rest(self):
        s = self.db()
        s.tokens.save(self.tok(1), "production")
        self.assertEqual(s.tokens.load("production")["oauth_token"], "tok1")
        with sqlite3.connect(self.db_path) as c:
            payload, fp = c.execute("select payload, fingerprint from etrade_tokens").fetchone()
        self.assertNotIn("tok1", payload)
        self.assertNotIn("sec1", payload)
        self.assertEqual(fp, store_db.fingerprint("tok1"))

    def test_yesterdays_token_is_only_returned_by_load_any_date(self):
        s = self.db()
        with mock.patch.object(et, "_et_today", return_value="2026-11-12"):
            s.tokens.save(self.tok(1), "production")
        self.assertIsNone(s.tokens.load("production"))
        self.assertEqual(s.tokens.load_any_date("production")["oauth_token"], "tok1")

    def test_another_key_reads_as_no_token(self):
        self.db().tokens.save(self.tok(1), "production")
        self.assertIsNone(self.db(key="another-secret").tokens.load("production"))

    def test_conditional_delete_keeps_a_newer_token(self):
        s = self.db()
        s.tokens.save(self.tok(2), "production")
        s.tokens.delete(env="production", if_token="tok1")      # stale holder: no effect
        self.assertEqual(s.tokens.load("production")["oauth_token"], "tok2")
        s.tokens.delete(env="production", if_token="tok2")
        self.assertIsNone(s.tokens.load("production"))

    def test_lock_lease_is_exclusive_and_expired_leases_are_reclaimed(self):
        s = self.db()
        h = s.lock.acquire("etrade_reauth.lock", ttl=300)
        self.assertIsNotNone(h)
        self.assertIsNone(s.lock.acquire("etrade_reauth.lock", ttl=300))
        self.assertTrue(s.lock.is_held("etrade_reauth.lock"))
        s.lock.release(h)
        self.assertFalse(s.lock.is_held("etrade_reauth.lock"))
        dead = s.lock.acquire("etrade_renew.lock", ttl=-1)      # a crashed holder's lease, already expired
        self.assertIsNotNone(dead)
        self.assertIsNotNone(s.lock.acquire("etrade_renew.lock", ttl=30))

    def test_release_needs_the_owner(self):
        s = self.db()
        h = s.lock.acquire("x", ttl=300)
        s.lock.release(store.LockHandle(name="x", owner="someone-else"))
        self.assertTrue(s.lock.is_held("x"))
        s.lock.release(h)
        self.assertFalse(s.lock.is_held("x"))

    def test_reauth_state_round_trip(self):
        s = self.db()
        self.assertEqual(s.reauth.load()["consecutive_failures"], 0)
        s.reauth.save({"consecutive_failures": 2, "last_attempt": 5.0, "cooldown_until": 9.0})
        self.assertEqual(s.reauth.load(), {"consecutive_failures": 2, "last_attempt": 5.0, "cooldown_until": 9.0})

    def test_browser_state_stays_per_machine(self):
        self.assertIsInstance(self.db().browser_state, store.FileBrowserStateStore)


class FallbackTest(_Base):
    def test_unreachable_db_uses_the_local_file_and_warns_once(self):
        with _Machine(self.root, "a"), \
             mock.patch.dict(os.environ, {"DATABASE_URL": "sqlite:///Z:/no/such/dir/x.db"}), \
             self.assertLogs("aether.etrade", level="WARNING") as logs:
            et._save_tokens(self.tok(1), "production")
            self.assertEqual(et._load_tokens("production")["oauth_token"], "tok1")
            self.assertEqual(et._load_tokens("production")["oauth_token"], "tok1")
            self.assertTrue(Path(et._TOKEN_PATH).exists())
        warns = [m for m in logs.output if "shared store unavailable" in m]
        self.assertEqual(len(warns), 2, warns)          # one for "token save", one for "token load"

    def test_a_token_minted_during_an_outage_is_shared_once_the_db_is_back(self):
        with _Machine(self.root, "a"):
            store.FileTokenStore().save(self.tok(7), "production")   # minted while the DB was down
            self.assertEqual(et._load_tokens("production")["oauth_token"], "tok7")
        self.assertEqual(self.db().tokens.load("production")["oauth_token"], "tok7")

    def test_lock_falls_back_to_the_file_lock(self):
        with _Machine(self.root, "a") as m, \
             mock.patch.dict(os.environ, {"DATABASE_URL": "sqlite:///Z:/no/such/dir/x.db"}):
            lock = et.make_etrade_store().lock
            h = lock.acquire("etrade_reauth.lock", ttl=300)
            self.assertTrue((m.dir / "etrade_reauth.lock").exists())
            lock.release(h)
            self.assertFalse((m.dir / "etrade_reauth.lock").exists())

    def test_factory_wraps_the_db_store(self):
        s = et.make_etrade_store()
        self.assertEqual(s.backend, "db")
        self.assertIsInstance(s.tokens, store._FallbackTokenStore)
        self.assertIsInstance(s.browser_state, store.FileBrowserStateStore)

    def test_no_consumer_secret_means_files_only(self):
        with _Machine(self.root, "a"), mock.patch.object(CFG, "etrade_production_secret", ""), \
             self.assertLogs("aether.etrade", level="WARNING"):
            et._save_tokens(self.tok(1), "production")
            self.assertEqual(et._load_tokens("production")["oauth_token"], "tok1")
        with self.assertRaises(StoreUnavailable):
            store_db.make_db_store(self.url).tokens.load("production")


class TwoMachinesTest(_Base):
    def test_machine_b_uses_the_token_machine_a_minted_and_does_not_log_in(self):
        with _Machine(self.root, "a"):
            et._save_tokens(self.tok(1), "production")              # A logs in
        with _Machine(self.root, "b") as b:
            self.assertFalse(Path(et._TOKEN_PATH).exists())          # B has no token of its own
            mint = mock.Mock(return_value=self.tok(99))
            renewer = TokenRenewer(str(b.dir / "etrade_reauth.lock"), renew_fn=mint,
                                   load_fn=lambda: et._load_tokens("production"),
                                   lock_ttl=300, wait_timeout=1, lock_provider=et._token_lock_provider())
            got = renewer.ensure(current_token=None)
        self.assertEqual(got["oauth_token"], "tok1")
        mint.assert_not_called()

    def test_a_rejected_old_token_does_not_delete_the_other_machines_new_one(self):
        with _Machine(self.root, "b"):
            et._save_tokens(self.tok(1), "production")              # B's token
        with _Machine(self.root, "a"):
            et._save_tokens(self.tok(2), "production")              # A logs in: tok1 is now dead
        with _Machine(self.root, "b"):
            et._discard_rejected_token(self.tok(1), "production", "rejected-401")   # B saw tok1 rejected
            self.assertFalse(Path(et._TOKEN_PATH).exists())          # B's own file went to the trash
            self.assertEqual(et._load_tokens("production")["oauth_token"], "tok2")   # and B now uses A's

    def test_a_rejected_current_token_is_removed_from_the_shared_store(self):
        """Otherwise the next load returns the same dead token from the database, again and again."""
        with _Machine(self.root, "b"):
            et._save_tokens(self.tok(1), "production")
            et._discard_rejected_token(self.tok(1), "production", "rejected-401")
            self.assertIsNone(et._load_tokens("production"))
        self.assertIsNone(self.db().tokens.load_any_date("production"))

    def test_two_machines_never_hold_the_reauth_lock_together(self):
        with _Machine(self.root, "a"):
            ha = et._token_lock_provider().acquire("etrade_reauth.lock", ttl=300)
        with _Machine(self.root, "b"):
            hb = et._token_lock_provider().acquire("etrade_reauth.lock", ttl=300)
        self.assertIsNotNone(ha)
        self.assertIsNone(hb)

    def test_file_backend_is_unchanged(self):
        with _Machine(self.root, "a"), mock.patch.dict(os.environ, {"AETHER_ETRADE_STORE": "file"}):
            self.assertIsNone(et._token_lock_provider())
            et._save_tokens(self.tok(1), "production")
            self.assertTrue(Path(et._TOKEN_PATH).exists())
        self.assertFalse(self.db_path.exists())                     # the DB was never touched


if __name__ == "__main__":
    unittest.main()
