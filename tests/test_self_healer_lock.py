"""
Regression test: the on-demand OHLCV self-healer must not burn its once-per-run attempt
when repair_missing skips because another process holds the RapidAPI lock.

Red before the fix (a lock-skip was indistinguishable from "API had nothing new", so the
symbol was marked attempted and never retried); green after repair_missing signals ``locked``
and _heal_symbol_cache releases the guard.
"""
import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game


class TestSelfHealerLockDefer(unittest.TestCase):

    def setUp(self):
        game._HEAL_ATTEMPTED.clear()
        self.addCleanup(game._HEAL_ATTEMPTED.clear)

    def test_lock_contention_does_not_burn_attempt(self):
        locked = {"updated": 0, "skipped": 1, "errors": [], "locked": True}
        with mock.patch.object(game.rapidapi, "repair_missing", return_value=locked) as m:
            self.assertFalse(game._heal_symbol_cache("AAA"))
            self.assertNotIn("AAA", game._HEAL_ATTEMPTED)   # guard released for a later retry
            game._heal_symbol_cache("AAA")                  # later pass retries, not short-circuits
        self.assertEqual(m.call_count, 2)

    def test_no_update_still_marks_attempted(self):
        # The normal "API had nothing new" path stays one-shot per run (behavior unchanged).
        no_update = {"updated": 0, "skipped": 1, "errors": []}
        with mock.patch.object(game.rapidapi, "repair_missing", return_value=no_update) as m:
            game._heal_symbol_cache("BBB")
            self.assertIn("BBB", game._HEAL_ATTEMPTED)
            game._heal_symbol_cache("BBB")                  # second call short-circuits
        self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main()
