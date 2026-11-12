"""Tests for scripts/backtesting/gann_time_cycle_study.py (R&D #87): the one-year and five-year
past windows (no overlap with the forward window), the per-date IC, the 10% zigzag, the
seasonal-date binomial test, Benjamini-Hochberg and the verdict."""
import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import gann_time_cycle_study as gs  # noqa: E402


def bars(closes, start=datetime.date(2015, 1, 1), step=1):
    days = [(start + datetime.timedelta(days=i * step)).isoformat() for i in range(len(closes))]
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": list(closes)}


class TestWindows(unittest.TestCase):
    def test_fwd(self):
        b = bars([100.0] * 20 + [110.0] + [100.0] * 5)
        self.assertAlmostEqual(gs.fwd(b, 0), 0.10)
        self.assertIsNone(gs.fwd(b, 10))                      # 20 bars ahead missing

    def test_one_year_back_window(self):
        closes = [100.0] * 800
        closes[20] = 120.0                                     # 2015-01-21: +20% over 2015-01-01's window
        b = bars(closes)
        i = b["idx"]["2016-01-01"]
        past = gs.past_windows(b, "2016-01-01", i, 1)
        self.assertEqual(len(past), 1)
        self.assertAlmostEqual(past[0], 0.20)

    def test_five_years_needs_all_and_on_or_before(self):
        b = bars([100.0] * 2200, start=datetime.date(2012, 1, 1))
        i = b["idx"]["2017-06-30"]
        self.assertEqual(len(gs.past_windows(b, "2017-06-30", i, 5)), 5)
        self.assertEqual(len(gs.past_windows(b, "2014-06-30", b["idx"]["2014-06-30"], 5)), 2)   # stops at the gap
        self.assertEqual(gs.years_back("2016-02-29", 1), "2015-02-28")

    def test_past_window_may_not_overlap_today(self):
        b = bars([100.0] * 60, step=30)                                       # monthly bars: a year back is 12 bars
        day = b["days"][30]
        self.assertEqual(gs.past_windows(b, day, 30, 1), [])

    def test_rows_avg5_none_without_five_years(self):
        b = bars([100.0 + (i % 7) for i in range(900)])
        rows = gs.symbol_rows("X", b, ["2016-06-30"])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["avg5"])
        self.assertIsNotNone(rows[0]["lag1"])


class TestIC(unittest.TestCase):
    def test_ic_and_min_names(self):
        n = gs.MIN_NAMES
        df = pd.DataFrame({"day": ["d"] * n, "lag1": list(range(n)), "f20": [-x for x in range(n)]})
        self.assertAlmostEqual(gs.ic_series(df, "lag1")["d"], -1.0)
        self.assertTrue(gs.ic_series(df.iloc[:n - 1], "lag1").empty)


class TestZigzag(unittest.TestCase):
    def test_turns(self):
        closes = [100, 105, 112, 108, 99, 95, 90, 96, 104, 101]
        b = bars(closes)
        got = gs.zigzag_turns(b["days"], closes, 0.10)
        self.assertEqual(got, [(b["days"][0], "low"), (b["days"][2], "high"), (b["days"][6], "low")])
        # 100 -> 112 (+12%) confirms the low at bar 0; 112 -> 99 (-11.6%) the high; 90 -> 104 the low

    def test_pullback_under_threshold_is_not_a_turn(self):
        closes = [100, 112, 105, 110, 106]                                    # -6% after the low is confirmed
        b = bars(closes)
        self.assertEqual(gs.zigzag_turns(b["days"], closes, 0.10), [(b["days"][0], "low")])

    def test_small_moves_no_turn(self):
        closes = [100, 104, 97, 103, 98]
        b = bars(closes)
        self.assertEqual(gs.zigzag_turns(b["days"], closes, 0.10), [])

    def test_first_move_down(self):
        closes = [100, 95, 89, 92, 99]
        b = bars(closes)
        self.assertEqual(gs.zigzag_turns(b["days"], closes, 0.10),
                         [(b["days"][0], "high"), (b["days"][2], "low")])


class TestShift(unittest.TestCase):
    def test_turns_on_season_dates_are_significant(self):
        turns = [f"{y}-{m:02d}-{d:02d}" for y in range(2001, 2011) for m, d in gs.SEASON_DATES]
        r = gs.season_test(turns)
        self.assertEqual((r["turns"], r["near"], r["share"]), (40, 40, 1.0))
        self.assertLess(r["p_binom"], 1e-20)
        far = [f"{y}-05-06" for y in range(2001, 2041)]                       # 46 days from Mar 21 / Jun 21
        self.assertEqual(gs.season_test(far)["p_binom"], 1.0)

    def test_binom_tail(self):
        self.assertAlmostEqual(gs.binom_tail(2, 2, 0.5), 0.25)
        self.assertAlmostEqual(gs.binom_tail(1, 2, 0.5), 0.75)
        self.assertAlmostEqual(gs.binom_tail(0, 3, 0.2), 1.0)

    def test_window_edges(self):
        base = [datetime.date(2001, 3, 21).timetuple().tm_yday]
        self.assertEqual(gs.near_count(["2010-03-28", "2010-03-29"], base, 7), 1)   # +7 in, +8 out
        self.assertEqual(gs.near_count(["2010-12-31"], [3], 7), 1)                    # wraps the year


class TestEvaluate(unittest.TestCase):
    def _h1(self, tr_ic, tr_t, te_t):
        return {"train": {"n": 150, "mean_ic": tr_ic, "t_hac": tr_t}, "test": {"n": 150, "mean_ic": 0.0, "t_hac": te_t}}

    def test_confirm_and_verdict(self):
        ev = gs.evaluate({"H1a": self._h1(0.03, 4.0, 2.5), "H1b": self._h1(0.03, 4.0, 1.0)}, {"p_binom": 0.5})
        self.assertEqual((ev["H1a"], ev["H1b"], ev["H2"]), ("CONFIRMED", "NOT_CONFIRMED", "NOT_SUPPORTED"))
        self.assertEqual(ev["verdict"], "GANN_TIMING_SUPPORTED")

    def test_sign_flip_and_h2_alone(self):
        ev = gs.evaluate({"H1a": self._h1(0.03, 4.0, -2.5), "H1b": self._h1(0.0, 0.1, 0.0)}, {"p_binom": 0.001})
        self.assertEqual(ev["H1a"], "NOT_CONFIRMED")
        self.assertEqual(ev["H2"], "SUPPORTED_WEAK")
        self.assertEqual(ev["verdict"], "NOT_SUPPORTED")                               # H2 alone never decides

    def test_bh(self):
        self.assertEqual(gs.benjamini_hochberg({"a": 0.04, "b": 0.07, "c": 0.5}), set())        # .04 > .033, .07 > .067
        self.assertEqual(gs.benjamini_hochberg({"a": 0.06, "b": 0.065, "c": 0.09}), {"a", "b", "c"})
        self.assertEqual(gs.benjamini_hochberg({"a": 0.10}), {"a"})                              # p == cut passes


if __name__ == "__main__":
    unittest.main()
