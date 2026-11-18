"""Tests for scripts/backtesting/beaten_quality_study.py (R&D #91): point-in-time EDGAR quality
(filing-date filter, annual period selection, staleness), the chart PGR labels, the 60-session
outcome from the next close, the month-paired difference and the verdict."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import beaten_quality_study as bq  # noqa: E402


def fact(start, end, filed, val):
    return {"start": start, "end": end, "filed": filed, "val": val}


def facts(ni=(), rev=()):
    return {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": list(ni)}},
                                  "Revenues": {"units": {"USD": list(rev)}}}}}


GOOD = facts(ni=[fact("2023-01-01", "2023-12-31", "2024-02-15", 100.0)],
             rev=[fact("2023-07-01", "2023-09-30", "2023-11-01", 50.0),
                  fact("2024-01-01", "2024-03-31", "2024-05-01", 60.0),
                  fact("2023-01-01", "2023-03-31", "2023-05-01", 40.0)])


class TestEdgar(unittest.TestCase):
    def test_good(self):
        self.assertEqual(bq.quality_edgar(GOOD, "2024-06-01"), "good")      # NI > 0, revenue +50% YoY

    def test_not_filed_yet_is_unknown(self):
        # on 2024-02-01 the FY2023 annual had ended but was not filed (2024-02-15)
        self.assertIsNone(bq.quality_edgar(GOOD, "2024-02-01"))

    def test_quarterly_net_income_is_not_annual(self):
        f = facts(ni=[fact("2024-01-01", "2024-03-31", "2024-05-01", 5.0)])
        self.assertEqual(bq.annual_net_income(f, "2024-06-01"), (None, None))

    def test_restatement_filed_later_is_ignored_then_used(self):
        f = facts(ni=[fact("2023-01-01", "2023-12-31", "2024-02-15", 100.0),
                      fact("2023-01-01", "2023-12-31", "2024-09-01", -5.0)])     # restated to a loss
        self.assertEqual(bq.annual_net_income(f, "2024-06-01")[1], 100.0)
        self.assertEqual(bq.annual_net_income(f, "2024-10-01")[1], -5.0)

    def test_junk_mid_and_stale(self):
        junk = facts(ni=[fact("2023-01-01", "2023-12-31", "2024-02-15", -1.0)],
                     rev=[fact("2024-01-01", "2024-03-31", "2024-05-01", 30.0),
                          fact("2023-01-01", "2023-03-31", "2023-05-01", 40.0)])
        self.assertEqual(bq.quality_edgar(junk, "2024-06-01"), "junk")
        mid = facts(ni=[fact("2023-01-01", "2023-12-31", "2024-02-15", 1.0)],
                    rev=[fact("2024-01-01", "2024-03-31", "2024-05-01", 30.0),
                         fact("2023-01-01", "2023-03-31", "2023-05-01", 40.0)])
        self.assertEqual(bq.quality_edgar(mid, "2024-06-01"), "mid")
        self.assertIsNone(bq.quality_edgar(GOOD, "2025-03-01"))              # last quarter 334 days old


class TestChart(unittest.TestCase):
    ROWS = {"2025-01-02": {"dailyPgr": 6}, "2025-01-03": {"dailyPgr": 2}, "2025-01-06": {"dailyPgr": 4},
            "2025-01-07": {"dailyPgr": -1}, "2025-01-08": {"dailyPgr": None}}

    def test_labels(self):
        self.assertEqual(bq.quality_chart(self.ROWS, "2025-01-02"), "good")
        self.assertEqual(bq.quality_chart(self.ROWS, "2025-01-03"), "junk")
        self.assertEqual(bq.quality_chart(self.ROWS, "2025-01-06"), "mid")
        self.assertEqual(bq.quality_chart({"2025-01-02": {"dailyPgr": 5}}, "2025-01-02"), "mid")   # 5 is not good
        self.assertEqual(bq.quality_chart(self.ROWS, "2025-01-05"), "junk")   # last row on/before (Friday)
        self.assertIsNone(bq.quality_chart(self.ROWS, "2025-01-07"))          # -1 is not a rating
        self.assertIsNone(bq.quality_chart(self.ROWS, "2025-01-08"))
        self.assertIsNone(bq.quality_chart(self.ROWS, "2024-12-31"))          # nothing before
        self.assertIsNone(bq.quality_chart({"2025-01-02": {"dailyPgr": 6}}, "2025-01-20"))  # too old


class TestOutcome(unittest.TestCase):
    def test_from_next_close(self):
        c = [100.0] * 5 + [80.0, 50.0] + [65.0] * 70
        days = [f"2015-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(len(c))]
        b = {"c": c, "days": days}
        o = bq.outcome60(b, 5, {"2010-2022": 0.1})
        self.assertAlmostEqual(o["r60"], 30.0)                                   # 50 -> 65, not 80 -> 65
        self.assertAlmostEqual(o["ex60"], 20.0)
        self.assertEqual(o["win60"], 1.0)
        self.assertIsNone(bq.outcome60(b, 20, {}))                                # 60 bars ahead missing


class TestStats(unittest.TestCase):
    def ev(self, month, label, x):
        return {"date": f"2015-{month:02d}-10", "q": label, "ex60": x}

    def test_monthly_diff_pairs_months(self):
        ev = [self.ev(1, "good", 5.0), self.ev(1, "junk", 1.0), self.ev(2, "good", 3.0),
              self.ev(2, "junk", 2.0), self.ev(3, "good", 9.0), self.ev(3, "mid", 0.0)]
        out = bq.monthly_diff(ev, "q", "ex60", 0, bounds=(-100, 100))
        self.assertEqual(out["months"], 2)                                        # month 3 has no junk
        self.assertAlmostEqual(out["diff"], 2.5)
        self.assertEqual((out["n_good"], out["n_junk"]), (3, 2))

    def test_verdict(self):
        full = {"months": 60, "n_good": 500, "n_junk": 300, "t_hac": 2.5, "diff": 3.0}
        self.assertEqual(bq.verdict(full, {"diff": 1.0}, {"diff": 2.0}), "QUALITY_HELPS")
        self.assertEqual(bq.verdict(full, {"diff": -1.0}, {"diff": 2.0}), "INCONCLUSIVE")
        self.assertEqual(bq.verdict(dict(full, t_hac=-2.5), {}, {}), "QUALITY_HURTS")
        self.assertEqual(bq.verdict(dict(full, t_hac=1.5), {"diff": 1}, {"diff": 1}), "INCONCLUSIVE")
        self.assertEqual(bq.verdict(dict(full, n_junk=50), {"diff": 1}, {"diff": 1}), "INSUFFICIENT")
        self.assertEqual(bq.verdict(dict(full, months=10), {"diff": 1}, {"diff": 1}), "INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
