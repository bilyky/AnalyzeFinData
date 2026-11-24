"""R&D #124 dynamic step sizing: the step rule, both buying plans, the advantage and the verdict."""
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import dynamic_sizing_study as ds  # noqa: E402


def _flat_then(last, n=60):
    return [100.0] * (n - 1) + [last]


class TestStepRule(unittest.TestCase):
    def test_bands(self):
        # The 50-day average includes the day itself (the plan check's definition).
        self.assertEqual(ds.step_for([100.0] * 60, 59), 0.10)                    # at the average: 0..-4% band
        self.assertEqual(ds.step_for([100.0] * 40 + [101.0] * 20, 59), 0.05)     # above the average
        c = [100.0] * 59 + [93.0]                                               # ~ -6.9% vs the average, a -7% day
        self.assertEqual(ds.step_for(c, 59), 0.15)
        c = [100.0] * 59 + [90.0]
        self.assertEqual(ds.step_for(c, 59), 0.25)                               # ~ -9.8%

    def test_jump_day_halves_the_step(self):
        c = [100.0] * 40 + [80.0] * 19 + [83.0]                                 # +3.75% day, ~9.8% under
        self.assertEqual(ds.step_for(c, 59), 0.25 / 2)
        c = [100.0] * 49 + [88.0] * 10 + [91.0]                                 # +3.4% day, ~6.6% under
        self.assertEqual(ds.step_for(c, 59), 0.15 / 2)


class TestPlans(unittest.TestCase):
    def test_equal_plan_is_ten_tenths(self):
        c = list(range(100, 160))
        self.assertEqual([round(a, 2) for a, _ in ds.equal_buys(c, 0)], [0.1] * 10)
        self.assertEqual([p for _, p in ds.equal_buys(c, 3)], list(range(103, 113)))   # one buy per session's close

    def test_dynamic_plan_spends_the_budget(self):
        c = [100.0] * 49 + [101.0 + 0.01 * i for i in range(60)]               # above the average: 5% steps
        buys = ds.dynamic_buys(c, 49)
        self.assertAlmostEqual(sum(a for a, _ in buys), 1.0)
        self.assertEqual(len(buys), 20)                                          # 20 x 5%
        self.assertTrue(all(abs(a - 0.05) < 1e-9 for a, _ in buys))

    def test_whatever_is_left_is_bought_at_session_40(self):
        with mock.patch.object(ds, "step_for", return_value=0.01):
            buys = ds.dynamic_buys([100.0] * 120, 50)
        self.assertEqual(len(buys), ds.WINDOW)
        self.assertAlmostEqual(buys[-1][0], 1.0 - 39 * 0.01)

    def test_average_cost_is_amount_weighted(self):
        self.assertAlmostEqual(ds.avg_cost([(0.5, 100.0), (0.5, 50.0)]), 1 / (0.5 / 100 + 0.5 / 50))

    def test_advantage_sign(self):
        # A steady rise above the average: 5% steps buy later and dearer than ten equal steps -> negative.
        c = [100.0] * 49 + [101.0 + 0.5 * i for i in range(60)]
        self.assertLess(ds.advantage(c, 49), 0)
        self.assertAlmostEqual(ds.advantage(c, 49),
                               math.log(ds.avg_cost(ds.equal_buys(c, 49)) / ds.avg_cost(ds.dynamic_buys(c, 49))))
        # Flat prices: no difference.
        self.assertAlmostEqual(ds.advantage([100.0] * 120, 60), 0.0)


class TestVerdict(unittest.TestCase):
    def test_branches(self):
        self.assertEqual(ds.verdict([0.01] * 23)[0], "INSUFFICIENT")
        with mock.patch.object(ds, "_t_hac", return_value=2.5):
            self.assertEqual(ds.verdict([0.01] * 30)[0], "DYNAMIC_BETTER")
            self.assertEqual(ds.verdict([-0.01] * 30)[0], "INCONCLUSIVE")
        with mock.patch.object(ds, "_t_hac", return_value=-2.5):
            self.assertEqual(ds.verdict([-0.01] * 30)[0], "DYNAMIC_WORSE")
        with mock.patch.object(ds, "_t_hac", return_value=1.9):
            self.assertEqual(ds.verdict([0.01] * 30)[0], "INCONCLUSIVE")

    def test_lag_is_one(self):
        with mock.patch.object(ds, "_t_hac", return_value=0.0) as t:
            ds.verdict([0.01] * 30)
        self.assertEqual(t.call_args.args[1], 1)


if __name__ == "__main__":
    unittest.main()
