"""R&D #123 sell-everything study: the flag rules, declustering, the verdict and the data merge."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import sell_everything_study as se  # noqa: E402

# Six closes: index 5 is the day judged. SPY falls 5 of 5 days, about -6% over five sessions.
SPY_DOWN = [100, 99, 97, 96, 95, 94]
VIX_UP = [20, 21, 22, 23, 24, 26]          # 26 / 20 = 1.30 >= 1.25
GOLD_DOWN = [10, 10, 10, 10, 10, 9.9]
GOLD_UP = [10, 10, 10, 10, 10, 10.2]
BTC_DOWN = [50, 49, 48, 47, 46, 45]
BTC_UP = [50, 51, 52, 53, 54, 55]


class TestClassify(unittest.TestCase):
    def test_everything_down_is_a_flag(self):
        self.assertEqual(se.classify(SPY_DOWN, VIX_UP, GOLD_DOWN, None, 5), "flag")
        self.assertEqual(se.classify(SPY_DOWN, VIX_UP, GOLD_DOWN, BTC_DOWN, 5), "flag")

    def test_a_working_haven_makes_it_plain_stress(self):
        self.assertEqual(se.classify(SPY_DOWN, VIX_UP, GOLD_UP, None, 5), "stress")
        self.assertEqual(se.classify(SPY_DOWN, VIX_UP, GOLD_DOWN, BTC_UP, 5), "stress")   # bitcoin up

    def test_no_stress_without_each_spy_and_vix_condition(self):
        self.assertIsNone(se.classify([100, 99, 98.5, 98, 97.8, 97.5], VIX_UP, GOLD_DOWN, None, 5))  # -2.5% only
        self.assertIsNone(se.classify(SPY_DOWN, [20, 20, 21, 21, 22, 24], GOLD_DOWN, None, 5))       # VIX 1.20
        self.assertIsNone(se.classify([100, 101, 102, 96, 95, 94], VIX_UP, GOLD_DOWN, None, 5))      # 3 down days
        self.assertIsNone(se.classify(SPY_DOWN, VIX_UP, GOLD_DOWN, None, 4))                          # no lookback

    def test_uses_no_future_data(self):
        later = SPY_DOWN + [200, 300]
        self.assertEqual(se.classify(later, VIX_UP + [10, 10], GOLD_DOWN + [1, 1], None, 5), "flag")


class TestDeclusterAndOutcomes(unittest.TestCase):
    def test_keeps_the_first_day_of_each_run(self):
        self.assertEqual(se.decluster([5, 6, 7, 30, 49, 50, 80], gap=20), [5, 30, 50, 80])

    def test_forward_returns_and_worst_close(self):
        spy = [100.0] + [100.0 + k for k in range(1, 25)]
        spy[3] = 90.0
        o = se.outcomes(spy, 0)
        self.assertAlmostEqual(o["r5"], 0.05)
        self.assertAlmostEqual(o["r20"], 0.20)
        self.assertAlmostEqual(o["worst20"], -0.10)


class TestVerdict(unittest.TestCase):
    STRESS = [0.01, 0.02, 0.0, 0.015, 0.005, 0.01, 0.02, 0.0]

    def test_too_few_events(self):
        self.assertEqual(se.verdict([0.05] * 7, self.STRESS)[0], "INSUFFICIENT")
        self.assertEqual(se.verdict([0.05] * 8, self.STRESS[:7])[0], "INSUFFICIENT")

    def test_bounce(self):
        flag = [0.08, 0.10, 0.07, 0.09, 0.11, 0.06, 0.08, 0.10]
        self.assertEqual(se.verdict(flag, self.STRESS)[0], "BOUNCE_AFTER_FLAG")

    def test_falls_further(self):
        flag = [-0.08, -0.10, -0.07, -0.09, -0.11, -0.06, -0.08, -0.10]
        self.assertEqual(se.verdict(flag, self.STRESS)[0], "FALLS_FURTHER_AFTER_FLAG")

    def test_higher_than_stress_but_still_negative_is_not_a_bounce(self):
        stress = [-0.20, -0.21, -0.19, -0.22, -0.20, -0.18, -0.21, -0.20]
        flag = [-0.01, -0.02, -0.015, -0.01, -0.005, -0.02, -0.01, -0.015]
        v, t = se.verdict(flag, stress)
        self.assertGreater(t, 2)
        self.assertEqual(v, "INCONCLUSIVE")

    def test_small_difference_is_inconclusive(self):
        flag = [0.03, -0.02, 0.01, 0.02, -0.01, 0.0, 0.02, 0.01]
        self.assertEqual(se.verdict(flag, self.STRESS)[0], "INCONCLUSIVE")


class TestMergeOurs(unittest.TestCase):
    def test_ours_first_yahoo_only_after_our_last_day(self):
        ours = {"2026-11-18": 100.0, "2026-11-19": 99.0}
        yahoo = {"2026-11-17": 50.0, "2026-11-18": 100.2, "2026-11-19": 99.1, "2026-11-20": 97.0}
        merged, filled, worst = se.merge_ours(ours, yahoo)
        self.assertEqual(merged, {"2026-11-18": 100.0, "2026-11-19": 99.0, "2026-11-20": 97.0})
        self.assertEqual(filled, 1)
        self.assertLess(worst, 0.005)

    def test_disagreement_aborts(self):
        with self.assertRaises(ValueError):
            se.merge_ours({"2026-11-19": 99.0}, {"2026-11-19": 100.0, "2026-11-20": 97.0})
        with self.assertRaises(ValueError):
            se.merge_ours({"2026-11-19": 99.0}, {"2026-11-20": 97.0})


if __name__ == "__main__":
    unittest.main()
