import os
import sys
import unittest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import patterns

class TestTraderVicPatterns(unittest.TestCase):
    def test_trader_vic_123_bottom(self):
        # Build an 80-bar OHLCV time series containing a valid 1-2-3 bottom reversal:
        # Trough 1 at index 20 (low = 90.0)
        # Peak at index 35 (high = 110.0)
        # Trough 2 (Higher Low) at index 50 (low = 95.0)
        # Breakout close at index 79 (close = 115.0)
        ohlcv_ts = {}
        for i in range(80):
            date_str = f"2026-01-{i+1:02d}"
            # Standard price baseline around 100
            op = hi = lo = cl = 100.0
            
            # Form Trough 1 around index 20
            if i == 20:
                lo = 90.0
                cl = 92.0
            # Form Peak around index 35
            elif i == 35:
                hi = 110.0
                cl = 108.0
            # Form Trough 2 (Higher Low) around index 50
            elif i == 50:
                lo = 95.0
                cl = 96.0
            # Breakout bar on the current (last) bar
            elif i == 79:
                cl = 115.0
                hi = 116.0
                op = 110.0
                
            ohlcv_ts[date_str] = {
                "1. open": str(op),
                "2. high": str(hi),
                "3. low": str(lo),
                "4. close": str(cl),
                "5. volume": "10000"
            }
            
        score, names = patterns.chart_pattern_score(ohlcv_ts, "2026-01-80")
        self.assertIn("Vic123↑", names)
        self.assertGreaterEqual(score, 2.0)

    def test_trader_vic_2b_bottom(self):
        # Build an 80-bar OHLCV time series containing a valid 2B bear trap reversal:
        # Significant Trough at index 20 (low = 100.0)
        # Breakout low below index 20 trough at index 75 (low = 95.0)
        # Reclaim and close back above 100.0 on current bar index 79 (close = 102.0)
        ohlcv_ts = {}
        for i in range(80):
            date_str = f"2026-01-{i+1:02d}"
            op = hi = lo = cl = 110.0
            
            # Form Trough 1 around index 20 (need n=5 neighbors, so keep surrounding prices higher)
            if i == 20:
                lo = 100.0
                cl = 101.0
            elif 15 <= i <= 25 and i != 20:
                lo = 108.0
                cl = 109.0
            # Break below 100.0 at index 75
            elif i == 75:
                lo = 95.0
                cl = 96.0
            # Close back above 100.0 on current bar index 79
            elif i == 79:
                cl = 102.0
                lo = 98.0
                
            ohlcv_ts[date_str] = {
                "1. open": str(op),
                "2. high": str(hi),
                "3. low": str(lo),
                "4. close": str(cl),
                "5. volume": "10000"
            }
            
        score, names = patterns.chart_pattern_score(ohlcv_ts, "2026-01-80")
        self.assertIn("Vic2B↑", names)
        self.assertGreaterEqual(score, 2.0)


class TestCandlestickScore(unittest.TestCase):
    """Behavioral tests for candlestick_score (previously untested).

    Two kinds:
      - the empty-data guard scoring.py relies on (no bars => neutral 0.0);
      - a golden/invariant test on a fixture that actually FIRES patterns, so it
        exercises the real compute path (parse -> 17 signals -> weight -> /5.0
        clamp -> round), not a short-circuit. It pins three real properties:
        the score is non-zero (patterns fired), bounded to [-2, +2], and
        SIGN-ANTISYMMETRIC under a price mirror (mirroring O/H/L/C about an axis
        negates the score) — the core directional contract of the bull/bear tally.
    """

    @staticmethod
    def _engulfing_series(mirror=False):
        """12 quiet up-drift bars then a 3-bar bullish-engulfing tail.

        engulfing_signal fires the buy on the bar AFTER the pattern, so the fire
        lands inside candlestick_score's last-5 window. `mirror` reflects every
        price about 100 (O/H/L/C swap+negate), turning every bullish fire bearish.
        """
        ts = {}

        def bar(d, o, h, l, c):
            if mirror:
                o, h, l, c = 200 - o, 200 - l, 200 - h, 200 - c
            ts[d] = {"1. open": str(o), "2. high": str(h),
                     "3. low": str(l), "4. close": str(c), "5. volume": "10000"}

        for i in range(12):
            base = 100 + i * 0.1
            bar(f"2026-04-{i + 1:02d}", base, base + 0.15, base - 0.05, base + 0.1)
        bar("2026-04-13", 101.5, 101.6, 100.4, 100.5)   # bearish (i-2)
        bar("2026-04-14", 100.6, 100.7, 99.4, 99.5)     # bearish (i-1)
        bar("2026-04-15", 99.3, 103.6, 99.2, 103.5)     # bullish engulfing (i)
        return ts

    def test_empty_ohlcv_is_neutral(self):
        self.assertEqual(patterns.candlestick_score({}, "2026-04-15"), 0.0)

    def test_fires_bounded_and_sign_antisymmetric(self):
        up = patterns.candlestick_score(self._engulfing_series(False), "2026-04-15")
        dn = patterns.candlestick_score(self._engulfing_series(mirror=True), "2026-04-15")
        # patterns actually fired (compute path ran, not a guard short-circuit)
        self.assertNotEqual(up, 0.0)
        # bounded contract scoring.py depends on
        for s in (up, dn):
            self.assertGreaterEqual(s, -2.0)
            self.assertLessEqual(s, 2.0)
        # mirroring price negates the score: the bull/bear tally is directionally symmetric
        self.assertAlmostEqual(up, -dn, places=2)
        # golden values pin the full weight/normalization pipeline against silent drift
        self.assertAlmostEqual(up, -1.24, places=2)


if __name__ == "__main__":
    unittest.main()
