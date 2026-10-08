"""
Tests for scripts/backtesting/theme_signal_study.py (R&D #51): month-end dates,
forward excess returns, 8-K coverage, and the per-date spread test / gate.
Pure functions over in-memory data; no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtesting import theme_signal_study as st


class TestHelpers(unittest.TestCase):
    def test_month_end_dates(self):
        self.assertEqual(st.month_end_dates("2024-01", "2024-03"),
                         ["2024-01-31", "2024-02-29", "2024-03-31"])
        self.assertEqual(st.month_end_dates("2025-12", "2026-01"),
                         ["2025-12-31", "2026-01-31"])

    def test_fwd_excess(self):
        bars = [("d0", 0, 0, 100.0, 1), ("d1", 0, 0, 110.0, 1), ("d2", 0, 0, 121.0, 1)]
        dates = [b[0] for b in bars]
        spy = {"d0": 100.0, "d1": 105.0, "d2": 110.0}
        self.assertAlmostEqual(st.fwd_excess(bars, dates, spy, 0, 2), 11.0)   # 21% - 10%
        self.assertIsNone(st.fwd_excess(bars, dates, spy, 1, 2))              # past the end
        self.assertIsNone(st.fwd_excess(bars, dates, {"d0": 1.0}, 0, 1))     # no SPY bar

    def test_agreements_covered(self):
        sub = {"filings": {"recent": {"filingDate": ["2026-09-01", "2026-03-01"]}}}
        self.assertTrue(st.agreements_covered(sub, "2026-06-30"))    # list reaches 4/1
        self.assertFalse(st.agreements_covered(sub, "2026-04-15"))   # window starts 1/15
        self.assertFalse(st.agreements_covered({}, "2026-06-30"))


def _rec(date, side_value, fwd):
    return {"date": date, "v": side_value, "fwd20": fwd, "fwd60": fwd}


class TestSpread(unittest.TestCase):
    def side(self, rec):
        return rec["v"]

    def test_spread_and_gate(self):
        obs = []
        for k in range(40):   # 40 dates, high beats low by 2 points with some noise
            d = f"2020-{k:02d}"
            noise = (k % 5 - 2) * 0.3
            obs += [_rec(d, "high", 3.0 + noise) for _ in range(5)]
            obs += [_rec(d, "low", 1.0) for _ in range(5)]
        r = st.spread_test(obs, self.side, 20)
        self.assertEqual(r["dates"], 40)
        self.assertEqual((r["n_high"], r["n_low"]), (200, 200))
        self.assertAlmostEqual(r["spread"], 2.0, places=2)
        self.assertTrue(r["passes"])
        self.assertEqual(st.spread_test(obs, self.side, 60)["hac_lag"], 2)

    def test_thin_dates_dropped_and_no_signal_fails(self):
        obs = [_rec("2020-01", "high", 5.0) for _ in range(4)]   # < 5 per side
        obs += [_rec("2020-01", "low", 0.0) for _ in range(10)]
        r = st.spread_test(obs, self.side, 20)
        self.assertEqual(r["dates"], 0)
        self.assertFalse(r["passes"])
        flat = []
        for k in range(40):
            flat += [_rec(f"d{k}", "high", float(k % 3)) for _ in range(5)]
            flat += [_rec(f"d{k}", "low", float(k % 3)) for _ in range(5)]
        self.assertFalse(st.spread_test(flat, self.side, 20)["passes"])


class TestWinsorize(unittest.TestCase):
    def test_clips_split_artifacts(self):
        obs = [{"fwd20": float(v), "fwd60": None} for v in range(-50, 51)]
        obs.append({"fwd20": 17118.0, "fwd60": None})   # split artifact
        bounds = st.winsorize(obs, pct=(0.01, 0.99))
        self.assertEqual(bounds["20d"][:2], (-49.0, 49.0))
        self.assertEqual(max(o["fwd20"] for o in obs), 49.0)
        self.assertEqual(bounds["20d"][2], 3)            # -50, +50 and the artifact
        self.assertNotIn("60d", bounds)


class TestUsable(unittest.TestCase):
    def test_excludes_stale_and_suspect(self):
        row = {"revenue_yoy": 30.0, "rpo_yoy": 500.0, "stale": ["revenue_yoy"]}
        self.assertIsNone(st._usable(row, "revenue_yoy"))
        self.assertIsNone(st._usable(row, "rpo_yoy"))
        self.assertEqual(st._usable({"rpo_yoy": 40.0}, "rpo_yoy"), 40.0)


if __name__ == "__main__":
    unittest.main()
