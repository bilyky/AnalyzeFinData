"""Pre-report risk study (R&D #98 step 2): groups, outcomes, daily differences and the verdict."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import earnings_risk_study as er  # noqa: E402


def _days(n):
    return [f"2010-01-{i + 1:02d}" if i < 31 else f"2010-02-{i - 30:02d}" for i in range(n)]


class TestGroups(unittest.TestCase):
    def test_report_index(self):
        days = ["d0", "d1", "d2", "d3", "d4"]
        ahead = er.report_index(days, {"d2"})
        self.assertEqual(list(ahead[:3]), [2, 1, 0])
        self.assertTrue(math.isinf(ahead[3]) and math.isinf(ahead[4]))

    def test_rows_groups_and_outcomes(self):
        days = _days(45)
        closes = [100.0] * 45
        closes[3] = 80.0                         # a 20% dip right after day 0..2
        spy = {d: 100.0 for d in days}
        sessions = {days[5]}                     # report first session at index 5
        rows = {r[0]: r for r in er.symbol_rows(days, closes, spy, sessions, h=10, start="2010-01-01")}
        # index 0: report 5 sessions ahead -> before; worst close 80 -> big drop
        self.assertEqual(rows[days[0]][1:3], ("before", 1))
        # index 5 (report today) and 6..14 (report passed? no: next report none -> clean only if > 2h ahead)
        self.assertNotIn(days[5], rows)          # report today: excluded
        self.assertEqual(rows[days[6]][1], "clean")   # no later report
        self.assertAlmostEqual(rows[days[6]][3], 0.0)

    def test_report_11_to_20_ahead_is_left_out(self):
        days = _days(60)
        rows = er.symbol_rows(days, [100.0] * 60, {d: 1.0 for d in days}, {days[25]}, h=10, start="2010-01-01")
        got = {r[0]: r[1] for r in rows}
        self.assertNotIn(days[10], got)          # 15 ahead
        self.assertEqual(got[days[15]], "before")  # 10 ahead
        self.assertEqual(got[days[4]], "clean")    # 21 ahead

    def test_excess_vs_spy(self):
        days = _days(20)
        closes = [100.0] * 9 + [110.0] * 11
        spy = {d: (100.0 if i < 10 else 105.0) for i, d in enumerate(days)}
        r = er.symbol_rows(days, closes, spy, set(), h=10, start="2010-01-01")[0]
        self.assertAlmostEqual(r[3], 0.10 - 0.05)


class TestStats(unittest.TestCase):
    def test_daily_differences_need_enough_stocks(self):
        rows = [("d1", "before", 1, 0.0)] * 5 + [("d1", "clean", 0, 0.01)] * 5 + [("d2", "before", 1, 0.0)] * 4 + [("d2", "clean", 0, 0.0)] * 9
        out = er.daily_differences(rows, min_per_group=5)
        self.assertEqual(list(out), ["d1"])
        self.assertAlmostEqual(out["d1"][0], 1.0)
        self.assertAlmostEqual(out["d1"][1], -0.01)

    def test_verdict(self):
        def h(bd, ex):
            return {"big_drop_t": bd, "excess_t": ex}
        self.assertEqual(er.verdict([h(3, 0.5), h(2.5, -1)]), "RULE_HELPS")
        self.assertEqual(er.verdict([h(3, 2.1), h(2.5, 0)]), "INCONCLUSIVE")      # a clear gain in one half
        self.assertEqual(er.verdict([h(3, 2.1), h(2.5, 2.2)]), "TRADE_OFF")
        self.assertEqual(er.verdict([h(1, 2.1), h(2.5, 2.2)]), "RULE_COSTS")
        self.assertEqual(er.verdict([h(3, 0), h(1.9, 0)]), "INCONCLUSIVE")
        self.assertEqual(er.verdict([h(None, 0), h(3, 0)]), "INCONCLUSIVE")

    def test_half_stats_uses_the_overlap_lag(self):
        # blocks of 10 equal daily differences (overlapping 10-session windows): the Newey-West t with
        # lag H - 1 = 9 is far smaller than a plain (lag 0) t on the same series (#325 review)
        vals = [(0.02 if (i // 10) % 3 else -0.01) for i in range(200)]
        diffs = {f"2010-{i:04d}": (v, v, 5, 5) for i, v in enumerate(vals)}
        st = er.half_stats(diffs, "2010-0000", None, h=10)
        self.assertAlmostEqual(st["big_drop_t"], round(er._t_hac(vals, 9), 2))
        self.assertLess(st["big_drop_t"], er._t_hac(vals, 0) * 0.6)

    def test_report_day_share(self):
        days = ["a", "b", "c"]
        self.assertAlmostEqual(er.report_day_share(days, [100.0, 110.0, 99.0], {"c"}), 0.10 / 0.20)


if __name__ == "__main__":
    unittest.main()
