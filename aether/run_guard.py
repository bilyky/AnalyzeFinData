"""Cross-process guard for the automated task schedule.

Two independent guarantees, both required by the split-scheduler design
(a fast deterministic ``ai_portfolio_game.py --run`` executor at 07:00 and a
slower AI ``autonomous_pipeline.py`` driver just after):

1. **State mutex** — tasks that read/write the same portfolio state
   (``Data/state_of_the_day.xlsx``, ``Data/ai_portfolio_game.json``) must never
   run concurrently. Whoever holds the named lock runs alone; a second task
   *waits* for release rather than racing on the workbook. A fixed 5-minute gap
   between two scheduled tasks is not a dependency — this lock is. If the holder
   crashes, the lock is presumed stale after ``lock_ttl`` and reclaimed.

2. **Once-per-trading-day idempotency** (opt-in via ``stamp``) — a stamped task
   executes at most once per calendar day (US/Pacific), no matter how many
   schedulers fire it. This is what makes the two parallel task registrars
   (``AETHER_ExecuteTrades`` and ``AnalyzeFinData_AI_Game``, *both*
   ``ai_portfolio_game.py --run`` at 07:00) safe: the second invocation acquires
   the mutex only after the first releases, sees today's stamp, and skips — so
   trades fire exactly once even if both task families are installed on the host.

This reuses the repo's established file-lock idiom (``O_CREAT | O_EXCL`` create +
TTL staleness, cf. ``aether/token_renewer.py`` and ``rapidapi.py``) rather than
introducing a second locking mechanism.
"""

import datetime
import logging
import os
import time
from pathlib import Path

import pytz

_log = logging.getLogger("aether.run_guard")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATA_DIR = _REPO_ROOT / "Data"
_PACIFIC = "America/Los_Angeles"

# A holder older than this (seconds) is presumed crashed and its lock reclaimed.
# Two hours comfortably exceeds any real run: the executor finishes in minutes,
# and the pipeline caps each subprocess step at 600s.
_DEFAULT_TTL = 7200.0


class RunSkipped(Exception):
    """This invocation must NOT run its body. Callers catch and exit 0 cleanly."""


class Busy(RunSkipped):
    """A live process still holds the state lock after ``wait_timeout`` elapsed."""


class AlreadyRanToday(RunSkipped):
    """A stamped task already completed today — a duplicate scheduler fired."""


def _today(tz: str) -> str:
    return datetime.datetime.now(pytz.timezone(tz)).date().isoformat()


class DailyRunGuard:
    """Serialises state-mutating tasks and, optionally, dedupes them per day.

    Use as a context manager (stamp written only on clean exit)::

        with DailyRunGuard("portfolio_state", stamp="trade_execution",
                           wait_timeout=7200):
            run_trades()

    or manually for code that cannot re-indent its whole body::

        guard = DailyRunGuard("portfolio_state", wait_timeout=7200)
        try:
            guard.acquire()
        except RunSkipped:
            sys.exit(0)
        atexit.register(guard.release)

    Args:
        lock_name: shared mutex key. Tasks that touch the same state MUST pass
            the same name so they serialise against each other.
        stamp: optional per-day idempotency key. When set, a second run on the
            same calendar day is refused with :class:`AlreadyRanToday`.
        wait_timeout: seconds to wait for the lock before giving up with
            :class:`Busy` (0 = fail fast). Pass ``lock_ttl`` to always wait for a
            live holder to finish.
        lock_ttl: age (seconds) past which a held lock is treated as crashed and
            reclaimed.
        poll: lock re-check interval while waiting.
        tz: timezone defining the "trading day" boundary.
        data_dir: override the Data directory (tests).
    """

    def __init__(self, lock_name: str, *, stamp: str | None = None,
                 wait_timeout: float = 0.0, lock_ttl: float = _DEFAULT_TTL,
                 poll: float = 1.0, tz: str = _PACIFIC, data_dir=None):
        base = Path(data_dir) if data_dir else _DATA_DIR
        self._lock_path = base / "locks" / f"{lock_name}.lock"
        self._stamp = stamp
        self._stamp_path = (base / "run_stamps" / f"{stamp}.date") if stamp else None
        self._wait_timeout = float(wait_timeout)
        self._lock_ttl = float(lock_ttl)
        self._poll = float(poll)
        self._tz = tz
        self._fd: int | None = None

    # ── once-per-day stamp ────────────────────────────────────────────────
    def _ran_today(self) -> bool:
        if not self._stamp_path or not self._stamp_path.exists():
            return False
        try:
            return self._stamp_path.read_text(encoding="utf-8").strip() == _today(self._tz)
        except OSError:
            return False

    def _write_stamp(self) -> None:
        if not self._stamp_path:
            return
        try:
            self._stamp_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._stamp_path.with_suffix(".tmp")
            tmp.write_text(_today(self._tz), encoding="utf-8")
            os.replace(tmp, self._stamp_path)  # atomic publish
        except OSError as e:
            _log.warning("run_guard: could not write stamp %s: %s", self._stamp, e)

    # ── cross-process file lock ───────────────────────────────────────────
    def _open_excl(self) -> int:
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {datetime.datetime.now().isoformat(timespec='seconds')}".encode())
        return fd

    def _try_lock(self) -> int | None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self._open_excl()
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(self._lock_path)
            except OSError:
                return None  # vanished mid-check; next poll retries
            if age <= self._lock_ttl:
                return None  # live holder — keep waiting
            _log.warning("run_guard: reclaiming stale lock %s (age %.0fs > ttl %.0fs)",
                         self._lock_path.name, age, self._lock_ttl)
            try:
                os.unlink(self._lock_path)
                return self._open_excl()
            except (OSError, FileExistsError):
                return None  # lost the reclaim race — keep waiting

    def _acquire_lock(self) -> int:
        deadline = time.monotonic() + self._wait_timeout
        while True:
            fd = self._try_lock()
            if fd is not None:
                return fd
            if time.monotonic() >= deadline:
                raise Busy(f"state lock '{self._lock_path.name}' held; "
                           f"waited {self._wait_timeout:.0f}s")
            time.sleep(self._poll)

    def _release_lock(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            os.unlink(self._lock_path)
        except OSError:
            pass

    # ── public API ────────────────────────────────────────────────────────
    def acquire(self) -> "DailyRunGuard":
        """Block until the lock is held (or reclaimed). Raise on skip.

        Raises :class:`AlreadyRanToday` if stamped and already run today,
        or :class:`Busy` if a live holder outlasts ``wait_timeout``.
        """
        if self._ran_today():  # fast path: no need to wait on the lock at all
            raise AlreadyRanToday(f"'{self._stamp}' already completed for {_today(self._tz)}")
        self._fd = self._acquire_lock()
        # Re-check inside the lock: a sibling that finished while we waited has
        # now stamped today, so skip rather than run a duplicate.
        if self._ran_today():
            self._release_lock()
            raise AlreadyRanToday(f"'{self._stamp}' already completed for {_today(self._tz)}")
        return self

    def release(self, success: bool = False) -> None:
        """Release the lock; write the day-stamp only when ``success`` is True."""
        try:
            if success:
                self._write_stamp()
        finally:
            self._release_lock()

    def __enter__(self) -> "DailyRunGuard":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release(success=exc_type is None)
        return False
