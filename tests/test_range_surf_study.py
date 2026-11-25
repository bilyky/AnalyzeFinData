"""R&D #122 range surf: the range detector, the trade loop, the yardsticks and the verdict."""
import datetime
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import portfolio_backtest as pb  # noqa: E402
import range_surf_study as rs  # noqa: E402


def _days(n, start="2010-01-04"):
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def _bars(closes, spread=0.5, atr=2.0, opens=None, days=None):
    days = days or _days(len(closes))
    c = [float(x) for x in closes]
    return {"idx": {d: i for i, d in enumerate(days)}, "c": c, "o": list(opens or c),
            "h": [x + spread for x in c], "l": [x - spread for x in c], "atr": [atr] * len(c)}


def _triangle(n, lo=90.0, hi=110.0, period=30, drift=0.0):
    out = []
    for i in range(n):
        ph = (i % period) / period
        x = lo + (hi - lo) * (2 * ph if ph < 0.5 else 2 * (1 - ph))
        out.append(x + drift * i)
    return out


class TestRange(unittest.TestCase):
    def test_trips(self):
        # low, high, low = 2 trips; a bar touching both zones is ignored; mid bars don't count
        highs = [91, 109, 100, 91, 120]
        lows = [89, 105, 99, 89, 80]
        self.assertEqual(rs.count_trips(highs, lows, 110.0, 90.0), 2)
        self.assertEqual(rs.count_trips([100, 100], [99, 99], 110.0, 90.0), 0)

    def test_fit_move(self):
        lin = np.arange(200, dtype=float)
        m = rs._rolling_fit_move(lin, 120)
        self.assertTrue(math.isnan(m[118]))
        self.assertAlmostEqual(m[119], 119.0)
        self.assertAlmostEqual(m[199], 119.0)
        self.assertAlmostEqual(rs._rolling_fit_move(np.full(150, 5.0), 120)[149], 0.0)

    def test_signals_on_a_wide_repeating_range(self):
        b = _bars(_triangle(400))
        sig = rs.range_signals(b)
        self.assertTrue(sig)
        i = min(sig)
        self.assertGreaterEqual(i, rs.MIN_HISTORY - 1)
        s = sig[i]
        self.assertLessEqual(s["pos"], rs.ZONE)
        # channel 89.5..110.5 -> H 21, target = U - H/4, stop = L - 0.5 ATR
        self.assertAlmostEqual(s["target"], 110.5 - 0.25 * 21.0)
        self.assertAlmostEqual(s["stop"], 89.5 - 0.5 * 2.0)
        self.assertAlmostEqual(s["ratio"], 21.0 / 2.0)

    def test_trend_is_not_sideways(self):
        # +0.1 a day: the 120-session fitted move is ~12, above half the channel height
        self.assertEqual(rs.range_signals(_bars(_triangle(400, drift=0.1))), {})

    def test_step_down_is_not_sideways_until_the_fit_window_is_flat(self):
        # 240 bars ranging 150..170, then 90..110: the 60-bar channel is clean from bar 300, but the
        # 120-bar fit still sees the step until bar 359
        c = [x + 60.0 for x in _triangle(240)] + _triangle(160)
        sig = rs.range_signals(_bars(c))
        self.assertFalse([i for i in sig if 300 <= i < 345])
        self.assertTrue([i for i in sig if i >= 360])

    def test_narrow_range_fails_k4_not_k3(self):
        b = _bars(_triangle(400), atr=6.0)                  # H 21 / ATR 6 = 3.5
        sig = rs.range_signals(b)
        self.assertTrue(sig)
        self.assertTrue(all(3.0 <= s["ratio"] < 4.0 for s in sig.values()))

    def test_breakdown_today_is_not_an_entry(self):
        c = _triangle(400)
        b = _bars(c)
        sig = rs.range_signals(b)
        i = min(sig)
        c2 = list(c)
        c2[i] = 80.0                                       # closes under the channel
        self.assertNotIn(i, rs.range_signals(_bars(c2)))


class TestSimulate(unittest.TestCase):
    def setUp(self):
        self.days = _days(10)

    def _run(self, closes, opens, sig, k=4.0, risk_pct=0.01):
        bars = {"AAA": _bars(closes, opens=opens, days=self.days)}
        by_day = {self.days[0]: [("AAA", sig)]}
        return rs.simulate_surf(bars, self.days, ["AAA"], by_day, k, risk_pct=risk_pct)

    def test_target_exit_with_costs_and_risk_cap(self):
        sig = {"ratio": 5.0, "pos": 0.1, "target": 110.0, "stop": 95.0}
        closes = [100, 101, 111, 111, 111, 111, 111, 111, 111, 111]
        opens = [100, 100, 105, 112, 111, 111, 111, 111, 111, 111]
        r = self._run(closes, opens, sig)
        self.assertEqual(len(r["trades"]), 1)
        t = r["trades"][0]
        self.assertEqual(t["reason"], "target")
        # buy day 1 at 100: min(10% of 100k, 1% of 100k / 5 * 100 = 20k) = 10k; sell day 3 open 112
        self.assertAlmostEqual(t["ret_pct"], (112 * 0.999 / (100 * 1.001) - 1) * 100)
        self.assertAlmostEqual(r["curve"][1][2], 100.0 * 101)   # 100 shares marked at 101

    def test_risk_cap_trims_a_wide_stop(self):
        sig = {"ratio": 5.0, "pos": 0.1, "target": 200.0, "stop": 50.0}
        r = self._run([100] * 10, [100] * 10, sig)
        # 1% of 100k / (100 - 50) * 100 = 2k < 10k
        self.assertAlmostEqual(r["curve"][1][2], 2000.0, places=6)

    def test_stop_and_gap_through_stop(self):
        sig = {"ratio": 5.0, "pos": 0.1, "target": 110.0, "stop": 95.0}
        closes = [100, 99, 94, 94, 94, 94, 94, 94, 94, 94]
        opens = [100, 100, 98, 90, 94, 94, 94, 94, 94, 94]
        t = self._run(closes, opens, sig)["trades"][0]
        self.assertEqual(t["reason"], "stop")
        self.assertAlmostEqual(t["loss_pct_equity"], 100 * (90 * 0.999 - 100 * 1.001) / 100_000 * 100)
        # an open already at the stop: no buy
        r = self._run([100] * 10, [100, 95] + [100] * 8, sig)
        self.assertEqual(r["trades"], [])
        self.assertEqual(r["curve"][-1][2], 0.0)

    def test_time_exit_and_k_filter(self):
        sig = {"ratio": 3.5, "pos": 0.1, "target": 200.0, "stop": 90.0}
        old = rs.MAX_HOLD
        rs.MAX_HOLD = 3
        try:
            t = self._run([100] * 10, [100] * 10, sig, k=3.0)["trades"][0]
        finally:
            rs.MAX_HOLD = old
        self.assertEqual((t["reason"], t["held"]), ("time", 3))
        self.assertEqual(self._run([100] * 10, [100] * 10, sig, k=4.0)["trades"], [])

    def test_order_fills_lowest_in_channel_first(self):
        days = self.days
        bars = {s: _bars([100] * 10, days=days) for s in ("AAA", "BBB")}
        sig = lambda p: {"ratio": 5.0, "pos": p, "target": 200.0, "stop": 99.0}   # noqa: E731
        old = rs.SLOTS
        rs.SLOTS = 1
        try:
            r = rs.simulate_surf(bars, days, ["AAA", "BBB"], {days[0]: [("AAA", sig(0.2)), ("BBB", sig(0.1))]}, 4.0)
        finally:
            rs.SLOTS = old
        self.assertEqual(r["open_positions"], ["BBB"])


class TestYardsticks(unittest.TestCase):
    def test_ew_matches_buy_and_hold(self):
        days = _days(5)
        bars = {"A": _bars([10, 11, 12, 11, 13], opens=[9.5] * 5, days=days),
                "B": _bars([20, 19, 21, 22, 20], opens=[21.0] * 5, days=days)}
        ref = [e for _d, e, _i in pb.buy_and_hold(bars, days, ["A", "B"], 10.0)["curve"]]
        cm = rs.close_matrix(bars, ["A", "B"], days)
        got = rs.ew_hold(cm, np.array([9.5, 21.0]), 10.0)
        np.testing.assert_allclose(got, ref)

    def test_close_matrix_forward_fills(self):
        days = _days(3)
        b = _bars([10, 12], days=[days[0], days[2]])
        cm = rs.close_matrix({"A": b}, ["A"], days)
        self.assertEqual(list(cm[0]), [10.0, 10.0, 12.0])

    def test_spy_matched_scales_by_exposure(self):
        curve = [("d0", 100.0, 50.0), ("d1", 101.0, 0.0), ("d2", 101.0, 0.0)]
        out = rs.spy_matched(curve, {"d0": 100.0, "d1": 110.0, "d2": 121.0})
        self.assertAlmostEqual(out[1][1], 105.0)           # half exposed to +10%
        self.assertAlmostEqual(out[2][1], 105.0)           # no exposure on d1's close


def _rates(**kw):
    base = {"n": 50, "dd_not_deeper_than_spy": 0.9, "cagr_above_spy": 0.9, "cagr_above_ew": 0.9,
            "calmar_above_spy": 0.9, "cagr_above_spy_match": 0.9, "tail_loss_p95_median": 0.8}
    return {"train": {**base, **kw}, "test": {**base, **kw}}


class TestVerdict(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(rs.verdict(_rates())["label"], "BETTER_THAN_SPY")
        self.assertEqual(rs.verdict(_rates(cagr_above_ew=0.5))["label"], "SAFER_LOWER_RETURN")
        self.assertEqual(rs.verdict(_rates(cagr_above_spy=0.5, calmar_above_spy=0.5))["label"], "INCONCLUSIVE")

    def test_risk_first(self):
        # a deeper drop than SPY fails whatever the return
        self.assertEqual(rs.verdict(_rates(dd_not_deeper_than_spy=0.79))["label"], "FAIL_RISK")
        self.assertEqual(rs.verdict(_rates(tail_loss_p95_median=1.2))["label"], "FAIL_RISK")
        self.assertEqual(rs.verdict(_rates(tail_loss_p95_median=None))["label"], "FAIL_RISK")

    def test_one_half_is_not_enough(self):
        w = _rates()
        w["train"]["dd_not_deeper_than_spy"] = 0.5
        self.assertEqual(rs.verdict(w)["label"], "FAIL_RISK")
        w = _rates()
        w["test"]["cagr_above_spy"] = 0.5
        w["test"]["calmar_above_spy"] = 0.5
        self.assertEqual(rs.verdict(w)["label"], "INCONCLUSIVE")

    def test_cash_use(self):
        self.assertEqual(rs.verdict(_rates())["cash_use"], "CASH_USE_PASS")
        self.assertEqual(rs.verdict(_rates(cagr_above_spy_match=0.1))["cash_use"], "CASH_USE_FAIL")
        self.assertEqual(rs.verdict(_rates(cagr_above_spy_match=0.5))["cash_use"], "CASH_USE_INCONCLUSIVE")

    def test_win_rates(self):
        good = {"surf_k4": {"max_drawdown_pct": -10.0, "cagr_pct": 8.0, "tail_loss_p95_pct": 0.9},
                "spy": {"max_drawdown_pct": -20.0, "cagr_pct": 6.0}, "ew": {"cagr_pct": 9.0},
                "spy_match": {"cagr_pct": 3.0}}
        bad = {**good, "surf_k4": {"max_drawdown_pct": -30.0, "cagr_pct": 2.0, "tail_loss_p95_pct": 1.1}}
        w = rs.win_rates([{"train": good, "test": good}, {"train": good, "test": good}, {"train": bad, "test": good}])
        self.assertEqual(w["train"]["dd_not_deeper_than_spy"], 0.667)
        self.assertEqual(w["train"]["cagr_above_spy"], 0.667)
        self.assertEqual(w["train"]["cagr_above_ew"], 0.0)
        self.assertEqual(w["test"]["calmar_above_spy"], 1.0)
        self.assertEqual(w["train"]["tail_loss_p95_median"], 0.9)


if __name__ == "__main__":
    unittest.main()
