"""R&D #113 study: the Oracle filter, the features, the outcome, thinning and the fixed verdict."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import waiver_trend_loss_study as st


def ev(month, flagged, big_loss, ex20, flag="far"):
    return {"day": f"{month}-15", flag: flagged, "big_loss": big_loss, "ex20": ex20}


class TestOracleMask(unittest.TestCase):
    def test_waiver_only_is_low_rr_elite(self):
        a = {"s10": np.array([6.9, 6.9, 1.0, 6.9]), "l60": np.array([8.7, 8.7, 8.7, 0.0]),
             "rr": np.array([0.15, 2.0, 0.15, 0.15]), "setup_full": np.array([True, True, True, True])}
        admit, waiver = st.oracle_mask(a)
        # SA-like (elite, low R/R) / good R/R / S10 under the floor / not elite
        self.assertEqual(admit.tolist(), [True, True, False, False])
        self.assertEqual(waiver.tolist(), [True, False, False, False])


class TestFeaturesAndOutcome(unittest.TestCase):
    def test_far_below_high_and_falling_200(self):
        closes = [100.0] * 100 + [100 - k * 0.2 for k in range(200)]     # a slow fall
        f = st.bar_features(closes, len(closes) - 1)
        self.assertGreater(f["below_high"], 0.10)
        self.assertTrue(f["below_falling_200"])
        flat = [50.0] * 300
        g = st.bar_features(flat, 299)
        self.assertEqual((round(g["below_high"], 6), g["below_falling_200"]), (0.0, False))
        self.assertIsNone(st.bar_features(flat, 100))                    # not enough history

    def test_outcome_starts_at_the_next_close(self):
        closes = [100.0, 200.0] + [200.0] * 9 + [160.0] + [200.0] * 20  # entry is 200 (i+1), not 100
        out = st.outcome(closes, 0, lambda k: 1.0)
        self.assertTrue(out["big_loss"])                                 # 160 <= 200 * 0.85
        self.assertAlmostEqual(out["ex20"], 0.0)
        shallow = st.outcome([100.0, 200.0] + [175.0] + [200.0] * 30, 0, lambda k: 1.0)
        self.assertFalse(shallow["big_loss"])                            # 175 > 170
        self.assertIsNone(st.outcome([1.0] * 15, 0, lambda k: 1.0))       # window not complete

    def test_features_read_no_bar_after_the_decision_day(self):
        closes = [50.0] * 300 + [500.0]                                  # a spike the day after bar 299
        self.assertEqual(round(st.bar_features(closes, 299)["below_high"], 6), 0.0)

    def test_thin_keeps_one_event_per_window(self):
        evs = [{"sym": "X", "i": i} for i in (10, 15, 30, 31, 52)]
        self.assertEqual([e["i"] for e in st.thin(evs)], [10, 31, 52])


class TestVerdict(unittest.TestCase):
    def months(self, n, start=2024):      # spans SPLIT_MONTH (2025-01)
        return [f"{start + k // 12}-{k % 12 + 1:02d}" for k in range(n)]

    def series(self, loss_f, loss_o, ret_f=-0.01, ret_o=0.0, n=24):
        evs = []
        for k, m in enumerate(self.months(n)):
            jitter = 0.02 * (k % 3)
            evs += [ev(m, True, b, ret_f) for b in ([1] * int(10 * (loss_f + jitter)) + [0] * (10 - int(10 * (loss_f + jitter))))]
            evs += [ev(m, False, b, ret_o) for b in ([1] * int(10 * loss_o) + [0] * (10 - int(10 * loss_o)))]
        return evs

    def judge(self, evs):
        return st.verdict(st.monthly_diffs(evs, "far"), sum(e["far"] for e in evs))

    def test_filter_helps(self):
        self.assertEqual(self.judge(self.series(0.5, 0.1))["verdict"], "FILTER_HELPS")

    def test_filter_hurts(self):
        self.assertEqual(self.judge(self.series(0.1, 0.5, ret_f=0.02))["verdict"], "FILTER_HURTS")

    def test_more_losses_but_better_return_is_not_a_pass(self):
        self.assertEqual(self.judge(self.series(0.5, 0.1, ret_f=0.05))["verdict"], "INCONCLUSIVE")

    def test_one_half_against_is_not_a_pass(self):
        evs = [e for e in self.series(0.5, 0.1) if e["day"] < st.SPLIT_MONTH] + \
              [e for e in self.series(0.1, 0.1) if e["day"] >= st.SPLIT_MONTH]
        self.assertNotEqual(self.judge(evs)["verdict"], "FILTER_HELPS")

    def test_a_positive_but_noisy_difference_is_not_a_pass(self):
        """Mean > 0, both halves > 0, return no better, but HAC t below 1.96: INCONCLUSIVE."""
        diffs = [(m, d, -0.01) for m, d in zip(self.months(24), [0.6, -0.5, 0.0] * 8)]
        r = st.verdict(diffs, 200)
        self.assertGreater(r["d_loss"], 0)
        self.assertTrue(all(h > 0 for h in r["d_loss_halves"]))
        self.assertLess(r["t_loss"], st.GATE_T)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_a_month_needs_both_groups(self):
        evs = [ev("2024-01", True, 1, 0.0), ev("2024-01", False, 0, 0.0), ev("2024-02", True, 1, 0.0)]
        self.assertEqual(st.monthly_diffs(evs, "far"), [("2024-01", 1.0, 0.0)])

    def test_too_few_months_or_events(self):
        self.assertEqual(self.judge(self.series(0.5, 0.1, n=6))["verdict"], "INSUFFICIENT")
        r = st.verdict([("2023-01", 0.4, -0.01)] * 30, 50)
        self.assertEqual(r["verdict"], "INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
