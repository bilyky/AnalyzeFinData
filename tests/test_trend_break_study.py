"""Tests for scripts/backtesting/trend_break_study.py (R&D #88): the zigzag with confirmation
indices, the pattern (two lower highs/lows, a higher high, a higher low), no look-ahead (the event
fires on the higher low's confirmation day, never earlier), the excess return, the monthly test and
the verdict."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import trend_break_study as tb  # noqa: E402


def path(points, steps=4):
    """Linear interpolation through `points` with `steps` bars per leg: a clean zigzag."""
    out = []
    for a, b in zip(points, points[1:]):
        out += [a + (b - a) * s / steps for s in range(steps)]
    return out + [points[-1]]


# Downtrend: highs 100 > 92 > 82, lows 80 > 70 > 60, then a higher high (86 > 82) and a higher low (70 > 60).
# Every leg moves more than 8%, so each turn is confirmed.
PATTERN = [95, 100, 80, 92, 70, 82, 60, 86, 70, 90]


def bars(closes, opens=None, start=datetime.date(2024, 1, 1)):
    days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(len(closes))]
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": list(closes), "o": list(opens or closes)}


class TestZigzag(unittest.TestCase):
    def test_turns_carry_their_confirmation(self):
        c = [100, 109, 120, 105, 95, 100, 110]
        z = tb.gs.zigzag_indices(c, 0.10)
        self.assertEqual(z[0], (0, "low", 2))                  # 109 is +9%: the low is confirmed at bar 2 (120)
        self.assertEqual(z[1], (2, "high", 3))                 # 120 -> 105 (-12.5%) confirms the high at bar 3
        self.assertTrue(all(k > i for i, _t, k in z))           # confirmation always after the extreme


class TestPattern(unittest.TestCase):
    def test_fires_once_on_the_higher_low_confirmation(self):
        c = path(PATTERN)
        ev = tb.find_events(c, 0.08)
        self.assertEqual(len(ev), 1)
        k = ev[0]
        low_i = 8 * 4                                          # index of the 70 low in the path
        self.assertGreater(k, low_i)                           # never on the low's own day
        self.assertGreaterEqual(c[k], c[low_i] * 1.08)         # the close that confirms the reversal

    def test_no_look_ahead(self):
        """Cutting the series right before the confirmation removes the event; at it, the event is there."""
        c = path(PATTERN)
        k = tb.find_events(c, 0.08)[0]
        self.assertEqual(tb.find_events(c[:k], 0.08), [])
        self.assertEqual(tb.find_events(c[:k + 1], 0.08), [k])

    def test_needs_each_condition(self):
        for name, pts in (("lower low", [95, 100, 80, 92, 70, 82, 60, 86, 55, 90]),
                          ("no higher high", [95, 100, 80, 92, 70, 82, 60, 78, 70, 90]),
                          ("one lower high only", [95, 100, 80, 104, 70, 82, 60, 86, 70, 90]),
                          ("one lower low only", [95, 100, 65, 92, 70, 82, 60, 86, 70, 90])):
            with self.subTest(name):
                self.assertEqual(tb.find_events(path(pts), 0.08), [])


class TestExcess(unittest.TestCase):
    def test_entry_next_open_vs_spy_same_dates_minus_cost(self):
        n = 40
        c, o = [10.0] * n, [10.0] * n
        o[5] = 9.0                                             # the confirmation day's open: must not be the entry
        c[25] = 11.0                                           # +10% from the open of k+1=6 to the close of k+20=25
        b = bars(c, o)
        sc, so = [100.0] * n, [100.0] * n
        sc[25] = 102.0
        sc[5] = 98.0                                           # SPY's close before entry: must not be its entry price
        spy = bars(sc, so)
        self.assertAlmostEqual(tb.excess(b, spy, 5, 20), 10.0 - 2.0 - 0.1)
        self.assertAlmostEqual(tb.raw_return(b, 5, 20), 0.10)
        self.assertIsNone(tb.excess(b, spy, 25, 20))           # window beyond the data
        self.assertIsNone(tb.excess(b, bars(sc, start=datetime.date(2025, 1, 1)), 5, 20))   # no shared dates


class TestVerdict(unittest.TestCase):
    def _events(self, months, value, start_year=2008):
        out = []
        for k in range(months):
            y, m = start_year + k // 12, k % 12 + 1
            for j in range(5):
                wobble = (k % 3 - 1) * 0.3
                out.append({"symbol": f"S{j}", "date": f"{y}-{m:02d}-1{j}", "x20": value + wobble + (j - 2) * 0.1,
                            "x30": value, "r30": 0.2 if j == 0 else 0.0})
        return out

    def test_supported_needs_both_halves(self):
        good = tb.summarize(self._events(160, 2.0))           # 2008-2021: both halves
        self.assertEqual(good["20d"]["verdict"], "SUPPORTED")
        mixed = [e if e["date"] >= tb.SPLIT else {**e, "x20": -0.5} for e in self._events(160, 4.0)]
        self.assertEqual(tb.summarize(mixed)["20d"]["verdict"], "INCONCLUSIVE")

    def test_reversal_minimums_and_the_hac_t(self):
        self.assertEqual(tb.summarize(self._events(160, -2.0))["20d"]["verdict"], "REVERSAL")
        self.assertEqual(tb.summarize(self._events(12, 2.0))["20d"]["verdict"], "INCONCLUSIVE")       # < 24 months
        self.assertEqual(tb.verdict({"months": 30, "events": 80, "month_mean": 1.0, "t_hac": 5.0},
                                    {"month_mean": 1}, {"month_mean": 1}), "INCONCLUSIVE")         # < 100 events
        self.assertEqual(tb.verdict({"months": 30, "events": 200, "month_mean": 1.0, "t_hac": 1.5},
                                    {"month_mean": 1}, {"month_mean": 1}), "INCONCLUSIVE")

    def test_option_share_and_winsorizing(self):
        self.assertAlmostEqual(tb.summarize(self._events(30, 1.0))["share_r30_above_option_need"], 0.2)
        ev = [{"date": "2024-01-05", "x20": 1000.0}, {"date": "2024-02-05", "x20": 0.0}]
        self.assertAlmostEqual(tb.monthly_test(ev, "x20", 1, (-10, 10))["month_mean"], 5.0)


if __name__ == "__main__":
    unittest.main()
