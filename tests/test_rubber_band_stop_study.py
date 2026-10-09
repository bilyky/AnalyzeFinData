"""Rubber-band stop study: event detection (no look-ahead), the fill rule, date clustering,
and the verdict function."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
from aether import risk_utils
import rubber_band_stop_study as st


def _ev(**kw):
    ev = {"sym": "AAA", "date": "2026-03-02", "stop": 100.0, "source": "atr", "open": 90.0,
          "high": 92.0, "open1": 91.0, "high1": 93.0, "close1": 89.0, "atr": 4.0}
    ev.update(kw)
    return ev


def _series(n=80, start=100.0):
    """A steady uptrend with a 2-point daily range; weekdays only, no long gaps."""
    d0 = datetime.date(2025, 1, 6)
    dates, o, h, lo, c = [], [], [], [], []
    day = d0
    for i in range(n):
        while day.weekday() >= 5:
            day += datetime.timedelta(days=1)
        px = start + i * 0.5
        dates.append(day.isoformat())
        o.append(px)
        h.append(px + 1)
        lo.append(px - 1)
        c.append(px + 0.5)
        day += datetime.timedelta(days=1)
    return dates, o, h, lo, c


class TestFillRule(unittest.TestCase):
    def test_next_open_above_limit_fills_at_that_open(self):
        self.assertEqual(st.rebound_fill(_ev(open1=101.0), 100.0), 101.0)

    def test_next_high_reaching_limit_fills_at_limit(self):
        self.assertEqual(st.rebound_fill(_ev(open1=95.0, high1=100.5), 100.0), 100.0)

    def test_miss_sells_at_next_close(self):
        self.assertEqual(st.rebound_fill(_ev(open1=91.0, high1=93.0, close1=85.0), 100.0), 85.0)

    def test_same_day_high_counts_only_in_the_optimistic_variant(self):
        ev = _ev(high=100.0, open1=91.0, high1=93.0, close1=85.0)
        self.assertEqual(st.rebound_fill(ev, 100.0), 85.0)
        self.assertEqual(st.rebound_fill(ev, 100.0, same_day=True), 100.0)

    def test_limits_and_outcome(self):
        ev = _ev(open=90.0, stop=100.0)
        self.assertEqual(st.limit_price("STOP", ev), 100.0)
        self.assertEqual(st.limit_price("HALF", ev), 95.0)
        # HALF limit 95 reached by high1 93? no -> close1 89 -> 89/90 - 1
        self.assertAlmostEqual(st.outcome(ev, "HALF"), 89.0 / 90.0 - 1)


class TestFindEvents(unittest.TestCase):
    def test_gap_below_the_production_stop_is_an_event(self):
        dates, o, h, lo, c = _series()
        t = 70
        stop = risk_utils.resolve_stop_detailed(c[t - 1], highs=h[:t], lows=lo[:t], closes=c[:t])["stop"]
        o[t] = stop - 5
        h[t], lo[t], c[t] = o[t] + 1, o[t] - 1, o[t]
        evs = st.find_events("AAA", dates, o, h, lo, c, min_date="2025-01-01")
        self.assertEqual([e["date"] for e in evs], [dates[t]])
        self.assertEqual(evs[0]["stop"], stop)
        self.assertEqual(evs[0]["open1"], o[t + 1])

    def test_stop_uses_bars_before_t_only(self):
        dates, o, h, lo, c = _series()
        t = 70
        stop = risk_utils.resolve_stop_detailed(c[t - 1], highs=h[:t], lows=lo[:t], closes=c[:t])["stop"]
        o[t] = stop - 5
        # Day t's own (crashed) bar must not move the stop it is judged against.
        h[t], lo[t], c[t] = o[t] + 1, 1.0, 2.0
        evs = st.find_events("AAA", dates, o, h, lo, c, min_date="2025-01-01")
        self.assertEqual(evs[0]["stop"], stop)

    def test_stale_bar_before_the_gap_is_not_an_event(self):
        dates, o, h, lo, c = _series()
        t = 70
        stop = risk_utils.resolve_stop_detailed(c[t - 1], highs=h[:t], lows=lo[:t], closes=c[:t])["stop"]
        o[t] = stop - 5
        late = datetime.date.fromisoformat(dates[t - 1]) + datetime.timedelta(days=20)
        dates = dates[:t] + [(late + datetime.timedelta(days=i)).isoformat() for i in range(len(dates) - t)]
        self.assertEqual(st.find_events("AAA", dates, o, h, lo, c, min_date="2025-01-01"), [])


class TestClusteringAndVerdict(unittest.TestCase):
    def test_events_on_one_date_count_once(self):
        a = _ev(date="2026-03-02", open1=101.0)          # STOP outcome +12.2%
        b = _ev(date="2026-03-02", sym="BBB", close1=81.0)  # miss: -10%
        res = st.summarize([a, b], spy_gap={})
        self.assertEqual(res["STOP/ALL"]["n_events"], 2)
        self.assertEqual(res["STOP/ALL"]["n_dates"], 1)
        self.assertAlmostEqual(res["STOP/ALL"]["mean_per_date"], ((101 / 90 - 1) + (81 / 90 - 1)) / 2)
        self.assertEqual(res["STOP/ALL"]["fill_rate"], 0.5)

    def test_groups(self):
        ev = _ev(date="2026-03-02", stop=100.0, open=90.0, atr=4.0)
        self.assertEqual(st.groups_of(ev, {"2026-03-02": -0.01}), ["ALL", "MARKET", "LARGE"])
        self.assertEqual(st.groups_of(_ev(open=98.0), {"2026-03-02": 0.0}), ["ALL", "IDIO", "SMALL"])

    def test_verdict(self):
        cases = [
            ((3.0, 0.004, 0.001, 40), "REBOUND_HELPS"),
            ((3.0, 0.004, -0.001, 40), "INCONCLUSIVE"),   # mean up, median down: no
            ((3.0, 0.001, 0.001, 40), "INCONCLUSIVE"),    # below the economic floor
            ((2.5, 0.004, 0.001, 40), "INCONCLUSIVE"),    # below the Bonferroni gate
            ((3.0, 0.004, 0.001, 20), "INCONCLUSIVE"),    # too few dates
            ((-2.9, -0.01, -0.01, 40), "REBOUND_HURTS"),
            ((None, 0.0, 0.0, 40), "INCONCLUSIVE"),
        ]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(st.verdict(*args), want)


if __name__ == "__main__":
    unittest.main()
