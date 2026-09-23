"""
Tests for aether.token_renewer — the cross-process lock that guards token renewal.

Focus: the PID-aware stale-lock reclaim + loud-failure logging added to fix the
leaked-``etrade_reauth.lock`` wedge (a crashed winner used to hold the lock for the
whole TTL; a lock that failed to delete on release used to leak silently).

Hermetic: every lock lives under a per-test temp dir; PID liveness is monkeypatched
where the reclaim *decision* is under test, and exercised for real (current process +
a reaped subprocess) where ``_pid_alive`` itself is under test.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether import token_renewer
from aether.token_renewer import TokenRenewer


class TestPidAlive(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(token_renewer._pid_alive(os.getpid()))

    def test_invalid_pid_is_dead(self):
        self.assertFalse(token_renewer._pid_alive(0))
        self.assertFalse(token_renewer._pid_alive(-1))
        self.assertFalse(token_renewer._pid_alive("nope"))

    def test_reaped_subprocess_is_dead(self):
        # Popen keeps a handle open after wait(), so on Windows the pid can't be
        # reused out from under us — a deterministic "dead pid" for the check.
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(token_renewer._pid_alive(p.pid))


class TestReadLockOwner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "x.lock")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file(self):
        self.assertIsNone(token_renewer._read_lock_owner(self.path))

    def test_empty_and_garbage(self):
        with open(self.path, "w"):
            pass
        self.assertIsNone(token_renewer._read_lock_owner(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("not json{")
        self.assertIsNone(token_renewer._read_lock_owner(self.path))

    def test_valid_stamp(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"pid": 123, "host": "h", "start_time": 1.0}, f)
        meta = token_renewer._read_lock_owner(self.path)
        self.assertEqual(meta["pid"], 123)
        self.assertEqual(meta["host"], "h")


class TestLockReclaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "svc.lock")
        self.renewer = TokenRenewer(
            self.lock,
            renew_fn=lambda: {"t": 1},
            load_fn=lambda: None,
            lock_ttl=300,
            wait_timeout=1,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_lock(self, pid, host, age_sec=0.0):
        with open(self.lock, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "host": host, "start_time": time.time()}, f)
        if age_sec:
            old = time.time() - age_sec
            os.utime(self.lock, (old, old))

    def test_acquire_stamps_owner_then_releases(self):
        fd = self.renewer._acquire()
        self.assertIsNotNone(fd)
        meta = token_renewer._read_lock_owner(self.lock)
        self.assertEqual(meta["pid"], os.getpid())
        self.assertEqual(meta["host"], socket.gethostname())
        self.renewer._release(fd)
        self.assertFalse(os.path.exists(self.lock))

    def test_dead_pid_reclaimed_before_ttl(self):
        self._write_lock(424242, socket.gethostname(), age_sec=0)  # fresh, within ttl
        with mock.patch.object(token_renewer, "_pid_alive", return_value=False):
            fd = self.renewer._acquire()
        self.assertIsNotNone(fd)  # reclaimed on dead PID despite age << ttl
        self.renewer._release(fd)

    def test_live_pid_not_reclaimed_within_ttl(self):
        self._write_lock(os.getpid(), socket.gethostname(), age_sec=0)
        with mock.patch.object(token_renewer, "_pid_alive", return_value=True):
            fd = self.renewer._acquire()
        self.assertIsNone(fd)  # live owner keeps its lock

    def test_live_pid_reclaimed_after_ttl(self):
        # A live-but-wedged owner is still reclaimed once the TTL fallback trips.
        self._write_lock(os.getpid(), socket.gethostname(), age_sec=400)
        with mock.patch.object(token_renewer, "_pid_alive", return_value=True):
            fd = self.renewer._acquire()
        self.assertIsNotNone(fd)
        self.renewer._release(fd)

    def test_cross_host_not_reclaimed_within_ttl(self):
        self._write_lock(os.getpid(), "some-other-host", age_sec=0)
        with mock.patch.object(token_renewer, "_pid_alive", return_value=False):
            fd = self.renewer._acquire()  # PID check skipped for a foreign host
        self.assertIsNone(fd)

    def test_legacy_stampless_lock_reclaimed_after_ttl(self):
        with open(self.lock, "w"):  # no stamp (older lock format)
            pass
        old = time.time() - 400
        os.utime(self.lock, (old, old))
        fd = self.renewer._acquire()
        self.assertIsNotNone(fd)
        self.renewer._release(fd)

    def test_acquire_reports_unremovable_stale_lock(self):
        self._write_lock(424242, socket.gethostname(), age_sec=0)
        with mock.patch.object(token_renewer, "_pid_alive", return_value=False), \
             mock.patch.object(token_renewer.trash, "soft_delete", return_value=None), \
             self.assertLogs("aether.token_renewer", level="ERROR") as cm:
            fd = self.renewer._acquire()
        self.assertIsNone(fd)  # couldn't remove → don't hand out a false win
        self.assertTrue(any("could NOT be removed" in m for m in cm.output))

    def test_release_logs_error_when_lock_lingers(self):
        fd = self.renewer._acquire()
        with mock.patch.object(token_renewer.trash, "soft_delete", return_value=None), \
             self.assertLogs("aether.token_renewer", level="ERROR") as cm:
            self.renewer._release(fd)  # soft_delete no-op → file remains
        self.assertTrue(any("NOT removed on release" in m for m in cm.output))
        os.remove(self.lock)  # real cleanup


if __name__ == "__main__":
    unittest.main()
