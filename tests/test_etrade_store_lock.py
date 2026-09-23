"""Coverage for the ``LockProvider`` port + ``FileLockProvider`` file adapter (store.py).

The port is the cross-process "only one browser mint opens" guarantee lifted out of
``token_renewer`` into the persistence bundle so a DB row-lease adapter can replace it
later with zero call-site changes. ``FileLockProvider`` must be a *thin* wrapper over the
SAME module-level ``token_renewer._acquire_lock`` / ``_release_lock`` primitive the reauth
single-flight already uses — these tests pin that (real lock file on disk, non-blocking
acquire, release-then-reacquire, single_flight True/False, name resolution) and the bundle
wiring (default provider, file/DB factory).
"""
import json
import os
import socket
import tempfile
import unittest

from aether import token_renewer
from aether.etrade.store import (
    EtradeStore, FileLockProvider, FileReauthStateStore, FileTokenStore,
    FileBrowserStateStore, LockHandle, LockProvider, _file_store,
)


class TestFileLockProvider(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_dir = self._tmp.name
        self.provider = FileLockProvider(lock_dir=self.lock_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_acquire_returns_handle_and_creates_lock_file(self):
        handle = self.provider.acquire("etrade_reauth.lock", ttl=300)
        self.assertIsInstance(handle, LockHandle)
        self.assertEqual(handle.name, "etrade_reauth.lock")
        self.assertTrue(os.path.exists(handle.path), "lock file must exist on disk while held")
        self.provider.release(handle)

    def test_concurrent_acquire_returns_none(self):
        first = self.provider.acquire("busy.lock", ttl=300)
        self.assertIsNotNone(first)
        second = self.provider.acquire("busy.lock", ttl=300)
        self.assertIsNone(second, "a held lock must hand None to a concurrent caller")
        self.provider.release(first)

    def test_release_removes_lock_and_allows_reacquire(self):
        handle = self.provider.acquire("cycle.lock", ttl=300)
        path = handle.path
        self.provider.release(handle)
        self.assertFalse(os.path.exists(path), "release must remove the lock file")
        again = self.provider.acquire("cycle.lock", ttl=300)
        self.assertIsNotNone(again, "lock must be re-acquirable after release")
        self.provider.release(again)

    def test_release_none_handle_is_noop(self):
        # A loser (acquire returned None) must be able to release() harmlessly.
        self.provider.release(None)

    def test_bare_name_resolves_under_lock_dir(self):
        handle = self.provider.acquire("bare.lock", ttl=300)
        self.assertEqual(handle.path, os.path.join(self.lock_dir, "bare.lock"))
        self.provider.release(handle)

    def test_absolute_name_passes_through_verbatim(self):
        abs_path = os.path.join(self.lock_dir, "sub", "explicit.lock")
        handle = self.provider.acquire(abs_path, ttl=300)
        self.assertEqual(handle.path, abs_path)
        self.assertTrue(os.path.exists(abs_path))
        self.provider.release(handle)

    def test_delegates_to_token_renewer_primitive(self):
        # The adapter must call the SHARED primitive, not a private copy — so both the
        # reauth single-flight door and this port share one lock behaviour on one file.
        seen = {}
        real_acquire = token_renewer._acquire_lock

        def spy(path, ttl):
            seen["path"] = path
            seen["ttl"] = ttl
            return real_acquire(path, ttl)

        orig = token_renewer._acquire_lock
        token_renewer._acquire_lock = spy
        try:
            handle = self.provider.acquire("spy.lock", ttl=123)
        finally:
            token_renewer._acquire_lock = orig
        self.assertEqual(seen["path"], os.path.join(self.lock_dir, "spy.lock"))
        self.assertEqual(seen["ttl"], 123)
        self.provider.release(handle)

    def test_dead_pid_owner_is_reclaimed(self):
        # Inherit token_renewer's dead-owner reclaim: a lock stamped with a dead PID on
        # this host is stale immediately, so a fresh acquire wins without waiting the TTL.
        path = os.path.join(self.lock_dir, "dead.lock")
        with open(path, "w", encoding="utf-8") as f:
            # Stamp the lock with a dead PID on THIS host: token_renewer can probe a same-host
            # owner's liveness, finds it dead, and reclaims at once (no TTL wait). 2**31-1 is a
            # PID that will never be alive.
            json.dump({"pid": 2 ** 31 - 1, "host": socket.gethostname()}, f)
        handle = self.provider.acquire("dead.lock", ttl=300)
        self.assertIsNotNone(handle, "a dead-PID lock on this host must be reclaimable")
        self.provider.release(handle)


class TestSingleFlight(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.provider = FileLockProvider(lock_dir=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_flight_yields_true_then_false(self):
        with self.provider.single_flight("sf.lock", ttl=300) as won_outer:
            self.assertTrue(won_outer, "the first holder must win")
            with self.provider.single_flight("sf.lock", ttl=300) as won_inner:
                self.assertFalse(won_inner, "a concurrent caller must lose")
        # After the outer guard exits, the lock is released and re-winnable.
        with self.provider.single_flight("sf.lock", ttl=300) as won_again:
            self.assertTrue(won_again)

    def test_single_flight_releases_on_exception(self):
        with self.assertRaises(RuntimeError):
            with self.provider.single_flight("boom.lock", ttl=300) as won:
                self.assertTrue(won)
                raise RuntimeError("boom")
        # Lock must have been released despite the exception.
        with self.provider.single_flight("boom.lock", ttl=300) as won_again:
            self.assertTrue(won_again)


class TestBundleWiring(unittest.TestCase):
    def test_etrade_store_defaults_lock_to_file_provider(self):
        store = EtradeStore(
            tokens=FileTokenStore(),
            browser_state=FileBrowserStateStore(),
            reauth=FileReauthStateStore(),
        )
        self.assertIsInstance(store.lock, FileLockProvider)

    def test_explicit_lock_is_kept(self):
        sentinel = FileLockProvider(lock_dir="Custom")
        store = EtradeStore(
            tokens=FileTokenStore(),
            browser_state=FileBrowserStateStore(),
            reauth=FileReauthStateStore(),
            lock=sentinel,
        )
        self.assertIs(store.lock, sentinel)

    def test_file_store_carries_a_lock_provider(self):
        store = _file_store()
        self.assertIsInstance(store.lock, LockProvider)
        self.assertEqual(store.backend, "file")

    def test_db_store_lock_is_stub(self):
        from aether.etrade.store_db import make_db_store
        store = make_db_store("postgresql://example/db")
        self.assertEqual(store.backend, "db")
        with self.assertRaises(NotImplementedError):
            store.lock.acquire("x")


if __name__ == "__main__":
    unittest.main()
