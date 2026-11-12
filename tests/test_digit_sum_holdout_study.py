"""Tests for scripts/backtesting/digit_sum_holdout_study.py (R&D #72): the pre-cutoff rebuild
(no outcome on or after the cutoff), the live gate swap, the signed hit measure, the digit-adjusted
scores, the daily return difference and the pre-registered decision."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import digit_sum_holdout_study as ds  # noqa: E402
from aether import scoring  # noqa: E402


def series(n, start=datetime.date(2022, 9, 1), up=lambda i: i % 2 == 0, price=lambda i: 10.0 + (i % 9)):
    ts = {}
    for i in range(n):
        p = price(i)
        ts[(start + datetime.timedelta(days=i)).isoformat()] = {"1. open": str(p), "4. close": str(p + (0.5 if up(i) else -0.5))}
    return ts


class TestRebuild(unittest.TestCase):
    def test_no_outcome_on_or_after_cutoff(self):
        ts = series(200, price=lambda i: 10.0)                  # 2022-09-01 .. 2023-03-19, one bucket
        rows, _ = ds.bucket_rows("X", ts, scoring._price_digit_sum)
        n_open = sum(r["n"] for r in rows if r["type"] == "OPEN")
        n_close = sum(r["n"] for r in rows if r["type"] == "CLOSE")
        pre = sum(d < ds.CUTOFF for d in ts)
        self.assertEqual(pre, 122)
        self.assertEqual(n_open, pre)
        self.assertEqual(n_close, pre - 1)                      # the last pre-cutoff close's outcome is 2023

    def test_bucket_counts_and_z(self):
        ts = series(400, start=datetime.date(2020, 1, 1), up=lambda i: True, price=lambda i: 10.0)
        rows, base = ds.bucket_rows("X", ts, scoring._price_digit_sum)
        o = [r for r in rows if r["type"] == "OPEN"]
        self.assertEqual(len(o), 1)                             # one digit only ($10 -> 1)
        self.assertEqual((o[0]["digit"], o[0]["up_pct"], o[0]["z"]), (1, 1.0, 0.0))   # base 1.0: no z
        self.assertEqual(base, 1.0)

    def test_min_n(self):
        rows, _ = ds.bucket_rows("X", series(40, start=datetime.date(2020, 1, 1)), scoring._price_digit_sum)
        self.assertEqual(rows, [])


class TestLiveIndex(unittest.TestCase):
    def test_gate_and_restore(self):
        good = {"symbol": "X", "type": "CLOSE", "digit": 3, "z": 2.5, "temporal": "consistent",
                "has_flip": False, "is_sparse": False}
        weak = dict(good, digit=4, z=1.5)
        stale = dict(good, digit=5, temporal="stale")
        saved = scoring._digit_index
        with ds.live_index([good, weak, stale], []) as (idx, idx_full):
            self.assertEqual(idx, {("X", "CLOSE", 3): 2.5})
            self.assertEqual(idx_full, {})
            self.assertAlmostEqual(scoring.digit_sum_score("X", 12.0), round(2.5 / 3, 2))   # 1+2 = 3
            self.assertEqual(scoring.digit_sum_score("X", 13.0), 0.0)
        self.assertIs(scoring._digit_index, saved)


class TestHit(unittest.TestCase):
    def test_signed_excess(self):
        rows = [{"symbol": "X", "type": "CLOSE", "digit": 3, "z": -3.0, "temporal": "consistent",
                 "has_flip": False, "is_sparse": False}]
        per = {"X": {"base": 0.5, "oos": [("2023-01-03", 12.0, 0, 11.0), ("2023-01-04", 12.0, 0, 12.0),
                                          ("2023-01-05", 13.0, 1, 12.0)], "ins": []}}
        with ds.live_index(rows, []):
            r = ds.hit_test(per, "oos")
            d = ds.digit_by_day(per)
        self.assertEqual((r["signals"], r["days"]), (2, 2))     # $13 fires nothing
        self.assertAlmostEqual(r["excess_hit_pct"], 50.0)       # bearish call, down day: -(0 - 0.5)
        self.assertEqual(d["X"]["2023-01-03"], 0.0)             # prior close $11 -> digit 2: no signal
        self.assertEqual(d["X"]["2023-01-04"], -1.0)            # prior close $12 -> digit 3


class TestScoresAndReturns(unittest.TestCase):
    def test_with_digit_matches_live_adding(self):
        self.assertEqual(ds.with_digit(2.4, 1.0, 0.67), (3.1, 1.3))     # S10 + d, L60 + d/2, 0.1 steps
        self.assertEqual(ds.with_digit(9.8, -9.9, 1.0), (10.0, -9.4))   # clipped at +/-10

    def test_digit_panel(self):
        p = ds.digit_panel({"X": {"d1": [2.0, 1.0], "d2": [2.0, 1.0]}}, {"X": {"d1": -1.0}})
        self.assertEqual(p, {"X": {"d1": [1.0, 0.5], "d2": [2.0, 1.0]}})

    def test_return_diff(self):
        a = [("d0", 100.0, 0), ("d1", 101.0, 0), ("d2", 102.01, 0)]
        b = [("d0", 100.0, 0), ("d1", 100.0, 0), ("d2", 100.0, 0)]
        r = ds.return_diff_test(a, b, lag=0)
        self.assertEqual(r["days"], 2)
        self.assertAlmostEqual(r["mean_bps_per_day"], 100.0)


class TestDecide(unittest.TestCase):
    def test_rule(self):
        no = {"mean_ic": 0.001, "t_hac": 0.5}
        self.assertEqual(ds.decide({"mean_ic": 0.01, "t_hac": 2.0}, {"mean_bps_per_day": -1, "t_hac": -3}), "KEEP")
        self.assertEqual(ds.decide(no, {"mean_bps_per_day": 0.5, "t_hac": 2.1}), "KEEP")
        self.assertEqual(ds.decide(no, {"mean_bps_per_day": 0.5, "t_hac": 1.9}), "REMOVE")
        self.assertEqual(ds.decide({"mean_ic": -0.01, "t_hac": -2.5}, {"mean_bps_per_day": -0.5, "t_hac": -2.1}),
                         "REMOVE")
        self.assertEqual(ds.decide({"mean_ic": None, "t_hac": None}, {"mean_bps_per_day": None, "t_hac": None}),
                         "REMOVE")


if __name__ == "__main__":
    unittest.main()
