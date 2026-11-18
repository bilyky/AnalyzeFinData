"""Tests for scripts/backtesting/beaten_quality_vs_spy.py (R&D #91b): stock-minus-SPY from the next
close, the stock's normal drift vs SPY, the monthly test and the verdicts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import beaten_quality_vs_spy as vs  # noqa: E402


def bars(closes, start=0):
    days = [f"2015-{1 + (i + start) // 28:02d}-{1 + (i + start) % 28:02d}" for i in range(len(closes))]
    return {"c": list(map(float, closes)), "days": days, "idx": {d: i for i, d in enumerate(days)}}


class TestVsSpy(unittest.TestCase):
    def test_from_next_close(self):
        n = vs.H + 10
        stock = bars([100.0, 50.0] + [60.0] * (n - 2))
        spy = bars([200.0] * 2 + [220.0] * (n - 2))
        # from close[1] (50) to close[61] (60) = +20%; SPY 200 -> 220 = +10%
        self.assertAlmostEqual(vs.vs_spy_at(stock, 0, spy), 10.0)
        self.assertIsNone(vs.vs_spy_at(stock, n - vs.H - 1, spy))                 # window runs off the end

    def test_missing_spy_day(self):
        n = vs.H + 5
        self.assertIsNone(vs.vs_spy_at(bars([100.0] * n), 0, bars([100.0] * n, start=200)))

    def test_drift_is_the_normal_edge(self):
        n = vs.H + 40
        stock = bars([100.0 * (1.001 ** i) for i in range(n)])
        spy = bars([100.0] * n)
        d = vs.drift_by_era(stock, spy)
        self.assertAlmostEqual(d["2010-2022"], (1.001 ** vs.H - 1) * 100, places=6)


class TestStats(unittest.TestCase):
    def test_monthly_and_verdict(self):
        ev = [{"date": f"20{10 + m // 12}-{1 + m % 12:02d}-05", "vs_spy": 2.0 + (m % 3)} for m in range(40) for _ in range(3)]
        full = vs.monthly(ev, "vs_spy")
        self.assertEqual((full["months"], full["events"]), (40, 120))
        self.assertEqual(full["beat_share"], 1.0)
        pos = {"mean": 1.0}
        self.assertEqual(vs.verdict(full, pos, pos, "UP", "DOWN"), "UP")
        self.assertEqual(vs.verdict(full, {"mean": -1.0}, pos, "UP", "DOWN"), "INCONCLUSIVE")
        self.assertEqual(vs.verdict(dict(full, t_hac=-3.0, mean=-1.0), {}, {}, "UP", "DOWN"), "DOWN")
        self.assertEqual(vs.verdict(dict(full, events=50), pos, pos, "UP", "DOWN"), "INSUFFICIENT")
        self.assertEqual(vs.verdict(dict(full, months=10), pos, pos, "UP", "DOWN"), "INSUFFICIENT")
        self.assertEqual(vs.verdict(dict(full, t_hac=1.0), pos, pos, "UP", "DOWN"), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
