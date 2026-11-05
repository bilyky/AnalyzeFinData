"""Tests for scripts/backtesting/insider_buy_study.py: parsing Form 4 purchases, event
aggregation and spacing, no look-ahead in the outcome window, the Newey-West t, and the
pre-registered verdict (with fixtures where the statistics disagree)."""
import datetime
import io
import os
import sys
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import insider_buy_study as ib  # noqa: E402

SUB = "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERTRADINGSYMBOL\n"
OWN = "ACCESSION_NUMBER\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
TRN = "ACCESSION_NUMBER\tTRANS_CODE\tTRANS_ACQUIRED_DISP_CD\tTRANS_SHARES\tTRANS_PRICEPERSHARE\n"


def _zip(sub, own, trn):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", SUB + sub)
        z.writestr("REPORTINGOWNER.tsv", OWN + own)
        z.writestr("NONDERIV_TRANS.tsv", TRN + trn)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def _bars(closes, start=datetime.date(2024, 1, 1)):
    days = [(start + datetime.timedelta(days=k)).isoformat() for k in range(len(closes))]
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": list(closes), "o": list(closes),
            "h": list(closes), "l": list(closes), "v": [1.0] * len(closes)}


class TestParse(unittest.TestCase):
    def test_only_insider_open_market_purchases(self):
        z = _zip("A1\t02-NOV-2026\t4\tintc\nA2\t02-NOV-2026\t4\tABC\nA3\t02-NOV-2026\t4/A\tXYZ\nA4\t03-NOV-2026\t4\tDEF\n",
                 "A1\tDirector,Officer\tCEO\nA2\tTenPercentOwner\t\nA3\tDirector\t\nA4\tOfficer\tCFO\n",
                 "A1\tP\tA\t150000\t64.73\nA2\tP\tA\t1000\t10\nA3\tP\tA\t5\t5\nA4\tS\tD\t100\t9\nA4\tP\tA\t0\t9\n")
        out = ib.parse_purchases(z)
        self.assertEqual(len(out), 1)                                  # 10% owner, 4/A, sale, zero shares out
        self.assertEqual((out[0]["symbol"], out[0]["filed"], out[0]["ceo"]), ("INTC", "2026-11-02", True))
        self.assertAlmostEqual(out[0]["value"], 150000 * 64.73)

    def test_events_sum_per_day_and_need_min_value(self):
        p = [{"symbol": "A", "filed": "2026-01-02", "value": 60_000, "ceo": False},
             {"symbol": "A", "filed": "2026-01-02", "value": 50_000, "ceo": True},
             {"symbol": "B", "filed": "2026-01-02", "value": 99_999, "ceo": False}]
        ev = ib.events_from(p)
        self.assertEqual([(e["symbol"], round(e["value"]), e["ceo"]) for e in ev], [("A", 110_000, True)])

    def test_quarter_names(self):
        self.assertEqual(ib.quarter_names("2025q3", "2026q2"), ["2025q3", "2025q4", "2026q1", "2026q2"])


class TestTiming(unittest.TestCase):
    def test_decision_day_is_last_session_up_to_filing(self):
        b = _bars([10.0] * 5)                                          # 2024-01-01 .. 01-05
        self.assertEqual(ib.decision_index(b, "2024-01-03"), 2)
        self.assertEqual(ib.decision_index(b, "2024-01-03T"), 2)
        self.assertEqual(ib.decision_index(b, "2023-12-31"), None)

    def test_outcome_starts_after_the_filing_session(self):
        b = _bars([10.0, 20.0, 10.0, 11.0, 12.1])                      # a jump ON the decision day is not counted
        idx = {d: 0.0 for d in b["days"]}
        self.assertAlmostEqual(ib.outcome(b, idx, 1, 2), 12.1 / 10.0 - 1)
        idx[b["days"][3]] = 0.10                                         # the universe's move is removed
        self.assertAlmostEqual(ib.outcome(b, idx, 1, 2), (12.1 / 10.0 - 1) - 0.10)
        self.assertIsNone(ib.outcome(b, idx, 1, 3))                       # window past the data

    def test_build_rows_spacing_and_universe(self):
        b = _bars([10.0] * 100)
        ev = [{"symbol": "A", "filed": b["days"][5], "value": 2e5, "ceo": False},
              {"symbol": "A", "filed": b["days"][15], "value": 2e5, "ceo": False},   # < SPACING later: dropped
              {"symbol": "A", "filed": b["days"][30], "value": 2e5, "ceo": False},
              {"symbol": "ZZ", "filed": b["days"][5], "value": 2e5, "ceo": False}]  # not in universe
        with mock.patch.object(ib, "trim_setup", return_value=False):
            rows = ib.build_rows(ev, {"A": b}, {d: 0.0 for d in b["days"]})
        self.assertEqual([r["decision_day"] for r in rows], [b["days"][5], b["days"][30]])

    def test_trim_setup_uses_position_watch_rules(self):
        import numpy as np
        rng = np.random.default_rng(4)
        r = list(rng.normal(0, 0.01, 280)) + [-0.006] * 20               # 20 days weak on its own
        closes = list(100 * np.exp(np.cumsum(r)))
        b = _bars(closes)
        flat = {d: 0.0 for d in b["days"]}                               # the basket did nothing
        self.assertTrue(ib.trim_setup(b, flat, len(closes) - 1))
        up = _bars(closes[:-1] + [closes[-1] * 1.30])                     # back above the 50-day
        self.assertFalse(ib.trim_setup(up, flat, len(closes) - 1))
        crash = _bars(closes[:-1] + [closes[-2] * 0.90])                  # a crash day: no trim
        self.assertFalse(ib.trim_setup(crash, flat, len(closes) - 1))
        self.assertFalse(ib.trim_setup(b, flat, 100))                     # too little history
        same = {d: closes[k] / closes[k - 1] - 1 for k, d in enumerate(b["days"]) if k}   # the market fell too
        self.assertFalse(ib.trim_setup(b, same, len(closes) - 1))

    def test_ew_index_caps_daily_returns(self):
        idx = ib.ew_index({"A": _bars([10.0, 30.0]), "B": _bars([10.0, 10.0])})
        self.assertAlmostEqual(idx["2024-01-02"], (0.5 + 0.0) / 2)


class TestStatsAndVerdict(unittest.TestCase):
    def test_nw_t(self):
        self.assertIsNone(ib.nw_t([1.0, 2.0], 0))
        xs = [0.01, 0.03, -0.01, 0.02, 0.04, 0.0]
        m = sum(xs) / 6
        var = sum((x - m) ** 2 for x in xs) / 6
        self.assertAlmostEqual(ib.nw_t(xs, 0), m / (var / 6) ** 0.5)
        self.assertNotAlmostEqual(ib.nw_t(xs, 2), ib.nw_t(xs, 0))

    def test_summarize_groups_by_month(self):
        rows = [{"filed": "2026-01-05", "x": 0.1}, {"filed": "2026-01-20", "x": 0.3},
                {"filed": "2026-02-03", "x": -0.1}, {"filed": "2026-03-03", "x": 0.2, }]
        s = ib.summarize(rows, "x", 0)
        self.assertEqual((s["n"], s["months"]), (4, 3))
        self.assertAlmostEqual(s["mean"], 0.125)

    def test_verdict_branches(self):
        good = {"n": 500, "mean": 0.02, "t_nw": 2.5}
        self.assertEqual(ib.verdict(good, {"n": 40, "mean": 0.01}), "PAUSE_SUPPORTED")
        self.assertEqual(ib.verdict(good, {"n": 20, "mean": 0.05}), "EDGE_NOT_IN_TRIM_SETUP")   # subset too small
        self.assertEqual(ib.verdict(good, {"n": 40, "mean": -0.01}), "EDGE_NOT_IN_TRIM_SETUP")
        self.assertEqual(ib.verdict({"n": 500, "mean": 0.03, "t_nw": 1.5}, {"n": 40, "mean": 0.1}), "INCONCLUSIVE")
        self.assertEqual(ib.verdict({"n": 500, "mean": -0.02, "t_nw": -2.2}, {"n": 40, "mean": 0.1}), "NO_EDGE")
        # positive t but negative mean can't happen in practice; a big mean alone doesn't pass:
        self.assertEqual(ib.verdict({"n": 9, "mean": 0.2, "t_nw": None}, {"n": 40, "mean": 0.1}), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
