"""Tests for scripts/backtesting/gap_up_drift_study.py (R&D #84): event detection (gap, volume,
price floor, one per 20 sessions), the excess return (entry at the next open, SPY on the same
dates, costs), the monthly test and the verdict (incl. the both-halves rule)."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import gap_up_drift_study as gs  # noqa: E402


def bars(closes, opens=None, vols=None, start=datetime.date(2024, 1, 1)):
    days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(len(closes))]
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": list(closes),
            "o": list(opens or closes), "h": list(closes), "l": list(closes), "v": list(vols or [100.0] * len(closes))}


def gap_at(n, t, gap=0.15, vol=400.0, price=10.0):
    c = [price] * n
    o = list(c)
    v = [100.0] * n
    o[t] = price * (1 + gap)
    v[t] = vol
    return c, o, v


class TestEvents(unittest.TestCase):
    def test_gap_and_volume_both_needed(self):
        c, o, v = gap_at(60, 30)
        self.assertEqual(gs.find_events(bars(c, o, v)), [30])
        c, o, v = gap_at(60, 30, gap=0.09)
        self.assertEqual(gs.find_events(bars(c, o, v)), [])          # gap too small
        c, o, v = gap_at(60, 30, vol=290.0)
        self.assertEqual(gs.find_events(bars(c, o, v)), [])          # volume below 3x

    def test_price_floor_and_spacing(self):
        c, o, v = gap_at(60, 30, price=4.0)
        self.assertEqual(gs.find_events(bars(c, o, v)), [])          # under $5
        c, o, v = gap_at(80, 30)
        o[40], v[40] = 11.5, 400.0                                   # 10 sessions later: skipped
        o[55], v[55] = 11.5, 400.0                                   # 25 later: counted
        self.assertEqual(gs.find_events(bars(c, o, v)), [30, 55])

    def test_needs_volume_history(self):
        c, o, v = gap_at(60, 10)
        self.assertEqual(gs.find_events(bars(c, o, v)), [])          # fewer than 21 prior bars


class TestExcess(unittest.TestCase):
    def test_entry_next_open_vs_spy_same_dates_minus_cost(self):
        n = 40
        c = [10.0] * n
        o = list(c)
        o[11] = 11.0                                                  # next-day open (entry)
        c[30] = 12.1                                                  # +10% from 11.0 at t+20
        b = bars(c, o)
        spy_c = [100.0] * n
        spy_c[30] = 102.0
        spy = bars(spy_c)
        self.assertAlmostEqual(gs.excess(b, spy, 10, 20), 10.0 - 2.0 - 0.2)
        self.assertAlmostEqual(gs.excess(b, spy, 10, 20, at_open=False), 21.0 - 2.0 - 0.2)   # from t's close

    def test_missing_window_or_spy_date(self):
        b = bars([10.0] * 25)
        self.assertIsNone(gs.excess(b, bars([100.0] * 25), 10, 20))   # t+20 beyond the data
        b2 = bars([10.0] * 40)
        spy = bars([100.0] * 40, start=datetime.date(2025, 1, 1))     # no shared dates
        self.assertIsNone(gs.excess(b2, spy, 10, 20))


class TestVerdict(unittest.TestCase):
    def _events(self, months, value, start_year=2023):
        out = []
        for k in range(months):
            y, m = start_year + k // 12, k % 12 + 1
            for j in range(5):
                wobble = (k % 3 - 1) * 0.3                            # months differ: a t needs variance
                out.append({"symbol": f"S{j}", "date": f"{y}-{m:02d}-1{j}", "gap": 12.0,
                            "x20": value + wobble + (j - 2) * 0.1,
                            "x60": value, "x20_close": value})
        return out

    def test_supported_needs_both_halves(self):
        good = self._events(48, 2.0)
        self.assertEqual(gs.summarize(good)["verdict"], "DRIFT_SUPPORTED")
        mixed = [e if e["date"] >= gs.SPLIT else {**e, "x20": -0.5} for e in self._events(48, 4.0)]
        s = gs.summarize(mixed)
        self.assertGreater(s["primary"]["month_mean"], 0)
        self.assertEqual(s["verdict"], "INCONCLUSIVE")                # 2023-24 half negative

    def test_reversal_and_minimums(self):
        self.assertEqual(gs.summarize(self._events(48, -2.0))["verdict"], "REVERSAL")
        self.assertEqual(gs.summarize(self._events(12, 2.0))["verdict"], "INCONCLUSIVE")     # < 24 months
        self.assertEqual(gs.verdict({"months": 30, "events": 80, "month_mean": 1.0, "t_hac": 5.0},
                                    {"month_mean": 1}, {"month_mean": 1}), "INCONCLUSIVE")    # < 100 events

    def test_verdict_uses_the_hac_t(self):
        p = {"months": 30, "events": 200, "month_mean": 1.0, "t": 3.0, "t_hac": 1.5}   # plain t passes, HAC doesn't
        self.assertEqual(gs.verdict(p, {"month_mean": 1}, {"month_mean": 1}), "INCONCLUSIVE")
        self.assertEqual(gs.verdict({**p, "t_hac": 2.5, "t": 1.0}, {"month_mean": 1}, {"month_mean": 1}),
                         "DRIFT_SUPPORTED")

    def test_monthly_test_winsorizes(self):
        ev = [{"date": "2024-01-05", "x20": 1000.0}, {"date": "2024-02-05", "x20": 0.0}]
        self.assertAlmostEqual(gs.monthly_test(ev, "x20", 1, (-10, 10))["month_mean"], 5.0)


if __name__ == "__main__":
    unittest.main()
