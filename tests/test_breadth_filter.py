import datetime
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
    """Create a mock 250-day daily OHLCV dataset with a valid calendar-based date series."""
    ts = {}
    price = base_price
    start = datetime.date(2025, 1, 2)
    for i in range(250):
        date_str = (start + datetime.timedelta(days=i)).isoformat()
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
        self._orig_symdir = ai_portfolio_game.SYMBOL_FULL_DIR
        self.temp_dir = tempfile.TemporaryDirectory()
        self.symbol_full_dir = Path(self.temp_dir.name) / "Data" / "Symbol_full"
        os.makedirs(self.symbol_full_dir, exist_ok=True)

        # Point the game at the temp workbook + OHLCV cache dir (the trend score
        # reads SYMBOL_FULL_DIR, the single source of truth for the cache path).
        ai_portfolio_game.XLSX_FILE = Path(self.temp_dir.name) / "state_of_the_day.xlsx"
        ai_portfolio_game.SYMBOL_FULL_DIR = self.symbol_full_dir

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
        ai_portfolio_game.SYMBOL_FULL_DIR = self._orig_symdir
        self.temp_dir.cleanup()

    def test_trend_score_bullish(self):
        # For SPY (constantly rising), score should be highly bullish (+10.0)
        score = ai_portfolio_game.calculate_ticker_trend_score("SPY")
        self.assertAlmostEqual(score, 10.0, places=1)

    def test_trend_score_missing_file(self):
        # For a non-existent file, return None ("no data"), not a 0.0 score.
        score = ai_portfolio_game.calculate_ticker_trend_score("INVALID_SYMBOL")
        self.assertIsNone(score)

    def _write_spy_row(self, l60=5.0):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["spacer"] * 30)          # header row
        spy_row = [None] * 30
        spy_row[3] = "SPY"
        spy_row[25] = l60                   # l60 score
        ws.append(spy_row)
        wb.save(ai_portfolio_game.XLSX_FILE)

    def test_regime_downgrade_on_breadth_divergence(self):
        # Base profile AGGRESSIVE (l60=5.0); SPY rising (+10) vs flat RSP -> wide
        # divergence -> downgrade AGGRESSIVE -> BALANCED.
        self._write_spy_row(l60=5.0)
        self.assertEqual(ai_portfolio_game.get_market_regime(), "BALANCED")

    def test_regime_not_downgraded_when_rsp_cache_missing(self):
        # RSP cache absent -> rsp_score None -> breadth skipped, base profile kept.
        os.remove(self.rsp_path)
        self._write_spy_row(l60=5.0)
        self.assertEqual(ai_portfolio_game.get_market_regime(), "AGGRESSIVE")

if __name__ == "__main__":
    unittest.main()
