"""G1 breaker freeze study: the replayed breaker state, episodes, spells and the verdict."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import breaker_freeze_study as bf  # noqa: E402


class TestReplay(unittest.TestCase):
    def test_uses_the_live_thresholds(self):
        c = [100.0] * 20 + [97.9]                       # -2.1% day
        self.assertIn("Single-Day", bf.on(c, 20))
        c = [100.0] * 15 + [99, 98, 97, 96, 95.0]        # -5% from the 10-session peak, no 2% day
        self.assertIn("Drawdown", bf.on(c, 19))
        self.assertIsNone(bf.on([100.0] * 20, 19))

    def test_episodes_need_twenty_quiet_sessions(self):
        c = [100.0] * 25 + [97.0] + [97.0] * 5 + [94.0] + [94.0] * 30 + [91.0]
        eps = bf.episodes(c)
        self.assertEqual(eps[0], 25)                     # the first drop
        self.assertNotIn(31, eps)                        # the breaker never went quiet for 20 sessions in between

    def test_spell_length_counts_consecutive_on_sessions(self):
        c = [100.0] * 20 + [97.0, 94.0, 99.9, 100.0]       # on, on (-6% from the peak), then off
        self.assertEqual(bf.spell_length(c, 20), 2)


class TestVerdict(unittest.TestCase):
    def test_branches(self):
        self.assertEqual(bf.verdict([0.05] * 14)[0], "INSUFFICIENT")
        with mock.patch.object(bf, "t_stat", return_value=2.5):
            self.assertEqual(bf.verdict([0.02] * 20)[0], "FREEZE_COSTS")
            self.assertEqual(bf.verdict([-0.02] * 20)[0], "INCONCLUSIVE")
        with mock.patch.object(bf, "t_stat", return_value=-2.5):
            self.assertEqual(bf.verdict([-0.02] * 20)[0], "FREEZE_HELPS")
        with mock.patch.object(bf, "t_stat", return_value=1.5):
            self.assertEqual(bf.verdict([0.02] * 20)[0], "INCONCLUSIVE")

    def test_t_stat(self):
        self.assertAlmostEqual(bf.t_stat([1.0, 3.0]), 2.0)
        self.assertIsNone(bf.t_stat([1.0]))


if __name__ == "__main__":
    unittest.main()
