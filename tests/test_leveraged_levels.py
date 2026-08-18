"""Unit tests for the Phase-1 leveraged/inverse/crypto levels study.

Covers the three genuinely-new pieces (the imported outcome metric is already tested
by test_backtest_levels): the split-adjuster (forward + reverse split continuity, and
the open-agreement guard that must PRESERVE a real intraday crash), the volatility-band
level math + the support<price<resistance ordering guard, and the R-multiple
expectancy metric.
"""
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "backtesting"))

import leveraged_levels_study as lls


class TestSplitAdjust(unittest.TestCase):
    def test_forward_split_and_crash_preserved(self):
        # Forward 2:1 split between idx3 (103) and idx4 (51.5): close & open both ~halve.
        # Then a real intraday CRASH at idx7 (close 25 off prior 51) whose OPEN (50) sits
        # near the prior close — open/close ratios DISAGREE, so it must NOT be adjusted.
        dates = [f"2020-01-0{i}" for i in range(1, 9)]
        o = [100, 101, 102, 102, 51, 51.5, 51.5, 50]
        c = [100, 102, 101, 103, 51.5, 52, 51, 25]
        h = c[:]
        l = c[:]
        o2, h2, l2, c2, splits = lls._split_adjust(dates, o, h, l, c)
        self.assertEqual(c2, [50.0, 51.0, 50.5, 51.5, 51.5, 52.0, 51.0, 25.0])
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["date"], "2020-01-05")
        self.assertEqual(splits[0]["ratio"], 0.5)
        # The crash bar keeps its real (unscaled) value — factor there is 1.0.
        self.assertEqual(c2[7], 25.0)

    def test_reverse_split_continuity(self):
        # 1:4 reverse split between idx2 (10.1) and idx3 (40.4).
        dates = [f"2021-02-0{i}" for i in range(1, 7)]
        o = [10, 10.1, 10.0, 40.0, 41, 40]
        c = [10, 10.2, 10.1, 40.4, 41, 40]
        o2, h2, l2, c2, splits = lls._split_adjust(dates, o, c[:], c[:], c)
        self.assertEqual(c2, [40.0, 40.8, 40.4, 40.4, 41.0, 40.0])
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["ratio"], 4.0)


class TestLevelMath(unittest.TestCase):
    def setUp(self):
        # Alternating 10/11 series → steady TR = 1.5 (so ATR is exactly 1.5), EMA
        # oscillates around ~10.5. h = c+0.5, l = c−0.5.
        self.c = [10.0 if i % 2 == 0 else 11.0 for i in range(40)]
        self.h = [x + 0.5 for x in self.c]
        self.l = [x - 0.5 for x in self.c]
        self.feats = lls._build_features(self.h, self.l, self.c)

    def test_atr_is_exact(self):
        self.assertAlmostEqual(self.feats["atr"][14][30], 1.5, places=6)

    def test_keltner_bands_exact(self):
        i, m, n = 30, 2.0, 14           # c[30] = 10.0, below EMA → price inside band
        mid = self.feats["ema"][n][i]
        atr = self.feats["atr"][n][i]
        s, r = lls._levels("K", self.feats, self.h, self.l, self.c, i, self.c[i], m, n)
        self.assertAlmostEqual(s, mid - m * atr, places=6)
        self.assertAlmostEqual(r, mid + m * atr, places=6)
        self.assertAlmostEqual(r - s, 2 * m * atr, places=6)

    def test_mean_revert_target_ok_below_mean(self):
        # price (10.0) below EMA → target=mid is above price → guard passes.
        i, m, n = 30, 3.0, 14
        mid = self.feats["ema"][n][i]
        s, r = lls._levels("K-MR", self.feats, self.h, self.l, self.c, i, self.c[i], m, n)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(r, mid, places=6)

    def test_ordering_guard_rejects_target_below_price(self):
        # price (11.0) above EMA → K-MR target=mid ≤ price → the instant-win case → skip.
        i, m, n = 31, 3.0, 14
        self.assertEqual(self.c[i], 11.0)
        s, r = lls._levels("K-MR", self.feats, self.h, self.l, self.c, i, self.c[i], m, n)
        self.assertIsNone(s)
        self.assertIsNone(r)

    def test_guard_helper(self):
        self.assertEqual(lls._guard(9.0, 11.0, 10.0), (9.0, 11.0))
        self.assertEqual(lls._guard(10.0, 11.0, 10.0), (None, None))   # price == support
        self.assertEqual(lls._guard(9.0, 10.0, 10.0), (None, None))    # price == resistance
        self.assertEqual(lls._guard(None, 11.0, 10.0), (None, None))


class TestExpectancy(unittest.TestCase):
    def test_e_r_and_win_rate(self):
        recs = [
            {"outcome": "target_first", "rr": 2.0, "r_mult": 2.0},
            {"outcome": "stop_first", "rr": 1.5, "r_mult": -1.0},
            {"outcome": "neither", "rr": 3.0, "r_mult": 0.4},
        ]
        m = lls._metrics(recs)
        self.assertEqual(m["n_both"], 3)
        self.assertEqual(m["n_decided"], 2)
        self.assertAlmostEqual(m["e_r"], round((2.0 - 1.0 + 0.4) / 3, 3), places=6)
        self.assertEqual(m["win_rate"], 50.0)       # 1 target / 2 decided
        self.assertEqual(m["median_rr"], 2.0)
        self.assertEqual(m["decided_rate"], round(2 / 3, 3))

    def test_empty(self):
        m = lls._metrics([])
        self.assertIsNone(m["e_r"])
        self.assertEqual(m["n_both"], 0)


if __name__ == "__main__":
    unittest.main()
