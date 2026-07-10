"""
Tests for backtest_levels — the walk-forward accuracy check for detect_support /
detect_resistance. Core outcome logic is deterministic; no files.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest_levels as bl


class TestEvaluate(unittest.TestCase):
    def test_target_first(self):
        r = bl._evaluate(90, 120, fwd_lows=[95, 96, 94], fwd_highs=[100, 121, 110])
        self.assertTrue(r["support_held"])
        self.assertTrue(r["target_hit"])
        self.assertEqual(r["outcome"], "target_first")

    def test_stop_first(self):
        # low breaks 90 on day 0; target 120 not reached until day 2.
        r = bl._evaluate(90, 120, fwd_lows=[85, 88, 95], fwd_highs=[100, 100, 121])
        self.assertFalse(r["support_held"])
        self.assertEqual(r["outcome"], "stop_first")

    def test_neither(self):
        r = bl._evaluate(90, 120, fwd_lows=[95, 96, 97], fwd_highs=[100, 105, 110])
        self.assertTrue(r["support_held"])
        self.assertFalse(r["target_hit"])
        self.assertEqual(r["outcome"], "neither")

    def test_support_only(self):
        r = bl._evaluate(90, None, fwd_lows=[80, 95], fwd_highs=[100, 100])
        self.assertFalse(r["support_held"])
        self.assertNotIn("outcome", r)


class TestAggregate(unittest.TestCase):
    def test_rates(self):
        recs = [
            {"support": 90, "resistance": 120, "price": 100, "support_held": True,
             "min_fwd_low": 95, "target_hit": True, "max_fwd_high": 121, "outcome": "target_first"},
            {"support": 90, "resistance": 120, "price": 100, "support_held": False,
             "min_fwd_low": 85, "target_hit": False, "max_fwd_high": 110, "outcome": "stop_first"},
        ]
        a = bl.aggregate(recs)
        self.assertEqual(a["samples"], 2)
        self.assertEqual(a["support"]["hold_rate"], 50.0)
        self.assertEqual(a["resistance"]["hit_rate"], 50.0)
        self.assertEqual(a["outcome"]["win_rate"], 50.0)   # 1 target_first / 2 decided


class TestBacktestSeries(unittest.TestCase):
    def test_runs_and_produces_records(self):
        # Sawtooth series long enough to have confirmed pivots.
        base = [100, 98, 96, 94, 96, 98, 100, 102, 100, 98] * 30   # 300 bars
        highs = [x + 2 for x in base]
        lows = [x - 2 for x in base]
        closes = base
        recs = bl.backtest_series(highs, lows, closes, horizon=10, step=10, start_after=60)
        self.assertTrue(len(recs) > 0)
        agg = bl.aggregate(recs)
        self.assertEqual(agg["samples"], len(recs))


if __name__ == "__main__":
    unittest.main()
