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

    def test_stop_always_below_price(self):
        for s in (risk_utils.resolve_stop(100.0, lows=[95, 96, 97], highs=[101]*3, closes=[100]*3),
                  risk_utils.resolve_stop(100.0)):
            self.assertLess(s, 100.0)
            self.assertGreater(s, 0)


if __name__ == "__main__":
    unittest.main()
