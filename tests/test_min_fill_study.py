"""R&D #65 min-fill study: the train pick and the pre-registered verdict."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import min_fill_study as mf


class TestPick(unittest.TestCase):
    def test_picks_the_best_train_sharpe_among_the_floors_never_the_baseline(self):
        train = {"no_floor": {"sharpe": 9.0}, "floor_1pct": {"sharpe": 0.8},
                 "floor_2pct": {"sharpe": 1.1}, "floor_4pct": {"sharpe": None}}
        self.assertEqual(mf.pick(train), "floor_2pct")


class TestVerdict(unittest.TestCase):
    def test_table(self):
        cases = [
            ({"mean_daily_pct": 0.02, "t_hac": 2.1}, -12.0, -11.0, "MIN_FILL_HELPS"),
            ({"mean_daily_pct": 0.02, "t_hac": 2.1}, -14.0, -11.0, "INCONCLUSIVE"),   # DD 3 pts deeper
            ({"mean_daily_pct": 0.02, "t_hac": 1.5}, -11.0, -11.0, "INCONCLUSIVE"),
            ({"mean_daily_pct": -0.02, "t_hac": -2.2}, -11.0, -11.0, "MIN_FILL_HURTS"),
            ({"mean_daily_pct": None, "t_hac": None}, -11.0, -11.0, "INCONCLUSIVE"),
        ]
        for diff, dd_pick, dd_base, want in cases:
            with self.subTest(diff=diff, dd_pick=dd_pick):
                self.assertEqual(mf.verdict(diff, dd_pick, dd_base), want)


if __name__ == "__main__":
    unittest.main()
