"""Setup x S10 gate study: grouping, the date-clustered comparison, and the shared
compute_setup_ok formula the study and backtest_ratings both use."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import backtest_ratings as br
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


if __name__ == "__main__":
    unittest.main()
