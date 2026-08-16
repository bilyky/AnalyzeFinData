"""
Tests for risk_utils.resolve_stop — the shared stop-detection ladder
(swing-low -> ATR -> 8%). Pure inputs, no file I/O.
"""
import datetime
import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import risk_utils


class TestResolveStop(unittest.TestCase):
    def test_swing_low_preferred(self):
        # min(last 3 lows) x 0.99, when below price.
        s = risk_utils.resolve_stop(12.0, lows=[10.0, 11.0, 9.0],
                                    highs=[12, 12, 12], closes=[11, 11, 11])
        self.assertEqual(s, round(9.0 * 0.99, 2))   # 8.91

    def test_atr_when_swing_low_not_below_price(self):
        # Recent lows sit above price -> swing-low invalid -> ATR stop.
        s = risk_utils.resolve_stop(100.0,
                                    highs=[112, 113, 114],
                                    lows=[110, 111, 112],
                                    closes=[111, 112, 113])
        # ATR = avg TR = 2.0 -> 100 - 2.5*2 = 95.0
        self.assertEqual(s, 95.0)

    def test_pct_fallback_when_no_series(self):
        self.assertEqual(risk_utils.resolve_stop(100.0), 92.0)   # 8% below

    def test_atr_unavailable_falls_to_pct(self):
        # Lows above price (no swing stop) and too few bars for ATR -> 8%.
        s = risk_utils.resolve_stop(50.0, lows=[60.0], highs=[61.0], closes=[60.5])
        self.assertEqual(s, round(50.0 * 0.92, 2))

    def test_invalid_price(self):
        self.assertIsNone(risk_utils.resolve_stop(0))
        self.assertIsNone(risk_utils.resolve_stop(None))
        self.assertIsNone(risk_utils.resolve_stop("nope"))

    def test_stale_cache_uses_pct_off_live_price(self):
        # Loading by symbol with a stale cache -> ignore swing-low/ATR, use 8% off price.
        stale_date = "2020-01-01"
        with mock.patch.object(risk_utils, "_load_ohlcv_series",
                               return_value=([200, 201, 202], [190, 191, 192],
                                             [195, 196, 197], stale_date)):
            s = risk_utils.resolve_stop(100.0, symbol="OLD")
        self.assertEqual(s, 92.0)   # 8% off live 100, NOT the (stale) 190x0.99=188.1

    def test_fresh_cache_uses_swing_low(self):
        fresh = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        with mock.patch.object(risk_utils, "_load_ohlcv_series",
                               return_value=([12, 12, 12], [10, 11, 9],
                                             [11, 11, 11], fresh)):
            s = risk_utils.resolve_stop(12.0, symbol="NEW")
        self.assertEqual(s, round(9.0 * 0.99, 2))   # 8.91 swing-low, cache is fresh

    def test_detect_support_confirmed_pivot(self):
        # A clear swing low of 90 with 3 higher bars each side -> support 90.
        lows = [100, 99, 98, 90, 98, 99, 100, 101, 102, 103]
        self.assertEqual(risk_utils.detect_support(105.0, lows, k=3), 90)

    def test_detect_support_ignores_unconfirmed_recent_low(self):
        # 85 is lower but sits in the last k=3 bars (unconfirmed) -> still returns 90.
        lows = [100, 99, 98, 90, 98, 99, 100, 101, 85, 86]
        self.assertEqual(risk_utils.detect_support(105.0, lows, k=3), 90)

    def test_detect_support_none_when_too_short(self):
        self.assertIsNone(risk_utils.detect_support(100.0, [95, 96, 97], k=3))

    def test_resolve_detailed_reports_support_source(self):
        lows = [100, 99, 98, 90, 98, 99, 100, 101, 102, 103]
        d = risk_utils.resolve_stop_detailed(105.0, lows=lows,
                                             highs=[110] * 10, closes=[105] * 10)
        self.assertEqual(d["source"], "support")
        self.assertEqual(d["support"], 90)
        self.assertEqual(d["stop"], round(90 * 0.99, 2))

    def test_resolve_detailed_stale_source(self):
        with mock.patch.object(risk_utils, "_load_ohlcv_series",
                               return_value=([200] * 8, [190] * 8, [195] * 8, "2020-01-01")):
            d = risk_utils.resolve_stop_detailed(100.0, symbol="OLD")
        self.assertTrue(d["stale"])
        self.assertEqual(d["source"], "stale")
        self.assertEqual(d["stop"], 92.0)

    def test_detect_resistance_confirmed_pivot(self):
        highs = [100, 101, 102, 120, 102, 101, 100, 99, 98, 97]
        self.assertEqual(risk_utils.detect_resistance(105.0, highs, k=3), 120)

    def test_detect_resistance_ignores_unconfirmed_recent_high(self):
        highs = [100, 101, 102, 120, 102, 101, 100, 99, 130, 131]  # 130/131 unconfirmed
        self.assertEqual(risk_utils.detect_resistance(105.0, highs, k=3), 120)

    def test_detect_resistance_none_when_price_above_all(self):
        highs = [100, 101, 102, 103, 102, 101, 100, 99, 98, 97]
        self.assertIsNone(risk_utils.detect_resistance(500.0, highs, k=3))

    def test_resolve_target_reports_resistance(self):
        highs = [100, 101, 102, 120, 102, 101, 100, 99, 98, 97]
        t = risk_utils.resolve_target_detailed(105.0, highs=highs,
                                               lows=[95] * 10, closes=[100] * 10)
        self.assertEqual(t["source"], "resistance")
        self.assertEqual(t["target"], 120)

    def test_resolve_target_stale_source(self):
        with mock.patch.object(risk_utils, "_load_ohlcv_series",
                               return_value=([120] * 8, [110] * 8, [115] * 8, "2020-01-01")):
            t = risk_utils.resolve_target_detailed(100.0, symbol="OLD")
        self.assertTrue(t["stale"])
        self.assertEqual(t["target"], round(100.0 * 1.08, 2))   # +8% off live

    def test_as_of_skips_staleness_and_computes_support(self):
        # Entry-anchored: even a very old as-of date must NOT trip the staleness gate;
        # it should compute the confirmed swing low from the as-of series.
        lows = [100, 99, 98, 90, 98, 99, 100, 101, 102, 103]
        with mock.patch.object(risk_utils, "_load_ohlcv_series",
                               return_value=([110] * 10, lows, [100] * 10, "2020-01-01")):
            d = risk_utils.resolve_stop_detailed(105.0, symbol="OLD", as_of="2020-01-01")
        self.assertFalse(d["stale"])
        self.assertEqual(d["source"], "support")
        self.assertEqual(d["stop"], round(90 * 0.99, 2))

    def test_stop_always_below_price(self):
        for s in (risk_utils.resolve_stop(100.0, lows=[95, 96, 97], highs=[101]*3, closes=[100]*3),
                  risk_utils.resolve_stop(100.0)):
            self.assertLess(s, 100.0)
            self.assertGreater(s, 0)

    @mock.patch("aether.risk_utils.pd.DataFrame.from_dict")
    @mock.patch("aether.risk_utils.Path.exists", return_value=True)
    @mock.patch("builtins.open", mock.mock_open(read_data="{\"Time Series (Daily)\": {\"2026-08-01\": {}}}"))
    def test_calculate_atr_duplicate_columns(self, mock_exists, mock_from_dict):
        import pandas as pd
        # Return a dataframe with 15 rows and duplicate '5. volume' columns to trigger the Length mismatch on broken code
        df = pd.DataFrame(
            [[10.0, 12.0, 8.0, 11.0, 1000.0, 1000.0]] * 15, 
            columns=['1. open', '2. high', '3. low', '4. close', '5. volume', '5. volume'],
            index=[f"2026-08-{i:02d}" for i in range(1, 16)]
        )
        mock_from_dict.return_value = df
        
        # This will raise a Length mismatch exception on the broken codebase and pass on the fixed codebase
        atr = risk_utils.calculate_atr("MOCK_SYM")
        self.assertEqual(atr, 4.0)


class TestSplitAdjust(unittest.TestCase):
    def test_no_split_returns_originals(self):
        o = [10, 10.1, 10.2, 10.1]
        h = [11, 11, 11, 11]
        l = [9, 9, 9, 9]
        c = [10, 10.1, 10.2, 10.1]
        H, L, C = risk_utils._split_adjust_ohlcv(o, h, l, c)
        self.assertEqual(C, c)   # unchanged object semantics: same values

    def test_forward_split_backadjusts_history(self):
        # 2:1 forward split between idx3 and idx4 (close & open both ~halve).
        o = [100, 101, 102, 102, 51, 51.5]
        c = [100, 102, 101, 103, 51.5, 52]
        H, L, C = risk_utils._split_adjust_ohlcv(o, c[:], c[:], c)
        self.assertEqual(C, [50.0, 51.0, 50.5, 51.5, 51.5, 52.0])

    def test_reverse_split_backadjusts_history(self):
        # 1:4 reverse split between idx2 and idx3.
        o = [10, 10.1, 10.0, 40.0, 41, 40]
        c = [10, 10.2, 10.1, 40.4, 41, 40]
        H, L, C = risk_utils._split_adjust_ohlcv(o, c[:], c[:], c)
        self.assertEqual(C, [40.0, 40.8, 40.4, 40.4, 41.0, 40.0])

    def test_intraday_crash_preserved(self):
        # Close craters (0.49x) but OPEN sits near the prior close -> NOT a split.
        o = [100, 101, 100, 50]     # idx3 open 50 ~ prior close 51
        c = [100, 102, 51, 25]
        H, L, C = risk_utils._split_adjust_ohlcv(o, c[:], c[:], c)
        self.assertEqual(C, c)      # untouched — real move, not a split

    def test_missing_open_does_not_adjust(self):
        # No/zero open -> cannot verify -> conservative: leave the bar alone.
        o = [100, 101, 102, 0]
        c = [100, 102, 101, 51]     # 0.5x close jump but open unusable
        H, L, C = risk_utils._split_adjust_ohlcv(o, c[:], c[:], c)
        self.assertEqual(C, c)


class TestLoaderSplitAdjust(unittest.TestCase):
    def test_load_ohlcv_series_returns_adjusted(self):
        # Synthetic cache file with a 2:1 forward split -> loader returns a continuous
        # series (pre-split closes halved), so detect_support sees the current scale.
        import json as _json
        bars = {
            "2020-01-01": {"1. open": "100", "2. high": "104", "3. low": "99", "4. close": "100"},
            "2020-01-02": {"1. open": "101", "2. high": "105", "3. low": "100", "4. close": "102"},
            "2020-01-03": {"1. open": "102", "2. high": "104", "3. low": "100", "4. close": "101"},
            "2020-01-04": {"1. open": "103", "2. high": "105", "3. low": "101", "4. close": "103"},
            "2020-01-05": {"1. open": "51", "2. high": "52", "3. low": "50", "4. close": "51.5"},
            "2020-01-06": {"1. open": "51.5", "2. high": "53", "3. low": "51", "4. close": "52"},
        }
        payload = _json.dumps({"Time Series (Daily)": bars})
        with mock.patch.object(risk_utils.Path, "exists", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=payload)):
            highs, lows, closes, last = risk_utils._load_ohlcv_series("MOCK")
        self.assertEqual(last, "2020-01-06")
        # Pre-split closes are halved onto the current scale; post-split bars unchanged.
        self.assertEqual(closes, [50.0, 51.0, 50.5, 51.5, 51.5, 52.0])
        # Highs likewise (104 -> 52.0 at the current scale).
        self.assertEqual(highs[0], 52.0)


if __name__ == "__main__":
    unittest.main()
