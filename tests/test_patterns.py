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
        print("123 REVERSAL TEST:", score, names)
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
        print("2B REVERSAL TEST:", score, names)
        self.assertIn("Vic2B↑", names)
        self.assertGreaterEqual(score, 2.0)

if __name__ == "__main__":
    unittest.main()
