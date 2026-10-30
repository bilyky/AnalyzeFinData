"""Helpers of the entry-gates portfolio comparison."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import entry_gates_portfolio as eg


class TestAdmit(unittest.TestCase):
    def test_monthly_admit_uses_that_months_set_else_fallback(self):
        adm = eg.monthly_admit({"2024-03": frozenset({("A", "2024-03-05")})}, frozenset({("B", "2024-02-01")}))
        self.assertTrue(adm("A", "2024-03-05"))
        self.assertFalse(adm("B", "2024-03-05"))     # March uses March's set only
        self.assertTrue(adm("B", "2024-02-01"))      # no pick for February -> fallback
        self.assertTrue(eg.in_set(frozenset({("A", "d")}))("A", "d"))

    def test_relaxations_change_one_thing(self):
        for name, rule in eg.relaxations().items():
            with self.subTest(name=name):
                self.assertEqual(sum(x != y for x, y in zip(rule, eg.wf.CURRENT)), 1)
        # ...and to the intended value (#215 review: "setup_sma" -> "off" passed the old test).
        want = {"setup_off": (1, "off"), "setup_sma": (1, "sma"), "rr_1": (2, 1.0), "s10_floor_0": (0, 0.0),
                "leader_path": (4, True), "rebound_path": (5, True)}
        for name, (pos, value) in want.items():
            with self.subTest(name=name):
                self.assertEqual(eg.relaxations()[name][pos], value)


class TestStats(unittest.TestCase):
    def test_daily_returns_and_paired_diff(self):
        curve = [("d1", 100.0, 0), ("d2", 110.0, 0), ("d3", 99.0, 0)]
        r = eg.daily_returns(curve)
        self.assertAlmostEqual(r["d2"], 0.10)
        self.assertAlmostEqual(r["d3"], -0.10)
        flat = {"d2": 0.0, "d3": 0.0}
        self.assertEqual(eg.diff_test(r, flat)["days"], 2)
        self.assertAlmostEqual(eg.diff_test(r, flat)["mean_daily_pct"], 0.0)

    def test_winners_bought_counts_runs_once_within_window(self):
        starts = {"A": [datetime.date(2024, 3, 1)], "B": [datetime.date(2024, 3, 1)]}
        trades = [{"symbol": "A", "entry_date": "2024-03-10"}, {"symbol": "A", "entry_date": "2024-03-12"},
                  {"symbol": "B", "entry_date": "2024-05-01"}]     # B bought too late
        self.assertEqual(eg.winners_bought(trades, starts), 1)

    def test_verdict(self):
        ok = {"mean_daily_pct": 0.02, "t_hac": 2.5}
        self.assertEqual(eg.verdict(ok, -12.0, -10.0), "ADAPTIVE_PORTFOLIO_HELPS")
        self.assertEqual(eg.verdict(ok, -16.0, -10.0), "INCONCLUSIVE")       # 6 points deeper
        self.assertEqual(eg.verdict({"mean_daily_pct": 0.02, "t_hac": 1.0}, -10, -10), "INCONCLUSIVE")
        self.assertEqual(eg.verdict({"mean_daily_pct": -0.02, "t_hac": -2.5}, -10, -10),
                         "ADAPTIVE_PORTFOLIO_HURTS")
        self.assertEqual(eg.verdict({"mean_daily_pct": None, "t_hac": None}, 0, 0), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
