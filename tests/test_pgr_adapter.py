"""
Unit tests for the new /api/suggestions -> legacy getSymbolData adapter in powergauge.

Chaikin migrated its data API to GET /api/suggestions/{symbol} (a flat dict); the rest
of powergauge still speaks the legacy {pgr[7], metaInfo[1], checklist_stocks{}} schema.
These tests pin the new->old mapping so a future API-shape change can't silently corrupt
ratings/prices. No network calls — the samples mirror verified live responses.

  python -m unittest discover -s tests -v
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import powergauge
from powergauge import (
    _PGR_FACTOR_MAP,
    _pgr_rating_old5,
    _adapt_suggestions_to_legacy,
    _pg_log,
    PowerGauge,
)


def _live_like_intc():
    """A trimmed copy of a verified live /api/suggestions/INTC `data` object."""
    return {
        "name": "Intel Corp",
        "rawPgrRating": 5, "correctedPgrRating": 5, "pgrRating": 5,
        "ratingName": "Neutral +", "rawPgr": "79.08",
        "days1ChangePct": -2.85, "days1Change": -2.62, "lastPrice": 89.72,
        "isEtf": False, "technicalRank": 0,
        "industry": "Semiconductors & Semiconductor Equipment", "sector": "Information Technology",
        "marketCap": 380000000000,
        "checklistData": {
            "industry": "Weak", "ltTrend": "Weak", "moneyFlow": "Weak",
            "OBOS": "Early", "relativeStrength": "Weak", "pgr": "Neutral +",
            "status": "Hold", "strengthCount": 0, "timingCount": 2,
            "sentence1": "This stock has <<0 of 3>>positive strength indicators.",
        },
        "signalInfo": {"moneyFlowSell": 1, "breakdownSell": 1},
        "pgrData": _live_pgr_data(),
    }


def _live_pgr_data():
    """pgrData from a live /api/suggestions/INTC response (2026-11-03)."""
    return {
        "pgrValue": 50, "correctedPgr": 50, "pgrRating": 4, "symbol": "INTC",
        "financials": {"financialStrength": 2, "debtEquityRatio": 3, "priceToBookValue": 3,
                       "returnOnInvestment": 4, "priceToSalesRatio": 3, "businessValue": 1},
        "earnings": {"earningsPerformance": 3, "epsSurprise": 5, "projectedPeRatio": 1,
                     "earningsTrend": 1, "earningsStability": 3, "earningsConsistency": 3},
        "technicals": {"priceVolumeActivity": 4, "priceTrend": 1, "priceTrendRoc": 1,
                       "relativeStrengthVsMarket": 2, "chaikinVolumeAccumulation": 4, "volumeTrend": 4},
        "experts": {"investorSentiment": 3, "earningEstimatesRevision": 1, "shortInterest": 5,
                    "insiderActivity": 5, "analystOptions": 1, "relativeStrengthVsIndustry": 3},
        "topFactors": [{"key": "moneyFlow", "value": "Strong", "rank": 1, "type": "Technicals"}],
    }


# ── _pgr_rating_old5: 7-level int / name -> legacy 5-level ─────────────────────

class TestPgrRatingOld5(unittest.TestCase):
    def test_int7_buckets_to_old5(self):
        # 1..7 collapse the Neutral -/·/+ band (4,5) down to old 3.
        expected = {1: 1, 2: 2, 3: 3, 4: 3, 5: 3, 6: 4, 7: 5}
        for new, old in expected.items():
            with self.subTest(new=new):
                self.assertEqual(_pgr_rating_old5(new, None), old)

    def test_name_fallback_when_int_missing(self):
        self.assertEqual(_pgr_rating_old5(None, "Very Bullish"), 5)
        self.assertEqual(_pgr_rating_old5(0, "Bearish"), 2)
        self.assertEqual(_pgr_rating_old5(None, "neutral +"), 3)  # case-insensitive

    def test_unrated_returns_zero(self):
        self.assertEqual(_pgr_rating_old5(None, None), 0)
        self.assertEqual(_pgr_rating_old5(0, ""), 0)
        self.assertEqual(_pgr_rating_old5(None, "not-a-rating"), 0)


# ── _adapt_suggestions_to_legacy: new dict -> legacy bundle ────────────────────

class TestAdaptSuggestions(unittest.TestCase):
    def test_valid_symbol_shape_and_rating(self):
        out = _adapt_suggestions_to_legacy(_live_like_intc(), "INTC")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["pgr"]), 7)               # legacy 7-slot list
        self.assertEqual(out["pgr"][0]["PGR Value"], 3)    # rawPgrRating 5 -> old 3
        self.assertEqual(out["pgr"][5]["Corrected PGR Value"], 3)
        meta = out["metaInfo"][0]
        self.assertEqual(meta["Last"], 89.72)
        self.assertEqual(meta["Percentage "], -2.85)       # trailing-space key preserved
        self.assertEqual(meta["Change"], -2.62)
        self.assertEqual(len(meta["signals"]), 12)         # fixed 12-char binary string

    def test_checklist_vocab_passthrough(self):
        cl = _adapt_suggestions_to_legacy(_live_like_intc(), "INTC")["checklist_stocks"]
        self.assertEqual(cl["moneyFlow"], "Weak")
        self.assertEqual(cl["overboughtOversold"], "Early")   # OBOS -> overboughtOversold
        self.assertEqual(cl["ltTrend"], "Weak")
        self.assertEqual(cl["relativeStrength"], "Weak")

    def test_strength_and_timing_counts_pass_through(self):
        # The live R&D #32 guard reads these (default 1 when missing).
        cl = _adapt_suggestions_to_legacy(_live_like_intc(), "INTC")["checklist_stocks"]
        self.assertEqual((cl["strengthCount"], cl["timingCount"]), (0, 2))
        self.assertTrue(cl["sentence1"].startswith("This stock has"))
        d = _live_like_intc(); del d["checklistData"]["strengthCount"]; d["checklistData"]["timingCount"] = None
        cl = _adapt_suggestions_to_legacy(d, "INTC")["checklist_stocks"]
        self.assertNotIn("strengthCount", cl)                  # absent, so the guard's default applies
        self.assertNotIn("timingCount", cl)                    # never None (None < 1 would raise)

    def test_signals_default_when_no_signalinfo(self):
        d = _live_like_intc(); d.pop("signalInfo")
        meta = _adapt_suggestions_to_legacy(d, "INTC")["metaInfo"][0]
        self.assertEqual(meta["signals"], "000000000000")

    def test_etf_populates_group_name(self):
        d = _live_like_intc(); d["isEtf"] = True; d["name"] = "SPDR S&P 500 ETF"
        meta = _adapt_suggestions_to_legacy(d, "SPY")["metaInfo"][0]
        self.assertEqual(meta["etf_group_name"], "SPDR S&P 500 ETF")
        self.assertTrue(meta["is_etf_symbol"])

    def test_invalid_symbol_returns_status(self):
        # Unknown ticker -> HTTP 200 with empty checklistData + null name.
        for bad in ({}, None, {"name": None, "checklistData": {}}):
            with self.subTest(bad=bad):
                out = _adapt_suggestions_to_legacy(bad, "ZZZZ")
                self.assertEqual(out["status"], "invalid symbol")
                self.assertNotIn("pgr", out)

    def test_missing_price_fields_are_none_not_crash(self):
        d = _live_like_intc()
        for k in ("lastPrice", "days1ChangePct", "days1Change"):
            d.pop(k)
        meta = _adapt_suggestions_to_legacy(d, "INTC")["metaInfo"][0]
        self.assertIsNone(meta["Last"])
        self.assertIsNone(meta["Percentage "])


# ── The 20 PGR factors: pgrData -> legacy Financials/Earnings/Technicals/Experts ──

def _factors(out):
    return {k: dict(kv for e in v for kv in e.items()) for d in out["pgr"][1:5] for k, v in d.items()}


class TestPgrFactors(unittest.TestCase):
    # The legacy order and names, as cached up to 2026-08-31 (backtest.py reads these).
    LEGACY = {
        "Financials": ["Value", "LT Debt to Equity", "Price to Book", "Return on Equity", "Price to Sales",
                       "Free Cash Flow"],
        "Earnings": ["Value", "Earnings Growth", "Earnings Surprise", "Earnings Trend", "Projected P/E",
                     "Earnings Consistency"],
        "Technicals": ["Value", "Rel Strength vs Market", "Chaikin Money Flow", "Price Strength",
                       "Price Trend ROC", "Volume Trend"],
        "Experts": ["Value", "Estimate Trend", "Short Interest", "Insider Activity", "Analyst Rating Trend",
                    "Industry Rel Strength"],
    }

    def test_all_24_entries_in_legacy_order(self):
        out = _adapt_suggestions_to_legacy(_live_like_intc(), "INTC")
        for slot, (group, names) in zip(out["pgr"][1:5], self.LEGACY.items(), strict=True):
            with self.subTest(group=group):
                self.assertEqual([next(iter(e)) for e in slot[group]], names)
                self.assertTrue(all(1 <= next(iter(e.values())) <= 5 for e in slot[group]))

    def test_matched_factors_take_the_right_new_key(self):
        f = _factors(_adapt_suggestions_to_legacy(_live_like_intc(), "INTC"))
        self.assertEqual(f["Financials"]["Free Cash Flow"], 1)          # businessValue
        self.assertEqual(f["Financials"]["Return on Equity"], 4)        # returnOnInvestment
        self.assertEqual(f["Earnings"]["Value"], 3)                     # earningsPerformance
        self.assertEqual(f["Earnings"]["Earnings Surprise"], 5)         # epsSurprise
        self.assertEqual(f["Earnings"]["Projected P/E"], 1)             # projectedPeRatio
        self.assertEqual(f["Technicals"]["Rel Strength vs Market"], 2)  # relativeStrengthVsMarket
        self.assertEqual(f["Technicals"]["Value"], 4)                   # priceVolumeActivity
        self.assertEqual(f["Experts"]["Estimate Trend"], 1)             # earningEstimatesRevision
        self.assertEqual(f["Experts"]["Short Interest"], 5)

    def test_earnings_growth_is_stability_not_performance(self):
        # Matched on 26 symbols: earningsStability = legacy "Earnings Growth" (100%),
        # earningsPerformance = the group "Value" (85%). INTC has 3 in both, so set them apart.
        d = _live_like_intc(); d["pgrData"]["earnings"].update(earningsStability=2, earningsPerformance=4)
        f = _factors(_adapt_suggestions_to_legacy(d, "INTC"))["Earnings"]
        self.assertEqual((f["Earnings Growth"], f["Value"]), (2, 4))

    def test_every_new_key_is_used_once(self):
        keys = [k for _, new, pairs in _PGR_FACTOR_MAP for _, k in pairs]
        self.assertEqual(len(keys), len(set(keys)))
        for _, new, pairs in _PGR_FACTOR_MAP:
            self.assertEqual({k for _, k in pairs}, set(_live_pgr_data()[new]))

    def test_missing_pgr_data_gives_empty_lists(self):
        d = _live_like_intc(); d.pop("pgrData")
        self.assertEqual(_factors(_adapt_suggestions_to_legacy(d, "INTC")),
                         {"Financials": {}, "Earnings": {}, "Technicals": {}, "Experts": {}})
        d["pgrData"] = {"financials": {"businessValue": None, "debtEquityRatio": 2}}
        self.assertEqual(_factors(_adapt_suggestions_to_legacy(d, "INTC"))["Financials"], {"LT Debt to Equity": 2})

    def test_rs_fallback_reads_the_mapped_technicals(self):
        d = _live_like_intc(); d["checklistData"].pop("relativeStrength")
        pg = PowerGauge("INTC")
        pg.init_from_json(_adapt_suggestions_to_legacy(d, "INTC"), check_schema=False)
        self.assertEqual(pg.relative_strength, "2")


class TestSchemaWarnsOnEmptyFactors(unittest.TestCase):
    def test_stock_without_factors_warns(self):
        d = _live_like_intc(); d.pop("pgrData")
        with self.assertLogs(_pg_log.name, level="WARNING") as cm:
            PowerGauge("INTC").init_from_json(_adapt_suggestions_to_legacy(d, "INTC"))
        self.assertTrue(any("no PGR factors" in m for m in cm.output))

    def test_stock_with_factors_and_etf_without_do_not_warn(self):
        etf = _live_like_intc(); etf.pop("pgrData"); etf["isEtf"] = True
        for d in (_live_like_intc(), etf):
            with self.subTest(etf=d["isEtf"]):
                with self.assertNoLogs(_pg_log.name, level="WARNING"):
                    PowerGauge("X").init_from_json(_adapt_suggestions_to_legacy(d, "X"))


# ── The raw response is kept next to the adapted snapshot ──────────────────────

class TestRawCache(unittest.TestCase):
    def _fetch(self, payload, market_open=False):
        resp = mock.MagicMock(ok=True, status_code=200)
        resp.json.return_value = payload
        http = mock.MagicMock()
        http.get.return_value = resp
        with mock.patch.object(powergauge.paths, "symbol_dir", return_value=self.snap),                 mock.patch.object(powergauge.paths, "symbol_raw_dir", return_value=self.raw),                 mock.patch.object(powergauge, "OHLCV_DIR", self.snap),                 mock.patch.object(powergauge, "ensure_valid_session", return_value={"jsessionid": "x"}),                 mock.patch.object(powergauge, "_get_http_session", return_value=http),                 mock.patch.object(powergauge, "is_nyse_market_open", return_value=market_open),                 mock.patch.object(PowerGauge, "find_prev_pf", lambda self: None):
            powergauge.get_symbol_data("INTC", self.day, False)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.snap, self.raw = os.path.join(self._tmp.name, "Symbol"), os.path.join(self._tmp.name, "Symbol_raw")
        self.day = datetime.date.today()

    def tearDown(self):
        self._tmp.cleanup()

    def test_ok_fetch_keeps_the_untouched_response(self):
        payload = {"data": _live_like_intc(), "extra": "kept"}
        with mock.patch.object(powergauge.paths, "symbol_raw_dir", return_value=self.raw):
            self._fetch(payload)
            self.assertEqual(powergauge.load_raw("INTC", self.day), payload)
        self.assertTrue(os.path.exists(os.path.join(self.snap, "INTC", f"INTC_{self.day}.json")))

    def test_nothing_saved_while_open_or_when_invalid(self):
        self._fetch({"data": _live_like_intc()}, market_open=True)
        self._fetch({"data": {}})
        self.assertFalse(os.path.exists(self.raw))

    def test_a_failed_raw_write_does_not_fail_the_fetch(self):
        with mock.patch.object(powergauge.gzip, "open", side_effect=OSError("disk full")),                 self.assertLogs(_pg_log.name, level="WARNING") as cm:
            self._fetch({"data": _live_like_intc()})
        self.assertTrue(any("could not save the raw response" in m for m in cm.output))
        self.assertTrue(os.path.exists(os.path.join(self.snap, "INTC", f"INTC_{self.day}.json")))


# ── Round-trip: adapter output feeds PowerGauge.init_from_json correctly ───────

class TestAdapterRoundTrip(unittest.TestCase):
    def test_init_from_json_consumes_adapted_bundle(self):
        bundle = _adapt_suggestions_to_legacy(_live_like_intc(), "INTC")
        pg = PowerGauge("INTC")
        pg.init_from_json(bundle)
        self.assertEqual(pg.price, 89.72)
        self.assertEqual(pg.pgr_value, 3)
        self.assertEqual(pg.pgr_corrected_value, 3)
        self.assertEqual(pg.money_flow, "Weak")
        self.assertEqual(pg.over_bt_sl, "Early")
        self.assertEqual(pg.lt_trend, "Weak")

    def test_invalid_symbol_sets_price_minus_one(self):
        pg = PowerGauge("ZZZZ")
        pg.init_from_json(_adapt_suggestions_to_legacy(None, "ZZZZ"), check_schema=False)
        self.assertEqual(pg.price, -1)


if __name__ == "__main__":
    unittest.main()
