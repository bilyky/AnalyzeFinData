"""Groups, per-date statistic, verdict and Setup-split parity of the Setup horizon study."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import setup_gate_horizon_study as st
from aether import risk_utils


def _ts(closes):
    dates = [f"2026-01-{i + 1:02d}" for i in range(len(closes))]
    return dates, {d: {"4. close": str(c)} for d, c in zip(dates, closes)}


class TestSetupParts(unittest.TestCase):
    def test_parts_agree_with_production_setup_ok(self):
        base = [100.0] * 20 + [90.0, 95.0, 97.0, 99.0, 101.0]
        dates, ts = _ts(base)
        for idx in range(20, len(dates)):
            for price in (85.0, 96.0, 100.0, 100.5, 105.0):   # 100.0 = SMA20 at idx 20
                with self.subTest(idx=idx, price=price):
                    above, up = st.setup_parts(price, idx, dates, ts)
                    self.assertEqual(above and up, risk_utils.setup_ok(price, idx, dates, ts))

    def test_price_equal_to_sma_is_not_above(self):
        dates, ts = _ts([100.0] * 17 + [90.0, 110.0, 100.0, 0.0])   # SMA20 of bars 0-19 = 100
        self.assertEqual(st.setup_parts(100.0, 20, dates, ts), (False, True))
        self.assertFalse(risk_utils.setup_ok(100.0, 20, dates, ts))

    def test_each_half_can_fail_alone(self):
        dates, ts = _ts([100.0] * 20 + [80.0, 80.0, 80.0, 80.0])
        self.assertEqual(st.setup_parts(90.0, 23, dates, ts), (False, True))    # below SMA20, up over 3 days
        dates, ts = _ts([90.0] * 20 + [120.0, 120.0, 120.0, 120.0])
        self.assertEqual(st.setup_parts(110.0, 23, dates, ts), (True, False))   # above SMA20, down over 3 days


class TestGrouping(unittest.TestCase):
    # (date, eligible, setup, above_sma, up3, fwd20, fwd60, worst20)
    OBS = [("2024-01-02", True, True, True, True, 1.0, 2.0, -3.0),
           ("2024-01-02", True, False, False, True, 5.0, 9.0, -6.0),
           ("2024-01-02", True, False, True, False, 3.0, 4.0, -2.0),
           ("2024-01-02", False, False, False, False, 50.0, 50.0, 0.0),     # not eligible
           ("2024-01-03", True, True, True, True, 2.0, None, -1.0),
           ("2024-01-03", True, False, False, False, 0.0, None, -9.0)]

    def test_groups(self):
        self.assertEqual([st.group_of(o) for o in self.OBS],
                         ["SETUP1", "FAIL_SMA", "FAIL_DIR", "FAIL_BOTH", "SETUP1", "FAIL_BOTH"])

    def test_per_day_eligible_only_and_setup0_pools_the_parts(self):
        pd_ = st.per_day(self.OBS, 6, (-100, 100))
        self.assertEqual(pd_["2024-01-02"]["SETUP0"], [9.0, 4.0])     # ineligible 50% left out
        self.assertNotIn("2024-01-03", pd_)                             # no 60-day returns
        r = st.clustered(st.per_day(self.OBS, 5, (-100, 4.0)), "SETUP0", "SETUP1", lag=0)
        # 01-02: mean(4 clipped, 3) - 1 = 2.5 ; 01-03: 0 - 2 = -2 -> 0.25
        self.assertEqual((r["days"], r["mean_diff"]), (2, 0.25))


class TestVerdict(unittest.TestCase):
    def test_table(self):
        ok = {"SETUP0": -5.0, "SETUP1": -4.0}
        cases = [
            ({"mean_diff": 1.0, "t_hac": 2.5}, {"mean_diff": 0.2}, ok, "SETUP_COSTS"),
            ({"mean_diff": 1.0, "t_hac": 2.5}, {"mean_diff": -0.2}, ok, "INCONCLUSIVE"),       # 20d disagrees
            ({"mean_diff": 1.0, "t_hac": 2.5}, {"mean_diff": 0.2},
             {"SETUP0": -7.0, "SETUP1": -4.0}, "INCONCLUSIVE"),                                # deeper dips
            ({"mean_diff": 1.0, "t_hac": 1.0}, {"mean_diff": 0.2}, ok, "INCONCLUSIVE"),
            ({"mean_diff": -1.0, "t_hac": -2.5}, {"mean_diff": -0.2}, ok, "SETUP_HELPS"),
            ({"mean_diff": None, "t_hac": None}, {}, ok, "INCONCLUSIVE"),
        ]
        for p60, p20, worst, want in cases:
            with self.subTest(p60=p60, p20=p20, worst=worst):
                self.assertEqual(st.verdict(p60, p20, worst), want)

class TestEligibilityAndForwardStats(unittest.TestCase):
    def test_eligible_needs_floor_and_combined(self):
        self.assertTrue(st.eligible(2.0, 3.0))
        self.assertFalse(st.eligible(1.9, 9.0))      # under the S10 floor
        self.assertFalse(st.eligible(2.0, 2.9))      # combined 4.9 < 5

    def test_forward_stats_windows(self):
        closes = [100.0 + k for k in range(70)]      # rising, except one dip at day 30
        closes[29] = 50.0                             # inside 60 days, outside 20
        f20, f60, worst20 = st.forward_stats(100.0, closes)
        self.assertAlmostEqual(f20, 19.0)             # closes[19] = 119
        self.assertAlmostEqual(f60, 59.0)
        self.assertAlmostEqual(worst20, 0.0)          # min of the first 20 = 100; the dip is later
        self.assertIsNone(st.forward_stats(100.0, closes[:30])[1])

    def test_summarize_uses_horizon_minus_one_as_hac_lag(self):
        obs = [(f"2024-01-{d:02d}", True, d % 2 == 0, True, True, float(d), float(d), -1.0) for d in range(1, 29)]
        lags = []
        real = st.sg._t_hac
        with mock.patch.object(st.sg, "_t_hac", side_effect=lambda xs, lag: lags.append(lag) or real(xs, lag)):
            st.summarize(obs)
        self.assertIn(19, lags)
        self.assertIn(59, lags)
        self.assertNotIn(0, lags)


if __name__ == "__main__":
    unittest.main()
