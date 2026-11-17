"""Tests for scripts/backtesting/leadlag_graph_filtered.py (R&D #93b): the universe filter (train-only
dollar volume, US filer, no ETF follower), the pairwise correlation, and the two-window rule."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import leadlag_graph_filtered as lf  # noqa: E402

T = 3600


def world(flip_b=False, link=0.35):
    r = np.random.default_rng(7)
    m = r.normal(0, 0.01, T)
    X = r.normal(0, 0.01, (T, 4))
    sign = np.ones(T)
    if flip_b:
        sign[2400:] = -1.0
    X[2:, 1] += link * sign[2:] * X[:-2, 0]
    R = X + 1.1 * m[:, None]
    k = np.arange(T)
    return R, m, k < 1200, (k >= 1200) & (k < 2400), k >= 2400


class TestUniverse(unittest.TestCase):
    def test_allowed(self):
        syms = ["BIG", "ETFX", "FOREIGN", "SMALL"]
        dv = {"BIG": 50e6, "ETFX": 90e6, "FOREIGN": 40e6, "SMALL": 5e6}
        lab = {"BIG": {"sic_div": "73"}, "ETFX": {"sic_div": "67", "etf": True}, "FOREIGN": {"sic_div": None}, "SMALL": {"sic_div": "35"}}
        lead, fol = lf.allowed(syms, dv, lab)
        self.assertEqual(lead.tolist(), [True, True, True, False])
        self.assertEqual(fol.tolist(), [True, False, False, False])

    def test_dollar_volume_uses_train_years_only(self):
        days = ["2010-01-04", "2014-06-02", "2018-01-02"]
        dv = lf.train_dollar_volume(days, [10.0, 20.0, 1000.0], [1e6, 2e6, 1e9], "2016-01-01")
        self.assertEqual(dv, float(np.median([10e6, 40e6])))                 # 2018 ignored


class TestRule(unittest.TestCase):
    def run_world(self, **kw):
        R, m, tr, a, b = world(**kw)
        E = lf.lg.residualize(R, m, tr)
        W = lf.lg.whiten(E, tr)
        ok = np.ones(4, bool)
        return lf.find_edges(E, W, tr, a, b, ok, ok)

    def test_link_holding_in_both_windows_is_confirmed(self):
        res = self.run_world()
        self.assertIn((2, 0, 1), {(lag, i, j) for lag, i, j, *_ in res["both"]})

    def test_link_flipping_in_window_b_is_not(self):
        res = self.run_world(flip_b=True)
        self.assertNotIn((2, 0, 1), {(lag, i, j) for lag, i, j, *_ in res["both"]})
        self.assertGreaterEqual(res["a_only_rule"], 1)                          # it did hold in A

    def test_disallowed_follower_is_never_tested(self):
        R, m, tr, a, b = world()
        E = lf.lg.residualize(R, m, tr)
        W = lf.lg.whiten(E, tr)
        fol = np.array([True, False, True, True])
        res = lf.find_edges(E, W, tr, a, b, np.ones(4, bool), fol)
        self.assertEqual([x for x in res["both"] if x[2] == 1], [])

    def test_pair_corr(self):
        x = np.arange(20, dtype=float)
        r, n = lf.pair_corr(x, x, 1, np.ones(20, bool))
        self.assertAlmostEqual(r, 1.0)
        self.assertEqual(n, 19)
        self.assertTrue(np.isnan(lf.pair_corr(np.ones(20), x, 1, np.ones(20, bool))[0]))


if __name__ == "__main__":
    unittest.main()
