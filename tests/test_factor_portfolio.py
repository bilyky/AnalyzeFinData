"""Tests for scripts/backtesting/factor_portfolio.py (R&D #64, price-factor portfolio).

Pins the factor maths (no look-ahead), the cross-sectional ranks, the monthly rebalance
(top-N equal weight at the next open, costs), the SPY trend filter and the pre-registered verdict.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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


class TestPointInTime(unittest.TestCase):
    """A symbol may only be ranked from the day it joined our universe."""

    def test_join_dates_are_first_scored_days(self):
        panel = {"A": {"2023-05-01": [1, 1], "2023-01-03": [1, 1]}, "B": {}, "C": {"2026-02-04": [0, 0]}}
        self.assertEqual(fp.join_dates(panel), {"A": "2023-01-03", "C": "2026-02-04"})

    def test_late_joiner_is_skipped_until_it_joins(self):
        days = _days(300)
        bars = {"UP": _bar_series(list(np.linspace(50, 100, 300)), days),
                "DN": _bar_series(list(np.linspace(100, 50, 300)), days)}
        join = {"UP": days[-1], "DN": days[0]}                          # UP joins on the last day
        before = fp.rank_symbols(bars, ["UP", "DN"], days[-2], "mom", join)
        self.assertEqual([s for _sc, s in before], ["DN"])
        after = fp.rank_symbols(bars, ["UP", "DN"], days[-1], "mom", join)
        self.assertEqual(after[0][1], "UP")
        unknown = fp.rank_symbols(bars, ["UP", "DN"], days[-1], "mom", {"DN": days[0]})
        self.assertEqual([s for _sc, s in unknown], ["DN"])               # never joined: never ranked

    def test_portfolio_never_holds_a_symbol_before_it_joins(self):
        days = _days(300)
        bars = {f"S{k}": _bar_series(list(np.linspace(50, 50 + 10 * (k + 1), 300)), days) for k in range(4)}
        bars["SPY"] = _bar_series(list(np.linspace(100, 200, 300)), days)
        join = {s: days[0] for s in bars}
        join["S3"] = "2099-01-01"                                       # strongest, but joins later
        r = fp.simulate_factor(bars, days[250:], ["S0", "S1", "S2", "S3"], "mom", top_n=2, cost_bps=0.0,
                               join=join)
        self.assertEqual(sorted(r["open_positions"]), ["S1", "S2"])


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


class TestRunPointInTime(unittest.TestCase):
    """run() end to end on a tiny fake cache: --pit must keep a late joiner out of both the factor
    portfolios and the equal-weight hold (#225 review: the wiring had no test)."""

    @staticmethod
    def _series(n, start, growth, wiggle):
        days, d = [], datetime.date(2024, 1, 2)
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += datetime.timedelta(days=1)
        px = [start * (1 + growth) ** k * (1 + wiggle * np.sin(k / 3.0)) for k in range(n)]
        ts = {day: {"1. open": p, "2. high": p * 1.01, "3. low": p * 0.99, "4. close": p,
                    "5. volume": 1000} for day, p in zip(days, px, strict=True)}
        return days, ts

    def _run(self, pit):
        days, spy = self._series(420, 100.0, 0.0005, 0.01)
        _d, old = self._series(420, 50.0, 0.0002, 0.02)          # joined from the start, weak
        _d, new = self._series(420, 20.0, 0.004, 0.03)           # joins late, strongest momentum
        series = {"SPY": spy, "OLD": old, "NEW": new}
        start = days[300]
        panel = {"SPY": {d: [0.0, 0.0] for d in days[300:]}, "OLD": {d: [0.0, 0.0] for d in days[300:]},
                 "NEW": {d: [0.0, 0.0] for d in days[380:]}}     # NEW enters our universe on day 380
        with tempfile.TemporaryDirectory() as tmp:
            ohlcv_dir, sym_dir = os.path.join(tmp, "ohlcv"), os.path.join(tmp, "sym")
            os.makedirs(ohlcv_dir)
            for s in series:
                open(os.path.join(ohlcv_dir, f"{s}_daily.json"), "w").close()
                os.makedirs(os.path.join(sym_dir, s))
            panel_path = os.path.join(tmp, "panel.json")
            with open(panel_path, "w", encoding="utf-8") as f:
                json.dump({"scores": panel}, f)
            with (mock.patch.object(fp._br, "OHLCV_DIR", ohlcv_dir),
                  mock.patch.object(fp._br, "SYM_DIR", sym_dir),
                  mock.patch.object(fp._br, "load_ohlcv", side_effect=lambda s: series[s]),
                  mock.patch.object(fp.pb, "PANEL_PATH", panel_path),
                  mock.patch.object(fp, "OUT_PATH", os.path.join(tmp, "out.json")),
                  mock.patch.object(fp.pb, "_out")):
                return fp.run(split=days[360], robust=1, min_year=2024, pit=pit), start

    def test_pit_changes_the_factor_portfolio_and_the_equal_weight_hold(self):
        pit, start = self._run(True)
        raw, _ = self._run(False)
        full = lambda p, v: p["vs_spy"]["periods"]["full"][v]["cagr_pct"]["median"]
        # With look-ahead, NEW (the strongest stock) is held from the start; point-in-time it isn't.
        self.assertNotEqual(full(pit, "fac_mom"), full(raw, "fac_mom"))
        self.assertNotEqual(full(pit, "ew_buy_hold"), full(raw, "ew_buy_hold"))
        self.assertLess(full(pit, "ew_buy_hold"), full(raw, "ew_buy_hold"))
        self.assertTrue(pit["pit"])
        self.assertEqual(pit["panel"]["n_scored"], 3)
        self.assertEqual(len(pit["panel"]["sha256"]), 64)


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
