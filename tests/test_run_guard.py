"""Tests for aether.run_guard.DailyRunGuard — the single-executor / no-overlap guard.

IO-isolated: every guard points at a throwaway ``data_dir`` (a real temp dir), so
the real lock/stamp filesystem logic runs — nothing about the guard is mocked.
"""

import datetime
import os
import tempfile
import time
import unittest
from pathlib import Path

import pytz

from aether.run_guard import AlreadyRanToday, Busy, DailyRunGuard, RunSkipped


def _today():
    return datetime.datetime.now(pytz.timezone("America/Los_Angeles")).date().isoformat()


class DailyRunGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def guard(self, **kw):
        kw.setdefault("data_dir", self.data_dir)
        return DailyRunGuard(kw.pop("lock_name", "portfolio_state"), **kw)

    # ── mutex ─────────────────────────────────────────────────────────────
    def test_mutex_blocks_a_live_holder(self):
        """A second guard on the same lock fails fast while the first holds it."""
        a = self.guard()
        a.acquire()
        try:
            with self.assertRaises(Busy):
                self.guard(wait_timeout=0).acquire()
        finally:
            a.release()

    def test_mutex_free_after_release(self):
        """Once released, the next acquirer succeeds."""
        a = self.guard()
        a.acquire()
        a.release()
        b = self.guard(wait_timeout=0)
        b.acquire()          # must not raise
        b.release()

    def test_stale_lock_is_reclaimed(self):
        """A lock older than lock_ttl (crashed holder) is reclaimed, not honored."""
        g = self.guard(wait_timeout=0, lock_ttl=100)
        lock = Path(self.data_dir) / "locks" / "portfolio_state.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("99999 stale", encoding="utf-8")
        old = time.time() - 10_000
        os.utime(lock, (old, old))
        g.acquire()          # reclaims the stale lock instead of raising Busy
        g.release()

    def test_fresh_foreign_lock_is_not_stolen(self):
        """A lock within lock_ttl is a live holder — do not steal it."""
        lock = Path(self.data_dir) / "locks" / "portfolio_state.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("99999 fresh", encoding="utf-8")  # mtime = now
        with self.assertRaises(Busy):
            self.guard(wait_timeout=0, lock_ttl=7200).acquire()

    # ── once-per-day stamp ────────────────────────────────────────────────
    def test_second_stamped_run_same_day_skips(self):
        """A stamped task refuses a second run on the same calendar day."""
        with self.guard(stamp="trade_execution", wait_timeout=0):
            pass  # clean exit writes today's stamp
        with self.assertRaises(AlreadyRanToday):
            self.guard(stamp="trade_execution", wait_timeout=0).acquire()

    def test_stamp_only_written_on_clean_exit(self):
        """A failed run leaves no stamp, so the next run still executes."""
        g = self.guard(stamp="trade_execution", wait_timeout=0)
        with self.assertRaises(ValueError):
            with g:
                raise ValueError("boom")
        # No stamp written → a fresh guard acquires normally.
        g2 = self.guard(stamp="trade_execution", wait_timeout=0)
        g2.acquire()
        g2.release()

    def test_yesterday_stamp_does_not_block(self):
        """A stamp from a prior day is ignored — the trading day rolled over."""
        stamp = Path(self.data_dir) / "run_stamps" / "trade_execution.date"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        yesterday = (datetime.date.fromisoformat(_today()) - datetime.timedelta(days=1)).isoformat()
        stamp.write_text(yesterday, encoding="utf-8")
        with self.guard(stamp="trade_execution", wait_timeout=0):
            pass  # runs, and refreshes the stamp to today
        self.assertEqual(stamp.read_text(encoding="utf-8").strip(), _today())

    def test_unstamped_guard_runs_repeatedly(self):
        """Without a stamp (the pipeline case) the guard never claims 'already ran'."""
        for _ in range(3):
            g = self.guard(wait_timeout=0)  # no stamp
            g.acquire()
            g.release()

    def test_distinct_stamps_are_independent(self):
        """One task's daily stamp must not suppress a different task."""
        with self.guard(stamp="trade_execution", wait_timeout=0):
            pass
        # A different stamp key on the same lock (after release) runs freely.
        with self.guard(stamp="something_else", wait_timeout=0):
            pass

    def test_runskipped_is_the_common_base(self):
        """Callers can catch RunSkipped to cover both skip reasons."""
        self.assertTrue(issubclass(AlreadyRanToday, RunSkipped))
        self.assertTrue(issubclass(Busy, RunSkipped))


if __name__ == "__main__":
    unittest.main()
