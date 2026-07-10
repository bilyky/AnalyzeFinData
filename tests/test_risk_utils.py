"""
Tests for risk_utils.resolve_stop — the shared stop-detection ladder
(swing-low -> ATR -> 8%). Pure inputs, no file I/O.
"""
import os
import sys
import unittest

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

    def test_stop_always_below_price(self):
        for s in (risk_utils.resolve_stop(100.0, lows=[95, 96, 97], highs=[101]*3, closes=[100]*3),
                  risk_utils.resolve_stop(100.0)):
            self.assertLess(s, 100.0)
            self.assertGreater(s, 0)


if __name__ == "__main__":
    unittest.main()
