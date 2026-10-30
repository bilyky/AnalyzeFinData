"""Rankers, the train pick and the verdict of the ranking study."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ranking_study as rs


class TestRankers(unittest.TestCase):
    def test_keys(self):
        extra = {("A", "d"): (7.0, -30.0, True), ("B", "d"): (1.0, 10.0, False)}
        rk = rs.rankers(extra)
        self.assertIsNone(rk["combined"])
        sc_a, sc_b = (2.0, 3.0), (4.0, 4.0)
        order = lambda name: sorted(["A", "B"], key=lambda s: -rk[name](s, "d", {"A": sc_a, "B": sc_b}[s]))
        self.assertEqual(order("s10"), ["B", "A"])
        self.assertEqual(order("l60"), ["B", "A"])
        self.assertEqual(order("combined_waived"), ["A", "B"])    # 7 + 3 > 1 + 4
        self.assertEqual(order("deepest_pullback"), ["A", "B"])   # -30% first
        self.assertEqual(order("leader_first"), ["A", "B"])       # leader beats a higher score
        self.assertEqual(rk["s10"]("Z", "d", (1.0, 1.0)), 1.0)    # missing extra -> defaults


class TestPickAndVerdict(unittest.TestCase):
    def test_pick_highest_train_sharpe_ignoring_missing(self):
        self.assertEqual(rs.pick({"combined": {"sharpe": 0.5}, "s10": {"sharpe": 0.9}, "l60": {"sharpe": None}}), "s10")

    def test_verdict(self):
        good = {"mean_daily_pct": 0.03, "t_hac": 2.4}
        self.assertEqual(rs.verdict("combined", good, -10, -10), "KEEP_CURRENT")
        self.assertEqual(rs.verdict("s10", good, -12, -10), "RANKING_HELPS")
        self.assertEqual(rs.verdict("s10", good, -16, -10), "INCONCLUSIVE")     # 6 points deeper
        self.assertEqual(rs.verdict("s10", {"mean_daily_pct": 0.03, "t_hac": 1.2}, -10, -10), "INCONCLUSIVE")
        self.assertEqual(rs.verdict("s10", {"mean_daily_pct": None, "t_hac": None}, -10, -10), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
