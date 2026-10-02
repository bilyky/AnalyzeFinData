"""
Tests for the AI-buildout supply-chain watchlist (aether/ai_buildout.py).

Pure functions over fixture SEC / OHLCV JSON; no network. Covers theme bucketing,
quarterly revenue YoY (tag choice, restatements, look-ahead), RPO YoY, 8-K item
1.01 counting, placeholder-bar skipping, CMF, and the watch-score ranking.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aether import ai_buildout as ab


def _q(start, end, val, filed=None):
    return {"start": start, "end": end, "val": val, "filed": filed or end, "form": "10-Q"}


def _facts(tags):
    return {"facts": {"us-gaap": {t: {"units": {"USD": v}} for t, v in tags.items()}}}


def _bar(c, h=None, l=None, v=1000, **extra):
    h = c + 1 if h is None else h
    l = c - 1 if l is None else l
    return {"1. open": str(c), "2. high": str(h), "3. low": str(l),
            "4. close": str(c), "5. volume": str(v), **extra}


class TestBucket(unittest.TestCase):
    def test_seed_wins_over_sic(self):
        self.assertEqual(ab.bucket_for("sei", sic=1389), "power")
        self.assertEqual(ab.bucket_for("VRT", sic=4911), "cooling")

    def test_sic_mapping_and_unknowns(self):
        self.assertEqual(ab.bucket_for("XYZ", sic="4911"), "power")
        self.assertEqual(ab.bucket_for("XYZ", sic=1731), "construction")
        self.assertIsNone(ab.bucket_for("XYZ", sic=7372))
        self.assertIsNone(ab.bucket_for("TECK", sic=1400))   # mining: too broad
        self.assertIsNone(ab.bucket_for("RUN", sic=1700))    # general trades: too broad
        self.assertIsNone(ab.bucket_for("XYZ", sic=None))
        self.assertIsNone(ab.bucket_for("XYZ", sic="n/a"))


class TestRevenueYoy(unittest.TestCase):
    def test_quarterly_yoy_ignores_ytd_durations(self):
        facts = _facts({"Revenues": [
            _q("2025-04-01", "2025-06-30", 100),
            _q("2026-01-01", "2026-06-30", 999),   # 6-month YTD, must be ignored
            _q("2026-04-01", "2026-06-30", 150),
        ]})
        self.assertEqual(ab.revenue_yoy(facts), ("2026-06-30", 50.0))

    def test_picks_tag_with_latest_quarter(self):
        facts = _facts({
            "Revenues": [_q("2018-10-01", "2018-12-31", 5), _q("2019-10-01", "2019-12-31", 6)],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                _q("2025-04-01", "2025-06-30", 200), _q("2026-04-01", "2026-06-30", 180)],
        })
        self.assertEqual(ab.revenue_yoy(facts), ("2026-06-30", -10.0))

    def test_restatement_uses_latest_filed(self):
        facts = _facts({"Revenues": [
            _q("2025-04-01", "2025-06-30", 100, filed="2025-08-01"),
            _q("2025-04-01", "2025-06-30", 120, filed="2026-08-01"),   # restated
            _q("2026-04-01", "2026-06-30", 150, filed="2026-08-01"),
        ]})
        self.assertEqual(ab.revenue_yoy(facts)[1], 25.0)

    def test_no_look_ahead(self):
        facts = _facts({"Revenues": [
            _q("2025-01-01", "2025-03-31", 100), _q("2026-01-01", "2026-03-31", 110),
            _q("2026-04-01", "2026-06-30", 500),
        ]})
        self.assertEqual(ab.revenue_yoy(facts, as_of="2026-05-15"), ("2026-03-31", 10.0))

    def test_missing_year_ago_gives_none(self):
        facts = _facts({"Revenues": [_q("2026-04-01", "2026-06-30", 150)]})
        self.assertEqual(ab.revenue_yoy(facts), ("2026-06-30", None))
        self.assertEqual(ab.revenue_yoy({}), (None, None))


class TestRpoYoy(unittest.TestCase):
    def test_instant_yoy_skips_zero(self):
        facts = _facts({"RevenueRemainingPerformanceObligation": [
            {"end": "2019-12-31", "val": 0, "filed": "2020-02-01"},
            {"end": "2025-06-30", "val": 20e9, "filed": "2025-08-01"},
            {"end": "2026-06-30", "val": 30e9, "filed": "2026-08-01"},
        ]})
        self.assertEqual(ab.rpo_yoy(facts), ("2026-06-30", 50.0))
        self.assertEqual(ab.rpo_yoy({}), (None, None))


class TestMaterialAgreements(unittest.TestCase):
    SUB = {"filings": {"recent": {
        "form": ["8-K", "8-K", "10-Q", "8-K", "8-K"],
        "filingDate": ["2026-10-01", "2026-09-22", "2026-08-05", "2026-09-08", "2026-05-01"],
        "items": ["1.01,2.03,9.01", "7.01,8.01", "", "1.01,2.01", "1.01"],
    }}}

    def test_counts_item_101_in_window(self):
        self.assertEqual(ab.material_agreements(self.SUB, "2026-10-02"),
                         (2, ["2026-10-01", "2026-09-08"]))

    def test_excludes_future_filings(self):
        self.assertEqual(ab.material_agreements(self.SUB, "2026-09-30")[0], 1)

    def test_item_match_is_exact(self):
        sub = {"filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-09-01"],
                                      "items": ["11.01"]}}}
        self.assertEqual(ab.material_agreements(sub, "2026-10-01")[0], 0)


class TestPriceSignals(unittest.TestCase):
    def test_real_bars_skip_placeholders_and_future(self):
        ts = {
            "2026-09-24": _bar(100),
            "2026-09-25": _bar(101, v=0),                   # zero volume
            "2026-09-26": _bar(102, h=102, l=102),          # flat
            "2026-09-27": _bar(103, provisional=True),      # provisional
            "2026-09-28": _bar(104),
            "2026-09-29": _bar(105),                        # after as_of
        }
        bars = ab._real_bars(ts, "2026-09-28")
        self.assertEqual([b[0] for b in bars], ["2026-09-24", "2026-09-28"])

    def test_cmf_bounds(self):
        up = [("d", 11.0, 9.0, 11.0, 100.0)] * 20     # closes at the high
        down = [("d", 11.0, 9.0, 9.0, 100.0)] * 20    # closes at the low
        self.assertEqual(ab.chaikin_money_flow(up), 1.0)
        self.assertEqual(ab.chaikin_money_flow(down), -1.0)
        self.assertIsNone(ab.chaikin_money_flow(up[:5]))

    def test_return_pct(self):
        bars = [("d", 0, 0, 100.0, 1)] + [("d", 0, 0, 110.0, 1)] * 60
        self.assertAlmostEqual(ab.return_pct(bars, 60), 10.0)
        self.assertIsNone(ab.return_pct(bars[:10], 60))


class TestWatchScore(unittest.TestCase):
    def test_strong_and_weak(self):
        strong = {"revenue_yoy": 40, "rpo_yoy": 30, "agreements_90d": 3,
                  "rs_60d": 15, "cmf_20": 0.2}
        weak = {"revenue_yoy": -5, "rpo_yoy": -2, "agreements_90d": 0,
                "rs_60d": -8, "cmf_20": -0.2}
        self.assertEqual(ab.watch_score(strong), 9)
        self.assertEqual(ab.watch_score(weak), -4)

    def test_suspect_rpo_jump_not_scored(self):
        base = {"revenue_yoy": None, "agreements_90d": 0, "rs_60d": None, "cmf_20": None}
        self.assertEqual(ab.watch_score({**base, "rpo_yoy": 3585.7}), 0)
        self.assertEqual(ab.watch_score({**base, "rpo_yoy": 75.0}), 2)

    def test_missing_data_is_neutral(self):
        self.assertEqual(ab.watch_score({"revenue_yoy": None, "rpo_yoy": None,
                                         "agreements_90d": 0, "rs_60d": None,
                                         "cmf_20": None}), 0)


class TestScan(unittest.TestCase):
    def test_scan_classifies_and_ranks(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Symbol_full").mkdir()
            (Path(d) / "symbols_to_check.txt").write_text(
                "14\t14\tVRT\n14\t14\tMSFT\n14\t14\tUTIL\n14\t14\tGONE\n14\t14\tSPY\n", encoding="utf-8")
            subs = {1: {"sic": 3585, "name": "Vertiv"}, 2: {"sic": 7372, "name": "Microsoft"},
                    3: {"sic": 4911, "name": "Some Utility"}}
            cik = {"VRT": 1, "MSFT": 2, "UTIL": 3, "GONE": 4}   # 4: fetch fails
            facts = {1: _facts({"Revenues": [_q("2025-04-01", "2025-06-30", 100),
                                             _q("2026-04-01", "2026-06-30", 140)]}),
                     3: {}}
            with mock.patch.dict(os.environ, {"AETHER_DATA_DIR": d}), \
                 mock.patch.object(ab, "ticker_cik_map", return_value=cik), \
                 mock.patch.object(ab, "submissions", side_effect=subs.get), \
                 mock.patch.object(ab, "company_facts", side_effect=facts.get):
                failed = []
                rows = ab.scan("2026-10-02", universe=ab.load_universe(), failed=failed)
                out = ab.save(rows, "2026-10-02", failed=failed)
                saved = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([r["symbol"] for r in rows], ["VRT", "UTIL"])   # MSFT not themed
        self.assertEqual(rows[0]["revenue_yoy"], 40.0)
        self.assertEqual(rows[0]["watch_score"], 2)
        self.assertEqual(saved["as_of"], "2026-10-02")
        self.assertEqual(saved["fetch_failed"], ["GONE"])

    def test_scan_raises_without_ticker_map(self):
        with mock.patch.object(ab, "ticker_cik_map", return_value={}):
            with self.assertRaises(RuntimeError):
                ab.scan("2026-10-02", universe=["VRT"])


if __name__ == "__main__":
    unittest.main()
