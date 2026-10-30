"""Grouping, date-clustered statistic and verdict of the trend-leader study."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import trend_leader_study as tl


def _snap(pgr=5, lt="Strong", mf="Strong", rs="Strong"):
    return {"checklist_stocks": {"pgrRating": pgr, "ltTrend": lt, "moneyFlow": mf, "relativeStrength": rs}}


class TestLeader(unittest.TestCase):
    def test_all_four_conditions_required(self):
        self.assertTrue(tl.is_leader(_snap()))
        for kw in ({"pgr": 4}, {"lt": "Neutral"}, {"mf": "Weak"}, {"rs": "Neutral"}):
            with self.subTest(kw=kw):
                self.assertFalse(tl.is_leader(_snap(**kw)))


class TestGroupsAndStatistic(unittest.TestCase):
    # (date, leader, at_high, admitted, fwd20, fwd60)
    OBS = [("2024-01-02", True, True, False, 10.0, 20.0), ("2024-01-02", False, False, True, 2.0, 4.0),
           ("2024-01-02", False, False, False, 0.0, None),
           ("2024-01-03", True, False, False, 6.0, None), ("2024-01-03", False, False, False, 2.0, 1.0)]

    def test_per_day_groups_and_clipping(self):
        g = tl.per_day_groups(self.OBS, 4, bounds=(0.0, 8.0))
        self.assertEqual(g["2024-01-02"]["LEADER"], [8.0])          # clipped
        self.assertEqual(g["2024-01-02"]["REST"], [2.0, 0.0])
        self.assertEqual(g["2024-01-02"]["LEADER_AT_HIGH"], [8.0])
        self.assertEqual(g["2024-01-02"]["ADMITTED"], [2.0])
        self.assertNotIn("LEADER_AT_HIGH", g["2024-01-03"])
        self.assertEqual(tl.per_day_groups(self.OBS, 5)["2024-01-02"]["REST"], [4.0])   # None skipped

    def test_clustered_diff_is_per_date_then_averaged(self):
        r = tl.clustered(tl.per_day_groups(self.OBS, 4), "LEADER", "REST", lag=0)
        # 01-02: 10 - mean(2, 0) = 9; 01-03: 6 - 2 = 4 -> mean 6.5 (not the pooled difference)
        self.assertEqual(r["days"], 2)
        self.assertEqual(r["mean_diff"], 6.5)


class TestVerdict(unittest.TestCase):
    def test_table(self):
        cases = [
            ({"mean_diff": 1.0, "t_hac": 2.5}, {"mean_diff": 2.0}, "LEADERS_BEAT"),
            ({"mean_diff": 1.0, "t_hac": 2.5}, {"mean_diff": -0.5}, "INCONCLUSIVE"),   # 60d disagrees
            ({"mean_diff": 1.0, "t_hac": 1.5}, {"mean_diff": 2.0}, "INCONCLUSIVE"),    # not significant
            ({"mean_diff": -1.0, "t_hac": -2.5}, {"mean_diff": -2.0}, "LEADERS_LAG"),
            ({"mean_diff": -1.0, "t_hac": -1.0}, {"mean_diff": -2.0}, "INCONCLUSIVE"),
            ({"mean_diff": None, "t_hac": None}, {}, "INCONCLUSIVE"),
        ]
        for p20, p60, want in cases:
            with self.subTest(p20=p20, p60=p60):
                self.assertEqual(tl.verdict(p20, p60), want)


if __name__ == "__main__":
    unittest.main()
