"""Tests for scripts/backtesting/feature_ic_subgroup_study.py (R&D #68 phase 1): the per-date
Spearman IC within groups, the minimum-names rule, Benjamini-Hochberg, and the verdict (discovery on
train, confirmation on test, subgroup-specific flags)."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import feature_ic_subgroup_study as fs  # noqa: E402


def _spearman(x, y):
    rx, ry = pd.Series(x).rank(), pd.Series(y).rank()
    return float(np.corrcoef(rx, ry)[0, 1])


class TestPriceFeatures(unittest.TestCase):
    """The row builder's price inputs and outcome (review of #263: the first version used the
    snapshot price at both ends, a built-in negative IC; and the definitions had no test)."""

    def test_definitions(self):
        c = [100.0 + k for k in range(100)]                      # c[k] = 100 + k
        j = 60
        f = fs.price_features(c, j)
        self.assertAlmostEqual(f["mom60"], c[60] / c[0] - 1)
        self.assertAlmostEqual(f["ret3"], c[60] / c[57] - 1)
        self.assertAlmostEqual(f["dist_sma20"], c[60] / (sum(c[40:60]) / 20) - 1)   # 20 closes before j
        self.assertAlmostEqual(f["f20"], c[81] / c[61] - 1)       # next close -> 20 sessions on

    def test_outcome_shares_no_price_with_the_inputs(self):
        c = [100.0] * 100
        base = fs.price_features(c, 60)
        bumped = list(c)
        bumped[60] = 130.0                                        # a wrong price on the day itself
        f = fs.price_features(bumped, 60)
        self.assertGreater(f["ret3"], base["ret3"])               # the inputs see it
        self.assertEqual(f["f20"], base["f20"])                   # the outcome doesn't

    def test_needs_history_and_future(self):
        c = [100.0] * 100
        self.assertIsNone(fs.price_features(c, 59))               # < 60 sessions of history
        self.assertIsNone(fs.price_features(c, 79))               # c[j+21] missing
        self.assertIsNotNone(fs.price_features(c, 78))


class TestDailyIC(unittest.TestCase):
    def test_matches_spearman_per_day_and_group(self):
        rng = np.random.default_rng(1)
        rows = []
        for day in ("2024-01-02", "2024-01-03"):
            for grp in ("A", "B"):
                for k in range(12):
                    rows.append({"day": day, "g": grp, "x": rng.normal(), "f20": rng.normal()})
        df = pd.DataFrame(rows)
        out = fs.daily_ic(df, "x", "g")
        self.assertEqual(len(out), 4)
        for _, r in out.iterrows():
            sub = df[(df.day == r.day) & (df.g == r.g)]
            self.assertAlmostEqual(r.ic, _spearman(sub.x, sub.f20), places=10)

    def test_ties_and_perfect_order(self):
        df = pd.DataFrame({"day": ["d"] * 10, "x": [1, 1, 2, 2, 3, 3, 4, 4, 5, 5], "f20": range(10)})
        self.assertAlmostEqual(fs.daily_ic(df, "x", None).ic.iloc[0], _spearman(df.x, df.f20), places=10)
        df2 = pd.DataFrame({"day": ["d"] * 10, "x": range(10), "f20": [-v for v in range(10)]})
        self.assertAlmostEqual(fs.daily_ic(df2, "x", None).ic.iloc[0], -1.0)

    def test_min_names_missing_values_and_constant_feature(self):
        df = pd.DataFrame({"day": ["d"] * 10, "x": list(range(9)) + [None], "f20": range(10)})
        self.assertTrue(fs.daily_ic(df, "x", None).empty)            # 9 usable names < 10
        df = pd.DataFrame({"day": ["d"] * 10, "x": [1.0] * 10, "f20": range(10)})
        self.assertTrue(fs.daily_ic(df, "x", None).empty)            # no variance: no IC


class TestBH(unittest.TestCase):
    def test_step_up(self):
        p = {"a": 0.001, "b": 0.02, "c": 0.03, "d": 0.5}
        # m=4, q=0.1: thresholds .025 .05 .075 .1 -> a, b, c pass (c at rank 3: .03 <= .075)
        self.assertEqual(fs.benjamini_hochberg(p, 0.10), {"a", "b", "c"})
        # step-up: a later passing rank pulls earlier failing ones in
        self.assertEqual(fs.benjamini_hochberg({"a": 0.06, "b": 0.07}, 0.10), {"a", "b"})   # .06 > .05, .07 <= .1
        self.assertEqual(fs.benjamini_hochberg({"a": 0.10}, 0.10), {"a"})                    # p == threshold passes
        self.assertEqual(fs.benjamini_hochberg({"a": 0.06, "b": 0.5}, 0.10), set())   # .06 > .05
        self.assertEqual(fs.benjamini_hochberg({"a": None, "b": 0.01}, 0.10), {"b"})

    def test_p_value(self):
        self.assertAlmostEqual(fs.p_two_sided(1.96), 0.05, places=3)
        self.assertIsNone(fs.p_two_sided(None))


def _s(mean, t, dates=200):
    return {"dates": dates, "mean_ic": mean, "t_hac": t}


class TestEvaluate(unittest.TestCase):
    def test_confirmation_needs_discovery_sign_and_test_t(self):
        res = {
            ("x", "ALL", "ALL"): {"train": _s(0.03, 5.0), "test": _s(0.02, 2.5)},     # confirmed
            ("y", "ALL", "ALL"): {"train": _s(0.03, 5.0), "test": _s(-0.02, -2.5)},   # sign flips
            ("z", "ALL", "ALL"): {"train": _s(0.03, 5.0), "test": _s(0.02, 1.5)},     # test t too small
            ("w", "ALL", "ALL"): {"train": _s(0.01, 0.5), "test": _s(0.05, 9.0)},     # never discovered
            ("v", "ALL", "ALL"): {"train": _s(0.03, 9.0, dates=30), "test": _s(0.05, 9.0)},  # too few dates
        }
        ev = fs.evaluate(res)
        self.assertEqual(ev["tests"], 4)
        self.assertEqual(ev["confirmed"], [("x", "ALL", "ALL")])
        self.assertEqual(set(ev["discovered"]), {("x", "ALL", "ALL"), ("y", "ALL", "ALL"), ("z", "ALL", "ALL")})
        self.assertEqual(ev["verdict"], "SIGNAL_FOUND")

    def test_negative_ic_confirms_too(self):
        res = {("x", "ALL", "ALL"): {"train": _s(-0.03, -5.0), "test": _s(-0.02, -2.5)}}
        self.assertEqual(fs.evaluate(res)["confirmed"], [("x", "ALL", "ALL")])

    def test_subgroup_specific(self):
        res = {
            ("x", "ALL", "ALL"): {"train": _s(0.003, 0.3), "test": _s(0.0, 0.1)},
            ("x", "sector", "Energy"): {"train": _s(0.05, 6.0), "test": _s(0.04, 3.0)},
            ("y", "ALL", "ALL"): {"train": _s(0.03, 6.0), "test": _s(0.03, 3.0)},
            ("y", "size", "small"): {"train": _s(0.04, 6.0), "test": _s(0.03, 3.0)},
        }
        ev = fs.evaluate(res)
        self.assertEqual(ev["subgroup_specific"], [("x", "sector", "Energy")])   # y small: ALL confirmed too

    def test_opposite_sign_is_specific(self):
        res = {
            ("y", "ALL", "ALL"): {"train": _s(0.03, 6.0), "test": _s(0.03, 3.0)},
            ("y", "regime", "SPY<200d"): {"train": _s(-0.04, -6.0), "test": _s(-0.03, -3.0)},
        }
        self.assertEqual(fs.evaluate(res)["subgroup_specific"], [("y", "regime", "SPY<200d")])

    def test_no_signal(self):
        res = {("x", "ALL", "ALL"): {"train": _s(0.001, 0.2), "test": _s(0.0, 0.0)}}
        self.assertEqual(fs.evaluate(res)["verdict"], "NO_CONFIRMED_SIGNAL")


class TestGroupsAndRun(unittest.TestCase):
    def test_regime_and_size(self):
        days = [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(220)]
        spy = {d: {"4. close": 100.0 + i} for i, d in enumerate(days)}     # rising: above its mean from day 200
        df = pd.DataFrame({"day": [days[100]] * 3 + [days[210]] * 3, "mcap": [1, 2, 3, 30, 10, 20]})
        out = fs.add_groups(df, spy)
        self.assertEqual(list(out.regime), ["SPY<200d"] * 3 + ["SPY>200d"] * 3)
        self.assertEqual([str(s) for s in out["size"]], ["small", "mid", "large", "large", "small", "mid"])

    def test_run_splits_train_and_test(self):
        rng = np.random.default_rng(2)
        rows = []
        for day in ("2024-06-03", "2025-06-02"):
            for k in range(12):
                x = rng.normal()
                rows.append({"day": day, "sector": "Energy", "regime": "SPY>200d", "size": "mid",
                             **{f: x for f in fs.FEATURES}, "f20": x})
        res = fs.run(pd.DataFrame(rows))
        r = res[("s10", "ALL", "ALL")]
        self.assertEqual((r["train"]["dates"], r["test"]["dates"]), (1, 1))
        self.assertAlmostEqual(r["train"]["mean_ic"], 1.0)
        self.assertIn(("s10", "sector", "Energy"), res)


if __name__ == "__main__":
    unittest.main()
