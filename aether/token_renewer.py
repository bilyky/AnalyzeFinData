"""
Generic cross-process token renewal singleton.

Guarantees that across all threads in this process AND all processes on the
machine, at most one renewal attempt runs at any moment.

Usage:
    renewer = TokenRenewer(
        lock_path="Data/my_service_renew.lock",
        renew_fn=lambda: do_http_renewal(),   # returns new token dict or None
        load_fn=lambda: load_token_from_disk(),
        lock_ttl=30,       # seconds — how long before a lock is considered stale
        wait_timeout=15,   # seconds to wait if another process holds the lock
    )

    fresh = renewer.ensure()  # returns valid token or None
"""

import logging
import os
import threading
import time

_log = logging.getLogger("aether.token_renewer")


class TokenRenewer:
    """Two-level mutex singleton for cross-process token renewal.

    Level 1 — threading.Lock: prevents N threads in this process from all
    calling renew_fn simultaneously.

    Level 2 — file lock (O_CREAT|O_EXCL): prevents multiple processes
    (watchdog, server, CLI scripts) from all hitting the remote endpoint
    simultaneously. The winner renews and writes to disk; losers wait and
    re-read the result.
    """

    def __init__(self, lock_path: str, renew_fn, load_fn,
                 lock_ttl: int = 30, wait_timeout: int = 15):
        self._lock_path    = lock_path
        self._renew_fn     = renew_fn     # () -> token | None
        self._load_fn      = load_fn      # () -> token | None  (re-reads from disk)
        self._lock_ttl     = lock_ttl
        self._wait_timeout = wait_timeout
        self._thread_lock  = threading.Lock()

    def ensure(self, current_token=None):
        """Return a valid token, renewing if necessary.

        Returns the renewed token on success, or None if renewal failed.
        Thread-safe and cross-process safe.
        """
        with self._thread_lock:
            # Re-read inside lock — another thread may have just renewed
            fresh = self._load_fn()
            if fresh:
                return fresh

            # Try to win the cross-process file lock
            fd = self._acquire()
            if fd is None:
                # Another process holds it — fall through to wait below
                pass
        # Release thread lock BEFORE the slow network call
        if fd is not None:
            try:
                _log.info(f"Renewing token via {self._lock_path}...")
                result = self._renew_fn()
                if result:
                    _log.info("Token renewed.")
                    return result
                _log.warning("Token renewal failed.")
                return None
            finally:
                self._release(fd)

        # Wait outside thread lock so other threads are not stalled
        _log.debug("Waiting for another process to renew token...")
        deadline = time.monotonic() + self._wait_timeout
        while os.path.exists(self._lock_path) and time.monotonic() < deadline:
            time.sleep(0.5)
        return self._load_fn() or current_token

    def _acquire(self) -> int | None:
        """Atomically create the lock file. Returns fd if won, None if held."""
        os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
        try:
            return os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Clean stale lock from a crashed process
            try:
                if time.time() - os.path.getmtime(self._lock_path) > self._lock_ttl:
                    os.unlink(self._lock_path)
                    _log.warning(f"Removed stale lock: {self._lock_path}")
                    return os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (OSError, FileExistsError):
                pass
            return None

    def _release(self, fd: int):
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(self._lock_path)
        except OSError:
            pass
