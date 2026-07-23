"""
Unit tests for powergauge.py compute helpers.
No API calls — uses synthetic OHLCV dicts and PowerGauge objects.

  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring
from powergauge import (
    PowerGauge, _pgr_str, _buying_ratio, _compute_pgr_fields, _SYMBOL_RE,
    get_symbol_data,
)
from tests.conftest import make_ohlcv


# ── _pgr_str ──────────────────────────────────────────────────────────────────

class TestPgrStr(unittest.TestCase):
    def test_all_defined_values(self):
        mapping = {0: "", 1: "Be-", 2: "Be", 3: "N", 4: "Bu", 5: "Bu+", 6: ""}
        for v, expected in mapping.items():
            with self.subTest(v=v):
                self.assertEqual(_pgr_str(v), expected)

    def test_out_of_bounds_returns_empty(self):
        for v in (-1, 7, 99, -100):
            with self.subTest(v=v):
                self.assertEqual(_pgr_str(v), "")


# ── _buying_ratio ─────────────────────────────────────────────────────────────

class TestBuyingRatio(unittest.TestCase):
    def _pg(self, pgr_corr=4, lt="Neutral", mf="Neutral", ob="Neutral", ind=""):
        pg = PowerGauge("TEST")
        pg.pgr_corrected_value = pgr_corr
        pg.lt_trend = lt
        pg.money_flow = mf
        pg.over_bt_sl = ob
        pg.industry_strength = ind
        return pg

    def _fields(self, rr=2.0, delta=0, season=0.0):
        return {"risk_ratio": rr, "pgr_delta": delta, "seasonality": season}

    def test_result_always_bounded(self):
        for pgr in range(1, 6):
            pg = self._pg(pgr_corr=pgr)
            result = _buying_ratio(pg, self._fields())
            self.assertGreaterEqual(result, -10.0)
            self.assertLessEqual(result, 10.0)

    def test_factor_directions(self):
        cases = [
            ("bull>bear",      _buying_ratio(self._pg(pgr_corr=5), self._fields()),
                               _buying_ratio(self._pg(pgr_corr=1), self._fields())),
            ("strong_mf>weak", _buying_ratio(self._pg(mf="Strong"), self._fields()),
                               _buying_ratio(self._pg(mf="Weak"),   self._fields())),
            ("delta>no_delta", _buying_ratio(self._pg(), self._fields(delta=1)),
                               _buying_ratio(self._pg(), self._fields(delta=0))),
            ("good_rr>zero",   _buying_ratio(self._pg(), self._fields(rr=3.0)),
                               _buying_ratio(self._pg(), self._fields(rr=0.0))),
        ]
        for label, higher, lower in cases:
            with self.subTest(label):
                self.assertGreater(higher, lower)


# ── _compute_pgr_fields ───────────────────────────────────────────────────────

def _make_pg_with_ohlcv(n_days: int = 35):
    """Return (PowerGauge, ohlcv_ts) for a simple uptrend."""
    closes = [140.0 + i * 0.5 for i in range(n_days)] + [158.0]
    ohlcv = make_ohlcv(closes)
    all_dates = sorted(ohlcv.keys())
    entry_date = date.fromisoformat(all_dates[-1])

    pg = PowerGauge("TEST", entry_date)
    pg.price = closes[-1]
    pg.percentage = (closes[-1] - closes[-2]) / closes[-2] * 100
    pg.pgr_value = 4
    pg.pgr_corrected_value = 4
    pg.industry_strength = "Strong"
    pg.lt_trend = "Neutral"
    pg.money_flow = "Strong"
    pg.over_bt_sl = "Optimal"
    return pg, ohlcv


class TestComputePgrFields(unittest.TestCase):
    def setUp(self):
        scoring.clear_regime_cache()
        import aether.scoring as _sc
        _sc._digit_index      = {}  # empty dict (not None) so loader is skipped
        _sc._digit_full_index = {}

    def tearDown(self):
        scoring.clear_regime_cache()
        import aether.scoring as _sc
        _sc._digit_index      = None
        _sc._digit_full_index = None

    def test_all_expected_keys_present(self):
        pg, ohlcv = _make_pg_with_ohlcv()
        fields = _compute_pgr_fields(pg, ohlcv_ts=ohlcv)
        expected = ("pgr", "prev_pgr", "prev_percentage", "pgr_delta",
                    "prev_move_perc", "prev_move_price", "stop_price",
                    "risk_ratio", "setup_ok", "seasonality", "rel_vol",
                    "market_regime", "ob_os", "money_flow", "lt_trend",
                    "industry_strength", "buying_ratio", "short_score", "long_score")
        for key in expected:
            with self.subTest(key=key):
                self.assertIn(key, fields)

    def test_scores_bounded(self):
        pg, ohlcv = _make_pg_with_ohlcv()
        fields = _compute_pgr_fields(pg, ohlcv_ts=ohlcv)
        for key in ("buying_ratio", "short_score", "long_score"):
            with self.subTest(key=key):
                self.assertGreaterEqual(fields[key], -10.0)
                self.assertLessEqual(fields[key], 10.0)

    def test_pgr_string_correct(self):
        pg, ohlcv = _make_pg_with_ohlcv()
        pg.pgr_value = 4
        pg.pgr_corrected_value = 4
        self.assertEqual(_compute_pgr_fields(pg, ohlcv_ts=ohlcv)["pgr"], "Bu")

    def test_pgr_shows_slash_when_corrected_differs(self):
        pg, ohlcv = _make_pg_with_ohlcv()
        pg.pgr_value = 5           # Bu+
        pg.pgr_corrected_value = 4  # Bu → "Bu/Bu+"
        self.assertIn("/", _compute_pgr_fields(pg, ohlcv_ts=ohlcv)["pgr"])

    def test_no_ohlcv_gives_none_setup_and_zero_stop(self):
        pg = PowerGauge("TEST", date(2024, 6, 1))
        pg.price = 100.0
        pg.pgr_value = 4
        pg.pgr_corrected_value = 4
        fields = _compute_pgr_fields(pg, ohlcv_ts=None)
        self.assertIsNone(fields["setup_ok"])
        self.assertEqual(fields["stop_price"], 0)
        self.assertEqual(fields["risk_ratio"], 0)

    def test_stop_price_below_entry_price(self):
        pg, ohlcv = _make_pg_with_ohlcv()
        fields = _compute_pgr_fields(pg, ohlcv_ts=ohlcv)
        self.assertGreater(fields["stop_price"], 0, "stop_price should be nonzero for an uptrend with OHLCV data")
        self.assertLess(fields["stop_price"], pg.price)

    def test_oob_pgr_value_does_not_raise(self):
        pg, ohlcv = _make_pg_with_ohlcv()
        pg.pgr_value = 99
        pg.pgr_corrected_value = 0
        fields = _compute_pgr_fields(pg, ohlcv_ts=ohlcv)
        self.assertEqual(fields["pgr"], "")


# ── Recursion depth cap ───────────────────────────────────────────────────────

class TestRecursionDepthCap(unittest.TestCase):
    def _chain(self, length: int, pct: float = 1.0) -> PowerGauge:
        nodes = [PowerGauge("TEST") for _ in range(length)]
        for i, pg in enumerate(nodes):
            pg.percentage = pct
            pg.change = 1.0 if pct > 0 else -1.0
            pg.price = 100.0 + i
            if i + 1 < length:
                pg.prevPG = nodes[i + 1]
        return nodes[0]

    def test_count_capped_value(self):
        # Cap at depth>30: depth 31 returns 1, bubbles back → root returns 32
        root = self._chain(100, pct=1.0)
        self.assertEqual(root.get_prev_same_move_count(), 32)

    def test_short_chain_not_capped(self):
        root = self._chain(5, pct=1.0)
        self.assertEqual(root.get_prev_same_move_count(), 4)

    def test_down_streak_capped_negative(self):
        root = self._chain(100, pct=-1.0)
        result = root.get_prev_same_move_count()
        self.assertLess(result, 0)
        self.assertGreater(result, -100)

    def test_percent_no_stack_overflow(self):
        root = self._chain(100)
        result = root.get_prev_same_move_percent()
        self.assertIsInstance(result, float)
        self.assertLess(abs(result), 1000)

    def test_price_no_stack_overflow(self):
        root = self._chain(100)
        result = root.get_prev_same_move_price()
        self.assertIsInstance(result, float)
        self.assertGreater(result, 0)


# ── Symbol path validation ────────────────────────────────────────────────────

class TestSymbolValidation(unittest.TestCase):
    def test_valid(self):
        for sym in ("AAPL", "BRK.B", "BRK_B", "SPY", "GOOGL", "T"):
            with self.subTest(sym=sym):
                self.assertIsNotNone(_SYMBOL_RE.match(sym))

    def test_invalid(self):
        for sym in ("../evil", "", "AA PL", "AA\x00", "aapl"):
            with self.subTest(sym=sym):
                self.assertIsNone(_SYMBOL_RE.match(sym))

    def test_get_symbol_data_raises_on_bad_symbol(self):
        with self.assertRaises(ValueError):
            get_symbol_data("../../../etc", date.today(), False, "fake")


if __name__ == "__main__":
    unittest.main()
