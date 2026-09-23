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

import contextlib
import ctypes
import json
import logging
import os
import socket
import threading
import time

from aether import trash

_log = logging.getLogger("aether.token_renewer")


def _pid_alive(pid: int) -> bool:
    """Best-effort 'is this PID currently running?' check.

    Conservative by design: on any uncertainty it returns True so the caller
    falls back to the age-based TTL rather than risk reclaiming a lock that is
    still legitimately held (which would open a second browser mint).
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)              # signal 0 = existence probe, no signal sent
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                 # exists, owned by another user
    except OSError:
        return True                 # unknown → assume alive (safe)
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Windows PID-liveness via OpenProcess/GetExitCodeProcess.

    NOTE: ``os.kill(pid, 0)`` MUST NOT be used on Windows — any non-CTRL signal
    (including 0) is routed to TerminateProcess and would *kill* the target.
    """
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_INVALID_PARAMETER = 87
    try:
        kernel32 = ctypes.windll.kernel32     # only exists on Windows
    except AttributeError:
        return True                 # not really Windows → assume alive (safe)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Only a clearly-invalid pid is treated as dead; any other failure
        # (e.g. access-denied on a live process) stays "alive" so we never
        # steal a lock from a running owner.
        return kernel32.GetLastError() != ERROR_INVALID_PARAMETER
    try:
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return True             # query failed → assume alive (safe)
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _read_lock_owner(path: str) -> dict | None:
    """Read the {pid, host, start_time} owner stamp written into a lock file.

    Returns None for a legacy/empty/unparseable lock (older locks carried no
    stamp) — callers then rely on the age-based TTL alone.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except ValueError:
        return None
    return meta if isinstance(meta, dict) else None


def _stamp_owner(fd: int) -> None:
    """Write this process's identity into the freshly-created lock file."""
    meta = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "start_time": time.time(),
    }
    try:
        os.write(fd, json.dumps(meta).encode("utf-8"))
    except OSError:
        pass                     # stamping is best-effort; TTL still protects us


def _lock_is_stale(path: str, lock_ttl: int) -> bool:
    """Decide whether an existing lock may be reclaimed.

    A lock owned by a *dead* PID on *this* host is stale immediately — this
    is the leak the PID stamp fixes (a crashed winner no longer wedges the
    lock for the full TTL). For a live owner, a cross-host owner, or a legacy
    stampless lock we fall back to the age-based TTL, which also rescues the
    case of a live-but-wedged owner that outlives the TTL.
    """
    owner = _read_lock_owner(path)
    if owner and owner.get("host") == socket.gethostname():
        pid = owner.get("pid")
        if isinstance(pid, int) and not _pid_alive(pid):
            _log.warning(f"Lock {path} owned by dead PID {pid} — reclaiming.")
            return True
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return False             # vanished or unreadable → let os.open retry
    return age > lock_ttl


def _acquire_lock(lock_path: str, lock_ttl: int) -> int | None:
    """Atomically create the cross-process lock file. Returns the fd if won, None if already held.

    On success the winner's identity ({pid, host, start_time}) is stamped into the file so a later
    contender can distinguish a dead-owner lock (reclaim immediately) from a live one (TTL fallback).
    A lock owned by a dead PID on this host is reclaimed at once; otherwise a lock whose mtime is
    older than ``lock_ttl`` is treated as stale (crashed/wedged holder) and reclaimed. The single
    definition of the file-lock acquire, shared by TokenRenewer and single_flight() — so both the
    renew ladder and the non-blocking single-flight door get the same dead-PID reclaim."""
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        _stamp_owner(fd)
        return fd
    except FileExistsError:
        # Lock is held — reclaim only if the holder is provably gone or the lock aged past its TTL.
        if not _lock_is_stale(lock_path, lock_ttl):
            return None
        trash.soft_delete(lock_path, reason="renew-lock-stale", force=True)
        if os.path.exists(lock_path):
            _log.error(
                f"Stale lock {lock_path} could NOT be removed (still held by an open "
                f"file handle?) — renewal remains blocked."
            )
            return None
        _log.warning(f"Removed stale lock: {lock_path}")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            _stamp_owner(fd)
            return fd
        except FileExistsError:
            return None          # another contender won the reclaim race


def _release_lock(lock_path: str, fd: int) -> None:
    """Close the fd and remove the lock file. The single definition of the file-lock release."""
    try:
        os.close(fd)
    except OSError:
        pass
    trash.soft_delete(lock_path, reason="renew-lock", force=True)
    # soft_delete(force=True) returns None whether or not the remove succeeded, so verify
    # explicitly: a lingering lock here is the silent-leak failure mode — surface it loudly
    # so a wedged lock is diagnosable rather than blocking renewal until its TTL expires.
    if os.path.exists(lock_path):
        _log.error(
            f"Lock file NOT removed on release: {lock_path} — renewal will be blocked "
            f"until its TTL expires or the owning process exits. Check for a "
            f"leaked/open file handle."
        )


@contextlib.contextmanager
def single_flight(lock_path: str, lock_ttl: int = 300):
    """Non-blocking cross-process single-flight guard over the shared file-lock primitive.

    Yields ``True`` to exactly one holder (which must do the guarded work) and ``False`` to any
    concurrent caller (work is already in flight — the loser must NOT start a second copy). Unlike
    ``TokenRenewer.ensure`` there is no wait loop: a loser returns immediately. The holder always
    releases the lock on exit (even on exception); a loser releases nothing. Stale locks (mtime
    older than ``lock_ttl``) are reclaimed by ``_acquire_lock``, so a crashed holder can never
    wedge the guard forever.

    Usage::

        with single_flight("Data/etrade_reauth.lock", lock_ttl=300) as won:
            if not won:
                return {"reason": "in_progress"}
            do_the_one_browser_mint()
    """
    fd = _acquire_lock(lock_path, lock_ttl)
    if fd is None:
        yield False
        return
    try:
        yield True
    finally:
        _release_lock(lock_path, fd)


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
        If the renew_fn raises an exception, the exception is propagated
        loudly to enforce zero-trust transparency.
        Thread-safe and cross-process safe.
        """
        with self._thread_lock:
            # Re-read inside lock — another thread may have just renewed
            fresh = self._load_fn()
            if fresh:
                if current_token is None:
                    return fresh
                # Only return the freshly-loaded token when it is actually newer/different
                # than what the caller already holds — i.e. another thread or process renewed it.
                is_newer = False
                if isinstance(fresh, dict) and isinstance(current_token, dict):
                    fresh_ts = fresh.get("saved_at", "")
                    cur_ts = current_token.get("saved_at", "")
                    if fresh_ts and cur_ts:
                        is_newer = fresh_ts > cur_ts
                    else:
                        is_newer = (
                            fresh.get("oauth_token") != current_token.get("oauth_token")
                            or fresh != current_token
                        )
                else:
                    is_newer = fresh != current_token
                if is_newer:
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
        return _acquire_lock(self._lock_path, self._lock_ttl)

    def _release(self, fd: int):
        _release_lock(self._lock_path, fd)
