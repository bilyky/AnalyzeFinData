"""Tail-risk study (R&D #115): tail fields, grouping, the per-date statistic and the verdict."""
import datetime
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import rule_walkforward as wf
import tail_risk_study as trs


def _weekdays(n, start=datetime.date(2020, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def _ts(closes, dates=None, opens=None, provisional=()):
    dates = dates or _weekdays(len(closes))
    opens = opens or closes
    ts = {}
    for k, (d, c, o) in enumerate(zip(dates, closes, opens)):
        bar = {"1. open": str(o), "2. high": str(max(c, o)), "3. low": str(min(c, o)), "4. close": str(c),
               "5. volume": "1000"}
        if k in provisional:
            bar.update({"5. volume": "0", "provisional": True})
        ts[d] = bar
    return ts, dates


class TestTailFields(unittest.TestCase):
    def test_history_count_excludes_reversed_and_gap_drops(self):
        n = wf.HIST_SESSIONS + 30
        closes = [100.0] * n
        # drop A at 100: -12%, stays down -> counts
        for k in range(100, n):
            closes[k] = 88.0
        # drop B at 200: -15% then +20% next session -> bad bar, excluded
        closes[200] = 88.0 * 0.85
        # drop C at 300: -11%, next session +5% only -> counts
        for k in range(300, n):
            closes[k] = 88.0 * 0.89
        closes[301:] = [88.0 * 0.89 * 1.05] * (n - 301)
        # drop D at 400: -10.5%, next flat -> counts
        for k in range(400, n):
            closes[k] = closes[399] * 0.895
        ts, dates = _ts(closes)
        ser = wf.tail_series(ts)
        _, h = wf.tail_fields(dates[wf.HIST_SESSIONS + 5], ser)
        self.assertEqual(h, 3.0)              # A, C, D; not the reversed B
        # too little history -> NaN
        _, h_short = wf.tail_fields(dates[wf.HIST_SESSIONS], ser)
        self.assertTrue(math.isnan(h_short))
        _, h_ok = wf.tail_fields(dates[wf.HIST_SESSIONS + 1], ser)
        self.assertFalse(math.isnan(h_ok))

    def test_window_is_strictly_before_the_entry_day(self):
        n = wf.HIST_SESSIONS + 20
        closes = [100.0] * n
        j = wf.HIST_SESSIONS + 10
        for k in range(j, n):
            closes[k] = 80.0                   # -20% ON the entry day: not history
        ts, dates = _ts(closes)
        ser = wf.tail_series(ts)
        self.assertEqual(wf.tail_fields(dates[j], ser)[1], 0.0)
        self.assertEqual(wf.tail_fields(dates[j + 1], ser)[1], 1.0)
        # a drop exactly 756 sessions back is inside the window; 757 back is outside
        closes2 = [100.0] * n
        for k in range(5, n):
            closes2[k] = 85.0
        ts2, dates2 = _ts(closes2)
        ser2 = wf.tail_series(ts2)
        self.assertEqual(wf.tail_fields(dates2[5 + wf.HIST_SESSIONS], ser2)[1], 1.0)
        self.assertEqual(wf.tail_fields(dates2[5 + wf.HIST_SESSIONS + 1], ser2)[1], 0.0)

    def test_drop_across_a_session_gap_is_not_counted(self):
        n = wf.HIST_SESSIONS + 20
        dates = _weekdays(n + 10)
        dates = dates[:50] + dates[60:]        # a two-week hole before bar 50
        closes = [100.0] * 50 + [80.0] * (len(dates) - 50)
        ts, _ = _ts(closes, dates=dates)
        ser = wf.tail_series(ts)
        self.assertEqual(wf.tail_fields(dates[wf.HIST_SESSIONS + 10], ser)[1], 0.0)

    def test_worst10_split_adjusted_and_placeholders_dropped(self):
        closes = [100.0] * 30
        opens = list(closes)
        closes[13:] = [50.0] * 17              # 2:1 split at bar 13 (open agrees) -> no drop
        opens[13:] = [50.0] * 17
        closes[15] = 40.0                      # real -20% vs 50 on the split scale
        ts, dates = _ts(closes, opens=opens, provisional=(16,))
        ts[dates[17]]["4. close"] = "50.0"
        ser = wf.tail_series(ts)
        worst, _ = wf.tail_fields(dates[10], ser)
        self.assertAlmostEqual(worst, -20.0, places=6)
        # a placeholder bar inside the window is dropped (not a session gap): the provisional
        # day 16 close of 50 is never read; with a fake low it would be the minimum
        ts[dates[16]]["4. close"] = "1.0"
        worst2, _ = wf.tail_fields(dates[10], wf.tail_series(ts))
        self.assertAlmostEqual(worst2, -20.0, places=6)
        # entry on a placeholder day -> NaN; too few bars after -> NaN
        self.assertTrue(math.isnan(wf.tail_fields(dates[16], ser)[0]))
        self.assertTrue(math.isnan(wf.tail_fields(dates[25], ser)[0]))

    def test_old_panel_rows_still_load(self):
        row = ("2024-01-02", "A", 3.0, 3.0, 3.0, True, True, 3.0, 10.0, False, False, 0.0, 1.0, 1.0, True)
        a = wf.to_arrays([row])
        self.assertNotIn("worst10", a)
        a2 = wf.to_arrays([row + (2, -5.0, 1.0)])
        self.assertEqual(float(a2["worst10"][0]), -5.0)
        self.assertEqual(int(a2["pgr"][0]), 2)


def _row(day, sym, s10=3.0, l60=6.0, rr=3.0, gain=10.0, pgr=4, worst=-1.0, drops=2.0):
    return (day, sym, s10, s10, l60, True, True, rr, gain, False, False, 0.0, 1.0, 1.0, True, pgr, worst, drops)


class TestGrouping(unittest.TestCase):
    def test_waiver_tags_and_population(self):
        rows = [_row("2024-01-02", "W0"),                                  # elite, rr ok, Bullish -> 0
                _row("2024-01-02", "RR", rr=0.1),                          # elite, rr waived -> 1
                _row("2024-01-02", "BE", pgr=2),                           # elite, Bearish -> 1
                _row("2024-01-02", "W2", rr=0.1, gain=1.0, pgr=1),         # both -> 2
                _row("2024-01-02", "BN", s10=2.0, l60=4.0, pgr=2),         # Bearish not elite -> rejected
                _row("2024-01-02", "NO", s10=1.0)]                         # fails S10 floor
        a = wf.to_arrays(rows)
        t = trs.tags(a)
        self.assertEqual(trs.population(a, t).tolist(), [True, True, True, True, False, False])
        self.assertEqual(trs.waiver_count(t).tolist()[:4], [0, 1, 1, 2])

    def test_history_groups(self):
        hi, lo = trs.history_groups([0.0, 1.0, 2.0, 3.0, 4.0, 9.0, float("nan")])
        self.assertEqual(hi.tolist(), [False, False, False, False, True, True, False])
        self.assertEqual(lo.tolist(), [True, True, False, False, False, False, False])


class TestStatistic(unittest.TestCase):
    def test_per_date_diff_differs_from_pooled_rates(self):
        # Pooled, HI drops more (5/12 vs 2/11); within each date HI drops LESS (Simpson).
        days = ["d1"] * 12 + ["d2"] * 11
        hi = np.array([True] * 10 + [False] * 2 + [True] * 2 + [False] * 9)
        big = np.array([1] * 5 + [0] * 5 + [1, 1] + [0, 0] + [0] * 9)
        lo = ~hi
        pooled = big[hi].mean() - big[lo].mean()
        self.assertGreater(pooled, 0)
        dates, xs = trs.per_date_diff(days, big, hi, lo)
        self.assertEqual(dates, ["d1", "d2"])
        self.assertEqual(xs, [0.5 - 1.0, 0.0 - 0.0])
        self.assertLess(np.mean(xs), 0)

    def test_dates_missing_a_group_are_skipped(self):
        dates, xs = trs.per_date_diff(["a", "a", "b"], [1, 0, 1], np.array([True, False, True]),
                                      np.array([False, True, False]))
        self.assertEqual(dates, ["a"])


def _stats(xs, start=datetime.date(2023, 1, 2)):
    dates = [(start + datetime.timedelta(days=k)).isoformat() for k in range(len(xs))]
    return trs.diff_stats(dates, list(xs))


class TestVerdict(unittest.TestCase):
    def test_pass(self):
        rng = np.random.default_rng(0)
        s = _stats(0.05 + rng.normal(0, 0.1, 300))
        self.assertGreaterEqual(s["t_hac"], 1.96)
        self.assertEqual(trs.verdict(s), "TAIL_WORSE")

    def test_plain_t_passes_but_hac_does_not(self):
        # long same-sign runs: plain t large, HAC t (lag 9) below 1.96
        blocks = np.repeat([0.3, -0.2] * 15, 10)       # 30 blocks of 10 equal values, mean 0.05
        s = _stats(blocks)
        self.assertGreater(s["mean_diff"], 0)
        self.assertGreaterEqual(s["t_plain"], 1.96)
        self.assertLess(s["t_hac"], 1.96)
        self.assertGreater(s["half1_mean"], 0)
        self.assertGreater(s["half2_mean"], 0)
        self.assertEqual(trs.verdict(s), "INCONCLUSIVE")

    def test_one_half_negative(self):
        rng = np.random.default_rng(2)
        xs = np.concatenate([0.30 + rng.normal(0, 0.05, 150), -0.01 + rng.normal(0, 0.05, 150)])
        s = _stats(xs)
        self.assertGreaterEqual(s["t_hac"], 1.96)
        self.assertLess(s["half2_mean"], 0)
        self.assertEqual(trs.verdict(s), "INCONCLUSIVE")

    def test_too_few_dates(self):
        rng = np.random.default_rng(3)
        s = _stats(0.2 + rng.normal(0, 0.05, 99))
        self.assertGreaterEqual(s["t_hac"], 1.96)
        self.assertEqual(trs.verdict(s), "INCONCLUSIVE")
        s100 = _stats(0.2 + rng.normal(0, 0.05, 100))
        self.assertEqual(trs.verdict(s100), "TAIL_WORSE")

    def test_negative_effect_is_not_tail_worse(self):
        rng = np.random.default_rng(4)
        s = _stats(-0.05 + rng.normal(0, 0.1, 300))
        self.assertEqual(trs.verdict(s), "INCONCLUSIVE")

    def test_hac_uses_lag_9(self):
        lags = []
        real = trs.sg._t_hac
        try:
            trs.sg._t_hac = lambda xs, lag: lags.append(lag) or real(xs, lag)
            _stats([0.1, -0.1] * 60)
        finally:
            trs.sg._t_hac = real
        self.assertEqual(lags, [9])


class TestRunEndToEnd(unittest.TestCase):
    def test_run_counts_and_groups(self):
        rows = []
        for k, day in enumerate(_weekdays(120, datetime.date(2024, 1, 1))):
            rows += [_row(day, "W2", rr=0.1, pgr=2, worst=-20.0 if k % 2 else -2.0, drops=5.0),
                     _row(day, "W0", worst=-16.0 if k % 5 == 0 else -1.0, drops=0.0),
                     _row(day, "BN", s10=2.0, l60=4.0, pgr=2, worst=-30.0),
                     _row(day, "NA", worst=float("nan"))]
        rep = trs.run(rows)
        self.assertEqual(rep["counts"]["dropped_bearish_not_elite"], 120)
        self.assertEqual(rep["counts"]["dropped_no_outcome"], 120)
        self.assertEqual(rep["groups"]["W2"]["obs"], 120)
        self.assertEqual(rep["groups"]["W0"]["obs"], 120)
        self.assertEqual(rep["test1_w2_minus_w0"]["n_dates"], 120)
        self.assertAlmostEqual(rep["test1_w2_minus_w0"]["mean_diff"], 0.5 - 0.2, places=4)
        self.assertEqual(rep["test2_high_minus_low"]["n_dates"], 120)


if __name__ == "__main__":
    unittest.main()
