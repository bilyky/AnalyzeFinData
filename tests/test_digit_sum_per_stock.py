"""Digit-sum per stock (R&D #72 follow-up): windows, the signed outcome, BH, the decision and the monthly skip."""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import digit_sum_per_stock as dp  # noqa: E402


class TestWindows(unittest.TestCase):
    def test_months_back(self):
        self.assertEqual(dp.months_back("2026-11-23", 12), "2025-11-23")
        self.assertEqual(dp.months_back("2025-11-23", 24), "2023-11-23")
        self.assertEqual(dp.months_back("2026-03-31", 1), "2026-02-28")
        self.assertEqual(dp.months_back("2026-01-15", 1), "2025-12-15")

    def test_first_run_windows(self):
        self.assertEqual(dp.windows("2026-11-23"),
                         {"as_of": "2026-11-23", "confirm_start": "2025-11-23", "discover_start": "2023-11-23"})


class TestOutcomes(unittest.TestCase):
    def test_signed_vs_same_period_base(self):
        # 4 sessions in the window, 3 up -> base 0.75; a bullish signal on an up day: +0.25,
        # a bearish one on the down day: -1 * (0 - 0.75) = +0.75; no signal -> skipped
        recs = [("d1", 10.0, 1, "2024-01-02"), ("d2", 20.0, 0, "2024-01-03"),
                ("d3", 30.0, 1, "2024-01-04"), ("d4", 40.0, 1, "2024-01-05"),
                ("d0", 50.0, 0, "2023-12-29")]                       # before the window
        score = {10.0: 0.5, 20.0: -0.4, 30.0: 0.0, 40.0: 0.0, 50.0: 1.0}
        out = dp.outcomes("X", recs, "2024-01-01", None, score=lambda _s, c: score[c])
        self.assertEqual(sorted(round(x, 4) for x in out), [0.25, 0.75])

    def test_end_is_exclusive(self):
        recs = [("d1", 10.0, 1, "2024-01-02"), ("d2", 10.0, 0, "2025-01-01")]
        out = dp.outcomes("X", recs, "2024-01-01", "2025-01-01", score=lambda _s, _c: 1.0)
        self.assertEqual(out, [0.0])                                   # one session, base 1.0


class TestStats(unittest.TestCase):
    def test_stats(self):
        st = dp.stats([1.0, 1.0, 1.0, -1.0])
        self.assertEqual(st["n"], 4)
        self.assertAlmostEqual(st["mean"], 0.5)
        self.assertAlmostEqual(st["t"], 1.0)
        self.assertAlmostEqual(st["p"], 0.158655, places=5)
        self.assertIsNone(dp.stats([0.2])["t"])

    def test_bh(self):
        p = {"A": 0.001, "B": 0.02, "C": 0.03, "D": 0.5}
        # m=4, q=0.1: thresholds .025 .05 .075 .1 -> A, B, C pass (C at .03 <= .075)
        self.assertEqual(dp.bh(p, 0.10), {"A", "B", "C"})
        self.assertEqual(dp.bh({"A": 0.04, "B": 0.5}, 0.10), {"A"})    # m=2: 0.04 <= 0.05
        self.assertEqual(dp.bh({"A": 0.06, "B": 0.5}, 0.10), set())
        self.assertEqual(dp.bh({}, 0.10), set())


class TestDecide(unittest.TestCase):
    def _st(self, n, mean, p):
        return {"n": n, "mean": mean, "t": None, "p": p}

    def test_needs_discovery_and_confirmation(self):
        disc = {"A": self._st(40, 0.1, 0.0001), "B": self._st(40, 0.1, 0.0002), "C": self._st(20, 0.2, 1e-9),
                "D": self._st(40, 0.0, 0.6)}
        conf = {"A": self._st(20, 0.05, 0.01), "B": self._st(20, -0.05, 0.9), "C": self._st(20, 0.1, 0.001)}
        r = dp.decide(disc, conf)
        self.assertEqual(r["tested"], 3)                               # C has too few discovery signals
        self.assertEqual(r["discovered"], ["A", "B"])
        self.assertEqual(r["allowlist"], ["A"])                        # B fails the confirmation year

    def test_confirmation_needs_enough_signals_and_p(self):
        disc = {"A": self._st(40, 0.1, 0.0001)}
        self.assertEqual(dp.decide(disc, {"A": self._st(10, 0.2, 0.001)})["allowlist"], [])
        self.assertEqual(dp.decide(disc, {"A": self._st(20, 0.02, 0.2)})["allowlist"], [])
        self.assertEqual(dp.decide(disc, {})["allowlist"], [])


class TestMonthly(unittest.TestCase):
    def test_is_stale(self):
        today = datetime.date(2026, 12, 26)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a.json")
            self.assertTrue(dp.is_stale(p, today))                    # missing
            with open(p, "w") as f:
                json.dump({"run_at": "2026-11-29T10:00:00"}, f)
            self.assertFalse(dp.is_stale(p, today))                   # 11-29 -> 12-26: 27 days
            self.assertTrue(dp.is_stale(p, datetime.date(2026, 12, 27)))  # 28 days
            with open(p, "w") as f:
                f.write("not json")
            self.assertTrue(dp.is_stale(p, today))


if __name__ == "__main__":
    unittest.main()
