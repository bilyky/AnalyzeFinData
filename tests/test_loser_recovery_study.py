"""O1 / R&D #81 loser-recovery study: events, outcomes, the monthly series and the verdict."""
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import loser_recovery_study as lr  # noqa: E402


def _days(n, start=datetime.date(2010, 1, 4)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


class TestMonthEnds(unittest.TestCase):
    def test_last_trading_day_of_each_month(self):
        days = ["2010-01-28", "2010-01-29", "2010-02-01", "2010-02-26", "2010-03-01"]
        self.assertEqual(lr.month_ends(days), [1, 3, 4])


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.days = _days(lr.LOOKBACK + lr.WASH + lr.HOLD + 10)
        self.spy = [100.0] * len(self.days)
        self.i = lr.LOOKBACK + 2                                    # the judged month-end index

    def series(self, start, at_t, at_wash, at_end):
        c = {d: start for d in self.days}
        c[self.days[self.i]] = at_t
        c[self.days[self.i + lr.WASH]] = at_wash
        c[self.days[self.i + lr.WASH + lr.HOLD]] = at_end
        return c

    def test_a_20pct_loser_is_an_event_with_the_buy_back_excess(self):
        spy = list(self.spy)
        spy[self.i + lr.WASH + lr.HOLD] = 110.0                    # SPY +10% over the buy-back window
        ev = lr.events_for(self.series(100, 80, 84, 105), self.days, spy, [self.i])
        self.assertEqual(len(ev), 1)
        self.assertAlmostEqual(ev[0]["loss"], -0.20)
        self.assertAlmostEqual(ev[0]["excess"], 105 / 84 - 1 - 0.10)   # bought back at t+31, held 126
        self.assertAlmostEqual(ev[0]["wash_excess"], 84 / 80 - 1)

    def test_spy_is_measured_over_the_same_window_as_the_stock(self):
        # Review of #312: with a flat SPY, measuring SPY from t instead of t+31 went unnoticed.
        spy = list(self.spy)
        for k in range(self.i + 1, len(spy)):
            spy[k] = 105.0                                          # SPY +5% inside the wash window
        spy[self.i + lr.WASH + lr.HOLD] = 110.25                     # +5% more over the buy-back window
        ev = lr.events_for(self.series(100, 80, 84, 105), self.days, spy, [self.i])[0]
        self.assertAlmostEqual(ev["excess"], (105 / 84 - 1) - 0.05)   # not - 0.1025 (SPY from t)
        self.assertAlmostEqual(ev["wash_excess"], (84 / 80 - 1) - 0.05)

    def test_a_smaller_loss_or_missing_bars_is_no_event(self):
        self.assertEqual(lr.events_for(self.series(100, 81, 84, 90), self.days, self.spy, [self.i]), [])
        gap = self.series(100, 70, 84, 90)
        del gap[self.days[self.i + lr.WASH]]
        self.assertEqual(lr.events_for(gap, self.days, self.spy, [self.i]), [])

    def test_no_event_without_a_full_lookback_or_forward_window(self):
        c = {d: 50.0 for d in self.days}
        self.assertEqual(lr.events_for(c, self.days, self.spy, [lr.LOOKBACK - 1, len(self.days) - 5]), [])

    def test_outcome_uses_no_data_before_the_wash_window(self):
        a = lr.events_for(self.series(100, 70, 84, 90), self.days, self.spy, [self.i])[0]
        b_series = self.series(100, 70, 84, 90)
        b_series[self.days[self.i + 5]] = 1.0                      # a crash inside the wash window
        b = lr.events_for(b_series, self.days, self.spy, [self.i])[0]
        self.assertEqual(a["excess"], b["excess"])


class TestSeriesAndVerdict(unittest.TestCase):
    def test_monthly_series_needs_enough_events(self):
        ev = [{"date": "2010-01-29", "excess": 0.1}] * 5 + [{"date": "2010-02-26", "excess": 0.2}] * 4
        self.assertEqual(lr.monthly_series(ev), [("2010-01", 0.1)])

    def test_winsorize_clips_the_tails(self):
        xs = list(range(101))
        w = lr.winsorize(xs)
        self.assertEqual((min(w), max(w)), (1, 99))

    def series(self, values):
        return [(f"m{i}", v) for i, v in enumerate(values)]

    def test_too_few_months(self):
        self.assertEqual(lr.verdict(self.series([0.05] * 35))[0], "INSUFFICIENT")

    def test_buy_back_losers_lag_and_inconclusive(self):
        with mock.patch.object(lr, "_t_hac", return_value=2.5):
            self.assertEqual(lr.verdict(self.series([0.05] * 40))[0], "BUY_BACK")
            self.assertEqual(lr.verdict(self.series([-0.05] * 40))[0], "INCONCLUSIVE")   # t positive, mean negative
        with mock.patch.object(lr, "_t_hac", return_value=-2.5):
            self.assertEqual(lr.verdict(self.series([-0.05] * 40))[0], "LOSERS_LAG")
        with mock.patch.object(lr, "_t_hac", return_value=1.9):
            self.assertEqual(lr.verdict(self.series([0.05] * 40))[0], "INCONCLUSIVE")

    def test_the_verdict_uses_lag_5(self):
        # Review of #312: 126-session windows sampled monthly overlap ~6 months; the lag is the protection.
        self.assertEqual(lr.HAC_LAG, 5)
        with mock.patch.object(lr, "_t_hac", return_value=0.0) as t:
            lr.verdict(self.series([0.01] * 40))
        self.assertEqual(t.call_args.args[1], 5)

    def test_lag_changes_the_t_on_an_autocorrelated_series(self):
        xs = [0.03 if (i // 6) % 2 else -0.01 for i in range(60)]   # six-month runs, mean +0.01
        self.assertLess(lr._t_hac(xs, lr.HAC_LAG), lr._t_hac(xs, 0))

    def test_real_hac_on_a_clear_series(self):
        v, t, m = lr.verdict(self.series([0.02 + 0.001 * (i % 3) for i in range(60)]))
        self.assertEqual(v, "BUY_BACK")
        self.assertGreater(t, 2)


if __name__ == "__main__":
    unittest.main()
