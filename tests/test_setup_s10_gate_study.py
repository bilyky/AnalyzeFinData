"""Setup x S10 gate study: grouping, the date-clustered comparison, and the shared
compute_setup_ok formula the study and backtest_ratings both use."""
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import backtest_ratings as br
from aether import risk_utils
from aether.utils import _to_float
import setup_s10_gate_study as st


def _ts(closes):
    dates = [f"2026-01-{i + 1:02d}" for i in range(len(closes))]
    return dates, {d: {"4. close": str(c)} for d, c in zip(dates, closes, strict=True)}


class TestComputeSetupOk(unittest.TestCase):
    def test_above_sma20_and_above_3d_ago_passes(self):
        dates, ts = _ts([10.0] * 20 + [10.5, 10.6, 10.7, 11.0])
        self.assertTrue(br.compute_setup_ok(11.0, 23, dates, ts))

    def test_below_close_3d_ago_fails_even_above_sma20(self):
        dates, ts = _ts([10.0] * 20 + [12.0, 11.8, 11.6, 11.5])
        self.assertFalse(br.compute_setup_ok(11.5, 23, dates, ts))   # 11.5 < close[3d ago] 12.0

    def test_below_sma20_fails(self):
        dates, ts = _ts([12.0] * 20 + [9.0, 9.1, 9.2, 9.5])
        self.assertFalse(br.compute_setup_ok(9.5, 23, dates, ts))


class TestGrouping(unittest.TestCase):
    def test_groups(self):
        self.assertEqual(st._group(True, 2.0), "BOTH")
        self.assertEqual(st._group(False, 2.0), "S10_ONLY")
        self.assertEqual(st._group(True, 1.99), "SETUP_ONLY")
        self.assertEqual(st._group(False, -3.0), "NEITHER")


class TestDateClustered(unittest.TestCase):
    def test_uses_only_days_with_both_groups_and_per_day_means(self):
        per_day = {
            "d1": {"A": [3.0, 1.0], "B": [1.0]},     # diff 2 - 1 = 1
            "d2": {"A": [2.0], "B": [0.0, 0.0]},     # diff 2
            "d3": {"A": [5.0]},                      # no B -> excluded
            "d4": {"A": [4.0], "B": [1.0]},          # diff 3
        }
        r = st._clustered(per_day, "A", "B")
        self.assertEqual(r["days"], 3)
        self.assertAlmostEqual(r["mean_diff10"], 2.0)
        self.assertAlmostEqual(r["t"], 3.46, places=2)   # mean 2, sd 1, n 3 -> 2/(1/sqrt 3)


class TestVerdict(unittest.TestCase):
    def _obs(self, both_f10, s10_only_f10, days=30):
        out = []
        for i in range(days):
            day = f"2026-02-{i % 28 + 1:02d}-{i}"
            jitter = (i % 5) * 0.1
            noise = ((i * 7) % 3 - 1) * 0.3   # varies the per-day difference so t is defined
            out += [(day, True, 3.0, 6.0, both_f10 + jitter, 0.0),
                    (day, False, 3.0, 6.0, s10_only_f10 + jitter + noise, 0.0),
                    (day, False, 0.0, 6.0, 0.0 + jitter, 0.0)]
        return out

    def test_excluded_group_much_worse_keeps_the_gate(self):
        self.assertEqual(st.summarize(self._obs(2.0, -2.0), set())["ALL"]["verdict"], "KEEP")

    def test_excluded_group_equal_and_beating_base_is_too_strict(self):
        self.assertEqual(st.summarize(self._obs(2.0, 2.0), set())["ALL"]["verdict"], "TOO_STRICT")

    def test_below_min_combined_is_ignored(self):
        obs = [("d", False, 5.0, 4.9, 9.0, 0.0)]
        self.assertNotIn("S10_ONLY", st.summarize(obs, set())["ALL"]["groups"])

    def _verdict_with(self, t, t_hac):
        fake = {"days": 50, "mean_diff10": 0.5, "t": t, "t_hac": t_hac,
                "t_nonoverlap_min": t, "t_nonoverlap_median": t}
        with mock.patch.object(st, "_clustered", return_value=fake):
            return st.summarize(self._obs(2.0, 2.0), set())["ALL"]["verdict"]

    def test_verdict_decides_on_hac_not_the_overlapping_t(self):
        # Overlapping t says "significant", HAC says not -> must be INCONCLUSIVE.
        self.assertEqual(self._verdict_with(t=5.0, t_hac=1.0), "INCONCLUSIVE")
        self.assertEqual(self._verdict_with(t=1.0, t_hac=5.0), "TOO_STRICT")
        self.assertEqual(self._verdict_with(t=1.0, t_hac=-3.0), "KEEP")


class TestHac(unittest.TestCase):
    def test_lag0_is_the_plain_t_with_population_variance(self):
        xs = [0.5, -0.2, 1.3, 0.8, -0.4, 0.9, 0.1, 1.1, -0.6, 0.7]
        n = len(xs)
        self.assertAlmostEqual(st._t_hac(xs, 0), st._t_one_sample(xs) * (n / (n - 1)) ** 0.5)

    def test_overlap_shrinks_t(self):
        rnd = random.Random(7)
        base = [rnd.gauss(0.3, 1.0) for _ in range(40)]
        overlapped = [v for v in base for _ in range(10)]    # each value repeated, like overlapping windows
        self.assertGreater(st._t_one_sample(overlapped), 2 * st._t_hac(overlapped, 9))

    def test_too_short_is_none(self):
        self.assertIsNone(st._t_hac([1.0, 2.0, 3.0], 9))


class TestNonOverlapSplit(unittest.TestCase):
    def test_every_tenth_day_offsets(self):
        # 30 days; diff on day i is 1.0 + (i % 10) * 0.1, so every offset k sees a constant
        # series except for a tiny ramp -> each offset's t exists and the median offset is k=5.
        per_day = {f"d{i:02d}": {"A": [1.0 + (i % 10) * 0.1 + i * 1e-3], "B": [0.0]} for i in range(30)}
        r = st._clustered(per_day, "A", "B")
        self.assertEqual(r["days"], 30)
        self.assertIsNotNone(r["t_nonoverlap_min"])
        self.assertLessEqual(r["t_nonoverlap_min"], r["t_nonoverlap_median"])


def _old_powergauge_setup(price, idx, all_dates, ohlcv_ts, sma_period=20, dir_days=3):
    """Frozen copy of the inline powergauge block risk_utils.setup_ok replaced."""
    sma_w = all_dates[max(0, idx - sma_period): idx]
    if len(sma_w) >= sma_period:
        sma = sum(_to_float(ohlcv_ts[d].get('4. close'), 0) for d in sma_w) / len(sma_w)
        trend_ok = price > sma
    else:
        trend_ok = False
    dir_ok = price > _to_float(ohlcv_ts[all_dates[idx - dir_days]].get('4. close'), 0) if idx >= dir_days else False
    return trend_ok and dir_ok


class TestSetupParity(unittest.TestCase):
    def test_shared_helper_matches_the_old_production_block(self):
        rnd = random.Random(11)
        for _ in range(200):
            closes = [max(0.0, 50 + rnd.gauss(0, 5)) for _ in range(40)]
            dates, ts = _ts(closes)
            for idx in range(0, 40):
                price = closes[idx] * (1 + rnd.gauss(0, 0.02))
                self.assertEqual(risk_utils.setup_ok(price, idx, dates, ts),
                                 _old_powergauge_setup(price, idx, dates, ts))

    def test_backtest_uses_the_production_definition(self):
        dates, ts = _ts([10.0] * 20 + [10.5, 10.6, 10.7, 11.0])
        for price in (11.0, 10.4, 9.0):
            self.assertEqual(br.compute_setup_ok(price, 23, dates, ts), risk_utils.setup_ok(price, 23, dates, ts))


if __name__ == "__main__":
    unittest.main()
