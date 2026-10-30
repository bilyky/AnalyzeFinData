"""Ranks, Spearman IC, per-date IC and the verdict of the rank IC study."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rank_ic_study as ri


class TestSpearman(unittest.TestCase):
    def test_ranks_average_ties(self):
        self.assertEqual(ri.ranks(np.array([3.0, 1.0, 3.0, 2.0])).tolist(), [2.5, 0.0, 2.5, 1.0])

    def test_spearman_monotone_and_reversed_and_constant(self):
        x = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(ri.spearman(x, [10, 20, 30, 40, 1000]), 1.0)   # rank, not value
        self.assertAlmostEqual(ri.spearman(x, [5, 4, 3, 2, 1]), -1.0)
        self.assertIsNone(ri.spearman(x, [1, 1, 1, 1, 1]))


class TestDailyIC(unittest.TestCase):
    def test_only_dates_with_enough_names_and_only_masked_obs(self):
        days = np.array(["d1"] * 4 + ["d2"] * 2)
        score = np.array([1.0, 2.0, 3.0, 4.0, 1.0, 2.0])
        ret = np.array([1.0, 2.0, 3.0, np.nan, 5.0, 6.0])
        mask = np.array([True, True, True, True, True, True])
        ic = ri.daily_ic(days, score, ret, mask, min_names=3)
        self.assertEqual(list(ic), ["d1"])                 # d2 has 2 names; NaN return dropped on d1
        self.assertAlmostEqual(ic["d1"], 1.0)
        # d1 has 4 obs but one has no return: with min_names 4 it must not count.
        self.assertEqual(ri.daily_ic(days, score, ret, mask, min_names=4), {})
        none = ri.daily_ic(days, score, ret, np.zeros(6, dtype=bool), min_names=1)
        self.assertEqual(none, {})

    def test_diff_pairs_dates(self):
        d = ri.diff({"a": 0.3, "b": 0.1, "c": 0.5}, {"a": 0.1, "b": 0.1}, lag=0)
        self.assertEqual(d["days"], 2)
        self.assertAlmostEqual(d["mean_ic"], 0.1)


class TestVerdict(unittest.TestCase):
    def test_table(self):
        self.assertEqual(ri.verdict({"mean_ic": 0.02, "t_hac": 2.3}), "S10_RANKS_BETTER")
        self.assertEqual(ri.verdict({"mean_ic": -0.02, "t_hac": -2.3}), "COMBINED_RANKS_BETTER")
        self.assertEqual(ri.verdict({"mean_ic": 0.02, "t_hac": 1.2}), "INCONCLUSIVE")
        self.assertEqual(ri.verdict({"mean_ic": None, "t_hac": None}), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
