import os
import sys
import unittest
import tempfile
import json
import openpyxl
from pathlib import Path

# Add current and parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ai_portfolio_game

def create_mock_ohlcv(path, base_price, trend_slope):
    """Create a mock 250-day daily OHLCV dataset with a trend slope."""
    ts = {}
    price = base_price
    for i in range(250):
        date_str = f"2026-01-{i+1:02d}" if i < 30 else f"2026-02-{(i-30)+1:02d}"
        if i >= 100: date_str = f"2026-03-{(i-100)+1:02d}"
        if i >= 150: date_str = f"2026-04-{(i-150)+1:02d}"
        if i >= 200: date_str = f"2026-05-{(i-200)+1:02d}"
        
        price += trend_slope
        ts[date_str] = {
            "1. open": str(price),
            "2. high": str(price + 1),
            "3. low": str(price - 1),
            "4. close": str(price),
            "5. volume": "1000"
        }
    
    data = {
        "Meta Data": {"1. Information": "Daily Prices"},
        "Time Series (Daily)": ts
    }
    
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)

class TestBreadthFilter(unittest.TestCase):
    def setUp(self):
        # Cache and save originals
        self._orig_xlsx = ai_portfolio_game.XLSX_FILE
        self.temp_dir = tempfile.TemporaryDirectory()
        self.symbol_full_dir = Path(self.temp_dir.name) / "Data" / "Symbol_full"
        os.makedirs(self.symbol_full_dir, exist_ok=True)
        
        # Override paths inside ai_portfolio_game
        ai_portfolio_game.XLSX_FILE = Path(self.temp_dir.name) / "state_of_the_day.xlsx"
        
        # We also mock parent path in calculate_ticker_trend_score
        self._orig_parent = ai_portfolio_game.XLSX_FILE.parent
        
        # Generate mock files for SPY and RSP
        # Bullish SPY (constantly rising)
        self.spy_path = self.symbol_full_dir / "SPY_daily.json"
        create_mock_ohlcv(self.spy_path, 400.0, 0.5) # rising
        
        # Flat RSP (stagnant/consolidation)
        self.rsp_path = self.symbol_full_dir / "RSP_daily.json"
        create_mock_ohlcv(self.rsp_path, 150.0, 0.0) # flat

    def tearDown(self):
        # Restore originals
        ai_portfolio_game.XLSX_FILE = self._orig_xlsx
        self.temp_dir.cleanup()

    def test_trend_score_bullish(self):
        # For SPY (constantly rising), score should be highly bullish (+10.0)
        score = ai_portfolio_game.calculate_ticker_trend_score("SPY")
        self.assertAlmostEqual(score, 10.0, places=1)

    def test_trend_score_missing_file(self):
        # For a non-existent file, should return 0.0 safely
        score = ai_portfolio_game.calculate_ticker_trend_score("INVALID_SYMBOL")
        self.assertEqual(score, 0.0)

    def test_regime_downgrade_on_breadth_divergence(self):
        # Create a mock state_of_the_day.xlsx workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["spacer"] * 30) # Header row
        
        # Append a SPY row indicating AGGRESSIVE trend (long-term l60 = 5.0)
        spy_row = [None] * 30
        spy_row[3] = "SPY"
        spy_row[25] = 5.0 # l60 score
        ws.append(spy_row)
        wb.save(ai_portfolio_game.XLSX_FILE)
        
        # Run market regime.
        # Base profile should be AGGRESSIVE (since l60 is 5.0).
        # But SPY has +10.0 score and RSP has flat score (not 10.0).
        # Divergence should trigger a downgrade from AGGRESSIVE to BALANCED.
        profile = ai_portfolio_game.get_market_regime()
        self.assertEqual(profile, "BALANCED")

if __name__ == "__main__":
    unittest.main()
