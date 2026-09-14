"""
Unit tests for the new /api/suggestions -> legacy getSymbolData adapter in powergauge.

Chaikin migrated its data API to GET /api/suggestions/{symbol} (a flat dict); the rest
of powergauge still speaks the legacy {pgr[7], metaInfo[1], checklist_stocks{}} schema.
These tests pin the new->old mapping so a future API-shape change can't silently corrupt
ratings/prices. No network calls — the samples mirror verified live responses.

  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from powergauge import (
    _pgr_rating_old5,
    _adapt_suggestions_to_legacy,
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
            "status": "Hold",
        },
        "signalInfo": {"moneyFlowSell": 1, "breakdownSell": 1},
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
