"""Tests for scripts/backtesting/factor_portfolio.py (R&D #64, price-factor portfolio).

Pins the factor maths (no look-ahead), the cross-sectional ranks, the monthly rebalance
(top-N equal weight at the next open, costs), the SPY trend filter and the pre-registered verdict.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import factor_portfolio as fp


def _bar_series(closes, days, opens=None):
    n = len(closes)
    return {"idx": {d: i for i, d in enumerate(days)}, "o": list(opens or closes), "h": list(closes),
            "l": list(closes), "c": list(closes), "atr": [1.0] * n, "sma50": [None] * n}


def _days(n, start_month=1):
    out, m, d = [], start_month, 1
    while len(out) < n:
        out.append(f"2024-{m:02d}-{d:02d}")
        d += 1
        if d > 20:
            d, m = 1, m + 1
    return out


class TestFactors(unittest.TestCase):
    def test_values_need_enough_history(self):
        days = _days(300)
        b = _bar_series([100.0] * 300, days)
        self.assertIsNone(fp.factor_values(b, fp.MIN_HISTORY - 2))
        self.assertIsNotNone(fp.factor_values(b, fp.MIN_HISTORY - 1))

    def test_momentum_high_and_lowvol(self):
        days = _days(300)
        closes = list(np.linspace(50, 100, 300))
        b = _bar_series(closes, days)
        i = 299
        f = fp.factor_values(b, i)
        self.assertAlmostEqual(f["mom"], closes[i - 21] / closes[i - 252] - 1)
        self.assertAlmostEqual(f["high"], 1.0)                           # rising: at its high
        flat = fp.factor_values(_bar_series([100.0] * 300, days), i)
        self.assertAlmostEqual(flat["lowvol"], 0.0)

    def test_no_look_ahead(self):
        days = _days(300)
        closes = [100.0 + (k % 7) for k in range(300)]
        b = _bar_series(closes, days)
        before = fp.factor_values(b, 260)
        b["c"][261:] = [1000.0] * 39                                     # change only the future (up)
        self.assertEqual(fp.factor_values(b, 260), before)

    def test_pct_ranks_with_ties(self):
        self.assertEqual(fp.pct_ranks({"a": 1, "b": 2, "c": 2, "d": 3}),
                         {"a": 0.25, "b": 0.625, "c": 0.625, "d": 1.0})
        self.assertEqual(fp.pct_ranks({}), {})

    def test_rank_mix_averages_the_three_ranks(self):
        days = _days(300)
        up = _bar_series(list(np.linspace(50, 100, 300)), days)          # best momentum and high
        down = _bar_series(list(np.linspace(100, 50, 300)), days)
        bars = {"UP": up, "DN": down}
        self.assertEqual([s for _sc, s in fp.rank_symbols(bars, ["UP", "DN"], days[-1], "mom")], ["UP", "DN"])
        self.assertEqual(fp.rank_symbols(bars, ["UP", "DN"], days[-1], "mix")[0][1], "UP")


class TestPortfolio(unittest.TestCase):
    def test_month_ends(self):
        cal = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-28", "2024-03-01"]
        self.assertEqual(fp.month_ends(cal), {"2024-01-31", "2024-02-28"})

    def _universe(self):
        days = _days(300)
        bars = {f"S{k}": _bar_series(list(np.linspace(50, 50 + 10 * (k + 1), 300)), days) for k in range(4)}
        bars["SPY"] = _bar_series(list(np.linspace(100, 200, 300)), days)
        return bars, days

    def test_buys_top_n_equal_weight_at_the_next_open(self):
        bars, days = self._universe()
        cal = days[250:]
        first_end = sorted(fp.month_ends(cal))[0]
        r = fp.simulate_factor(bars, cal, ["S0", "S1", "S2", "S3"], "mom", top_n=2, cost_bps=0.0)
        k = cal.index(first_end)
        self.assertEqual(r["curve"][k][2], 0.0)                          # nothing bought on the signal day
        self.assertGreater(r["curve"][k + 1][2], 0.0)                    # bought at the next open
        self.assertEqual(sorted(r["open_positions"]), ["S2", "S3"])      # strongest momentum

    def test_costs_reduce_equity(self):
        bars, days = self._universe()
        cal = days[250:]
        free = fp.simulate_factor(bars, cal, ["S0", "S1", "S2", "S3"], "mom", top_n=2, cost_bps=0.0)
        paid = fp.simulate_factor(bars, cal, ["S0", "S1", "S2", "S3"], "mom", top_n=2, cost_bps=10.0)
        self.assertLess(paid["curve"][-1][1], free["curve"][-1][1])
        self.assertGreater(paid["traded_usd"], 0.0)
        # The first buy alone already costs: on the first holding day (no sells yet) equity is lower.
        k = cal.index(sorted(fp.month_ends(cal))[0]) + 1
        self.assertLess(paid["curve"][k][1], free["curve"][k][1])

    def test_trend_filter_goes_to_cash_below_the_200_day_average(self):
        bars, days = self._universe()
        bars["SPY"] = _bar_series(list(np.linspace(200, 100, 300)), days)  # falling: below its SMA
        cal = days[250:]
        r = fp.simulate_factor(bars, cal, ["S0", "S1", "S2", "S3"], "mix", trend=True, top_n=2, cost_bps=0.0)
        self.assertEqual(r["open_positions"], [])
        self.assertTrue(all(inv == 0.0 for _d, _e, inv in r["curve"]))
        self.assertTrue(fp.spy_above_trend(_bar_series(list(np.linspace(100, 200, 300)), days), days[-1]))


class TestVerdict(unittest.TestCase):
    @staticmethod
    def _summary(train_medians, train_win, test_win):
        mk = lambda med, w: {"sharpe": {"median": med}, "win_rate": w}
        train = {v: mk(m, 0.0) for v, m in zip(fp.VARIANTS, train_medians, strict=True)}
        test = {v: mk(0.0, 0.0) for v in fp.VARIANTS}
        best = max(train, key=lambda v: train[v]["sharpe"]["median"])
        train[best]["win_rate"], test[best]["win_rate"] = train_win, test_win
        return {"periods": {"train": train, "test": test}}

    def test_pass_needs_both_halves(self):
        v = fp.verdict(self._summary([0.1, 0.2, 0.9, 0.3, 0.4], 0.8, 0.85))
        self.assertEqual((v["picked"], v["passed"]), ("fac_lowvol", True))
        self.assertFalse(fp.verdict(self._summary([0.1, 0.2, 0.9, 0.3, 0.4], 0.8, 0.79))["passed"])
        self.assertFalse(fp.verdict(self._summary([0.1, 0.2, 0.9, 0.3, 0.4], 0.5, 0.95))["passed"])


if __name__ == "__main__":
    unittest.main()
