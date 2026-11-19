"""Tests for scripts/backtesting/leadlag_graph_study.py (R&D #93): a planted lag-2 link is found and
confirmed; the market and the follower's own momentum do not create edges; the placebo shift
breaks the link; Benjamini-Hochberg, the verdict and the cross-sector label."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import leadlag_graph_study as lg  # noqa: E402

T = 3000


def world(seed=1, link=0.35, ar=0.0):
    """5 stocks + market; stock 1 follows stock 0 two days later; all share the market."""
    r = np.random.default_rng(seed)
    m = r.normal(0, 0.01, T)
    X = r.normal(0, 0.01, (T, 5))
    if ar:
        for t in range(1, T):
            X[t, 3] += ar * X[t - 1, 3]                        # stock 3 has its own momentum
    X[2:, 1] += link * X[:-2, 0]
    R = X + 1.2 * m[:, None]
    train = np.arange(T) < 2000
    return R, m, train, ~train


class TestPipeline(unittest.TestCase):
    def test_planted_link_found_at_its_lag(self):
        R, m, train, test = world()
        E = lg.residualize(R, m, train)
        W = lg.whiten(E, train)
        edges = lg.find_edges(E, W, train, test)
        self.assertIn((2, 0, 1), {(lag, i, j) for lag, i, j, *_ in edges})
        self.assertTrue(all((i, j) == (0, 1) for _lag, i, j, *_ in edges))   # nothing else

    def test_market_alone_makes_no_edges(self):
        R, m, train, test = world(link=0.0)
        E = lg.residualize(R, m, train)
        self.assertEqual(lg.find_edges(E, lg.whiten(E, train), train, test), [])

    def test_own_momentum_is_removed(self):
        # stock 4 moves WITH stock 3 the same day; stock 3 has its own momentum. Without whitening,
        # 4 today "predicts" 3 tomorrow only through 3's momentum: a fake edge.
        R, m, train, test = world(link=0.0, ar=0.4)
        R[:, 4] += 0.9 * (R[:, 3] - 1.2 * m)
        E = lg.residualize(R, m, train)
        fake = lg.find_edges(E, E, train, test)
        self.assertIn((1, 4, 3), {(lag, i, j) for lag, i, j, *_ in fake})
        real = lg.find_edges(E, lg.whiten(E, train), train, test)
        self.assertNotIn((1, 4, 3), {(lag, i, j) for lag, i, j, *_ in real})      # fake edge gone
        # (3 -> 4 can stay: stock 4 holds 0.9 x stock 3, which includes 3's own lag: a real link)

    def test_market_momentum_is_not_an_edge(self):
        # the market itself carries over day to day: every stock "predicts" every other unless
        # the market part is removed first
        r = np.random.default_rng(4)
        m = np.zeros(T)
        shocks = r.normal(0, 0.01, T)
        for t in range(1, T):
            m[t] = 0.5 * m[t - 1] + shocks[t]
        R = r.normal(0, 0.004, (T, 5)) + 1.5 * m[:, None]
        train = np.arange(T) < 2000
        E = lg.residualize(R, m, train)
        self.assertEqual(lg.find_edges(E, lg.whiten(E, train), train, ~train), [])

    def test_a_link_that_flips_out_of_sample_is_not_confirmed(self):
        R, m, train, test = world(link=0.0)
        X = R - 1.2 * m[:, None]
        sign = np.where(np.arange(T) < 2000, 1.0, -1.0)
        R[2:, 1] += 0.35 * sign[2:] * X[:-2, 0]
        E = lg.residualize(R, m, train)
        edges = lg.find_edges(E, lg.whiten(E, train), train, test)
        self.assertNotIn((2, 0, 1), {(lag, i, j) for lag, i, j, *_ in edges})

    def test_placebo_breaks_the_link(self):
        R, m, train, test = world()
        E = lg.residualize(R, m, train)
        W = lg.whiten(E, train)
        S = lg.shift_leaders(E, [train, test], 300)
        self.assertEqual(lg.find_edges(S, W, train, test), [])

    def test_lag_uses_both_days_in_the_period(self):
        A = np.arange(10, dtype=float)[:, None]
        rows = np.array([True] * 5 + [False] * 5)
        _rho, n = lg.lagged_corr(np.hstack([A, A]), np.hstack([A, A]), 2, rows)
        self.assertEqual(n[0, 1], 3)                                           # t = 0,1,2 (t+2 <= 4)

    def test_missing_values_are_pairwise(self):
        A = np.random.default_rng(0).normal(size=(50, 2))
        A[10:20, 0] = np.nan
        _rho, n = lg.lagged_corr(A, A, 1, np.ones(50, bool))
        self.assertEqual(n[0, 1], 39)


class TestRules(unittest.TestCase):
    def test_bh(self):
        p = np.array([0.001, 0.02, 0.04, 0.5, np.nan])
        self.assertEqual(lg.bh_mask(p, 0.05).tolist(), [True, True, False, False, False])

    def test_verdict(self):
        self.assertEqual(lg.verdict(100, [10, 20, 30]), "GRAPH_REAL")
        self.assertEqual(lg.verdict(60, [10, 20, 30]), "INCONCLUSIVE")              # < 3x the max
        self.assertEqual(lg.verdict(30, [10, 20, 30]), "NO_GRAPH")                  # <= 1.5x the mean
        self.assertEqual(lg.verdict(0, [0, 0, 0]), "NO_GRAPH")
        self.assertEqual(lg.verdict(10, [0, 0, 0]), "INCONCLUSIVE")                 # < 20 edges
        self.assertEqual(lg.verdict(25, [0, 0, 0]), "GRAPH_REAL")

    def test_cross_sector(self):
        self.assertTrue(lg.cross_sector({"sic_div": "73"}, {"sic_div": "42"}))
        self.assertFalse(lg.cross_sector({"sic_div": "73", "industry": "x"}, {"sic_div": "73", "industry": "y"}))
        self.assertTrue(lg.cross_sector({"industry": "Software"}, {"industry": "Trucking"}))
        self.assertIsNone(lg.cross_sector({}, {"industry": "Trucking"}))

    def test_returns_need_consecutive_days(self):
        syms, R = lg.returns_matrix({"X": (["d1", "d2", "d4"], [100.0, 110.0, 121.0])}, ["d1", "d2", "d3", "d4"])
        self.assertAlmostEqual(R[1, 0], np.log(1.1))
        self.assertTrue(np.isnan(R[3, 0]))                                     # d3 missing: no d2->d4 jump


if __name__ == "__main__":
    unittest.main()
