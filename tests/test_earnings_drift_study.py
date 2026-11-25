"""R&D #98 step 3, Part A: reaction classification, event finding, the held window, the portfolio and the verdict."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import earnings_drift_study as ed  # noqa: E402


def _days(n):
    return [f"2010-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]


class TestEvents(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(ed.classify(0.06, 2.5), "UP")
        self.assertEqual(ed.classify(-0.07, 3.0), "DOWN")
        self.assertIsNone(ed.classify(0.06, 1.5))          # light volume
        self.assertIsNone(ed.classify(0.04, 5.0))          # small move
        self.assertIsNone(ed.classify(0.10, None))

    def test_events_use_reaction_vs_spy_and_volume(self):
        days = _days(40)
        closes = [100.0] * 25 + [108.0] + [108.0] * 14
        vols = [1000.0] * 25 + [3000.0] + [1000.0] * 14
        spy = {d: (100.0 if i < 25 else 102.0) for i, d in enumerate(days)}   # SPY +2% on the same day
        ev = ed.events_for(days, closes, vols, spy, {days[25]}, start="2010-01-01")
        self.assertEqual(len(ev), 1)
        i, g, reaction, vr = ev[0]
        self.assertEqual((i, g), (25, "UP"))
        self.assertAlmostEqual(reaction, 0.08 - 0.02)
        self.assertAlmostEqual(vr, 3.0)
        # the same move with SPY up 4% is only +4% excess: no event
        spy2 = {d: (100.0 if i < 25 else 104.0) for i, d in enumerate(days)}
        self.assertEqual(ed.events_for(days, closes, vols, spy2, {days[25]}, start="2010-01-01"), [])

    def test_too_early_or_missing_session_is_skipped(self):
        days = _days(30)
        spy = {d: 100.0 for d in days}
        self.assertEqual(ed.events_for(days, [100.0] * 30, [1.0] * 30, spy, {days[5], "2099-01-01"},
                                       start="2010-01-01"), [])


class TestInSession(unittest.TestCase):
    def test_in_session(self):
        self.assertTrue(ed.in_session("2026-10-29T09:30:00-04:00"))
        self.assertTrue(ed.in_session("2026-10-29T15:59:59-04:00"))
        self.assertFalse(ed.in_session("2026-10-29T16:00:00-04:00"))
        self.assertFalse(ed.in_session("2026-10-29T07:02:04-04:00"))
        self.assertFalse(ed.in_session(None))


class TestHolding(unittest.TestCase):
    def test_held_excess_window(self):
        days = _days(10)
        closes = [100.0, 100.0, 110.0, 110.0, 99.0] + [99.0] * 5
        spy = {d: 100.0 for d in days}
        ex = ed.held_excess(days, closes, spy, 1, 3)
        self.assertEqual(list(ex), days[2:5])
        self.assertAlmostEqual(ex[days[2]], 0.10)
        self.assertAlmostEqual(ex[days[4]], -0.10)

    def test_portfolio_needs_min_active(self):
        out = ed.portfolio({"a": [0.01, 0.02, 0.03], "b": [0.05, 0.05]}, min_active=3)
        self.assertEqual(list(out), ["a"])
        self.assertAlmostEqual(out["a"], 0.02)


class TestVerdict(unittest.TestCase):
    def test_sides(self):
        up = [{"t": 2.5}, {"t": 2.1}]
        down = [{"t": -3.0}, {"t": -2.2}]
        self.assertEqual(ed.verdict(up, down), ["DRIFT_UP", "DRIFT_DOWN"])
        self.assertEqual(ed.verdict([{"t": 2.5}, {"t": 1.9}], down), ["DRIFT_DOWN"])
        self.assertEqual(ed.verdict(up, [{"t": -3.0}, {"t": 0.5}]), ["DRIFT_UP"])
        self.assertEqual(ed.verdict([{"t": None}, {"t": 3}], [{"t": 2.5}, {"t": 2.5}]), ["INCONCLUSIVE"])

    def test_half_stats(self):
        s = ed.half_stats({"2010-01-01": 0.001, "2010-01-02": 0.001, "2017-01-01": 0.5}, "2005-01-01", "2016-01-01")
        self.assertEqual(s["days"], 2)
        self.assertAlmostEqual(s["mean_daily_excess_bps"], 10.0)


if __name__ == "__main__":
    unittest.main()
