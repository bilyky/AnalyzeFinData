"""Cohort mask, per-date cohort difference and verdict of the rebound-path study."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rebound_path_study as rb


def _a(days, past60, mf_weak, f20):
    return {"day": np.array(days), "past60": np.array(past60, dtype=float),
            "mf_weak": np.array(mf_weak, dtype=bool), "f20": np.array(f20, dtype=float)}


class TestMask(unittest.TestCase):
    def test_threshold_and_money_flow(self):
        a = _a(["d"] * 4, [-25, -25, -10, -20], [False, True, False, False], [0] * 4)
        self.assertEqual(rb.rebound_mask(a).tolist(), [True, False, False, True])
        self.assertEqual(rb.rebound_mask(a, need_flow=False).tolist(), [True, True, False, True])
        self.assertEqual(rb.rebound_mask(a, -24.0).tolist(), [True, False, False, False])


class TestCohortDiff(unittest.TestCase):
    def test_per_date_difference_and_loss_share(self):
        a = _a(["d1", "d1", "d1", "d2", "d2"], [-30, 0, 0, -30, 0], [False] * 5, [6.0, 2.0, 0.0, -40.0, 0.0])
        r = rb.cohort_diff(a, rb.rebound_mask(a), a["f20"], lag=0)
        lo, hi = np.quantile(a["f20"], [0.01, 0.99])           # the study's 1/99% clip
        c = np.clip(a["f20"], lo, hi)
        want = ((c[0] - (c[1] + c[2]) / 2) + (c[3] - c[4])) / 2   # per-date diffs, then averaged
        self.assertEqual(r["days"], 2)
        self.assertAlmostEqual(r["mean_diff"], round(want, 3))
        self.assertEqual(r["loss30_share"], 0.5)      # one of the two cohort obs lost 30%+


class TestVerdict(unittest.TestCase):
    def test_table(self):
        good_p, good_c = {"mean_daily_pct": 0.02, "t_hac": 2.3}, {"mean_diff": 0.5, "t_hac": 2.2}
        self.assertEqual(rb.verdict(good_p, good_c, -12, -10), "REBOUND_HELPS")
        self.assertEqual(rb.verdict(good_p, {"mean_diff": 0.5, "t_hac": 1.0}, -12, -10), "INCONCLUSIVE")
        self.assertEqual(rb.verdict(good_p, good_c, -16, -10), "INCONCLUSIVE")
        self.assertEqual(rb.verdict({"mean_daily_pct": -0.02, "t_hac": -2.3}, good_c, -10, -10), "REBOUND_HURTS")
        self.assertEqual(rb.verdict({"mean_daily_pct": None, "t_hac": None}, good_c, -10, -10), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
