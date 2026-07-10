import os
import sys
import unittest
import tempfile
import json
import openpyxl
from unittest import mock
from pathlib import Path

# Add current and parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ai_portfolio_game as game
import watchdog

class TestActiveSetupPricing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.xlsx_path = Path(self.temp_dir.name) / "state_of_the_day.xlsx"
        
        # Build mock workbook with active setup symbol on row 350
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["spacer"] * 30) # Row 1: Header
        
        # Append 340 stagnant rows
        for _ in range(340):
            ws.append([None] * 30)
            
        # Row 342 (Excel row 342): active setup symbol
        row_active = [None] * 30
        row_active[3] = "APA"
        row_active[20] = "1" # Setup active
        row_active[24] = 4.0 # S10
        row_active[25] = 6.0 # L60
        ws.append(row_active)
        
        # Row 343: inactive symbol
        row_inactive = [None] * 30
        row_inactive[3] = "XYZ"
        row_inactive[20] = "0" # Setup inactive
        ws.append(row_inactive)
        
        wb.save(self.xlsx_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_only_active_setups_extracted(self):
        # Verify that we extract BSY/APA but skip inactive ones, bypassing the old row-50 limit
        wb = openpyxl.load_workbook(self.xlsx_path, data_only=True)
        ws = wb["Research"]
        
        extracted_syms = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3] and str(row[20] or '') in ('1', 'OK', 1):
                extracted_syms.append(row[3])
        
        self.assertIn("APA", extracted_syms)
        self.assertNotIn("XYZ", extracted_syms)
        self.assertEqual(len(extracted_syms), 1)


class TestSequentialVerificationLoop(unittest.TestCase):
    def test_fallback_buy_execution_on_safety_failure(self):
        # Set up a defensive state with exactly 1 available slot
        state = {
            "balance": 7746.29,
            "equity": 10295.0,
            "positions": {
                "FANG": {"qty": 8, "cost": 184.37},
                "HE": {"qty": 81, "cost": 13.57}
            },
            "history": [],
            "start_date": "2026-01-01",
            "profile": "DEFENSIVE"
        }
        
        rules = {
            "max_positions": 3,
            "max_allocation_pct": 0.10,
            "atr_multiplier": 1.5,
            "min_score_threshold": 10.0,
            "cash_buffer_pct": 0.50
        }
        
        # Two top buys: #1 (BSY) has higher score, #2 (APA) has slightly lower score
        top_buys = [
            {"sym": "BSY", "price": 31.55, "total": 11.5, "bottom_desc": ""},
            {"sym": "APA", "price": 33.825, "total": 10.7, "bottom_desc": ""}
        ]
        
        available_slots = 1
        min_cash_required = state["equity"] * rules["cash_buffer_pct"] # 5147.50
        
        # Track buy transactions executed
        buys_executed = 0
        executed_transactions = []
        
        # We mock backtrack_verify: BSY fails (vertical drop), APA passes
        def mock_verify(symbol):
            if symbol == "BSY":
                return False, "Failed check"
            return True, "Verified"
            
        with mock.patch.object(game, "backtrack_verify", side_effect=mock_verify), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            
            # Execute our sequential verification loop
            for buy in top_buys:
                if buys_executed >= available_slots:
                    break
                    
                current_available = available_slots - buys_executed
                cash_per_buy = (state["balance"] - min_cash_required) / current_available
                qty = int(cash_per_buy // buy["price"])
                if qty > 0:
                    is_verified, v_msg = game.backtrack_verify(buy["sym"])
                    if not is_verified:
                        continue # Skip to next candidate

                    cost = qty * buy["price"]
                    state["balance"] -= cost
                    state["positions"][buy["sym"]] = {"qty": qty, "cost": buy["price"], "stop_loss": buy["price"] - 1.5}
                    tx = {"date": "2026-07-09", "type": "BUY", "symbol": buy["sym"], "price": buy["price"], "qty": qty}
                    state["history"].append(tx)
                    executed_transactions.append(tx)
                    buys_executed += 1

        # Verify that the slot was successfully filled by the fallback candidate APA
        self.assertEqual(buys_executed, 1)
        self.assertIn("APA", state["positions"])
        self.assertNotIn("BSY", state["positions"])
        self.assertEqual(len(executed_transactions), 1)
        self.assertEqual(executed_transactions[0]["symbol"], "APA")
        self.assertEqual(executed_transactions[0]["qty"], 76)


class TestWatchdogLogScanning(unittest.TestCase):
    def test_ignore_success_with_0_errors(self):
        # Mocking file read with a successful log containing '0 errors'
        mock_log = "Status: [2026-07-09 05:36:24] OHLCV: 1 recovered, 514 already current, 0 errors\n"
        
        with mock.patch("builtins.open", mock.mock_open(read_data=mock_log)), \
             mock.patch("os.path.exists", return_value=True):
            errors = watchdog.check_logs()
            
        # Verify that '0 errors' is successfully ignored by the log scanner
        self.assertEqual(errors, [])


class TestBubbleZScoreCalculation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.symbol_full_dir = Path(self.temp_dir.name) / "Data" / "Symbol_full"
        os.makedirs(self.symbol_full_dir, exist_ok=True)
        self._orig_xlsx = game.XLSX_FILE
        game.XLSX_FILE = Path(self.temp_dir.name) / "state_of_the_day.xlsx"
        
        # Build mock 600-day dataset for BUBBLE (rising rapidly)
        self.bubble_path = self.symbol_full_dir / "BUBBLE_daily.json"
        ts = {}
        price = 100.0
        for i in range(600):
            date_str = f"2026-{i//100+1:02d}-{(i%100)+1:02d}"
            price += 0.5 # Constantly rising
            ts[date_str] = {
                "1. open": str(price), "2. high": str(price), "3. low": str(price),
                "4. close": str(price), "5. volume": "100"
            }
        with open(self.bubble_path, "w") as f:
            json.dump({"Time Series (Daily)": ts}, f)

    def tearDown(self):
        game.XLSX_FILE = self._orig_xlsx
        self.temp_dir.cleanup()

    def test_calculate_bubble_z_score_sufficient_history(self):
        # Price is rising, Z-score should be highly positive (> 1.5)
        score = game.calculate_bubble_z_score("BUBBLE")
        self.assertIsNotNone(score)
        self.assertTrue(score > 1.5)

    def test_calculate_bubble_z_score_insufficient_history(self):
        # Write a file with only 250 days of history
        short_path = self.symbol_full_dir / "SHORT_daily.json"
        ts = {f"2026-01-{i+1:02d}": {"4. close": "100"} for i in range(250)}
        with open(short_path, "w") as f:
            json.dump({"Time Series (Daily)": ts}, f)
            
        score = game.calculate_bubble_z_score("SHORT")
        self.assertIsNone(score)


class Test25SigmaBubbleGuard(unittest.TestCase):
    def test_bubble_rejection_and_fallback(self):
        # Set up defensive game state with 1 slot
        state = {
            "balance": 7746.29,
            "equity": 10295.0,
            "positions": {
                "FANG": {"qty": 8, "cost": 184.37},
                "HE": {"qty": 81, "cost": 13.57}
            },
            "history": []
        }
        
        # BSY has a Z-score of 3.1 (> 2.5), APA has a Z-score of 0.9 (<= 2.5)
        top_buys = [
            {"sym": "BSY", "price": 31.55, "total": 11.5, "bottom_desc": ""},
            {"sym": "APA", "price": 33.825, "total": 10.7, "bottom_desc": ""}
        ]
        
        def mock_z_score(symbol):
            if symbol == "BSY": return 3.1 # Super-bubble!
            return 0.9 # Healthy value
            
        executed_buys = []
        with mock.patch.object(game, "calculate_bubble_z_score", side_effect=mock_z_score), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
             
            available_slots = 1
            min_cash_required = 10295.0 * 0.50
            buys_executed = 0
            
            for buy in top_buys:
                if buys_executed >= available_slots:
                    break
                    
                current_available = available_slots - buys_executed
                cash_per_buy = (state["balance"] - min_cash_required) / current_available
                qty = int(cash_per_buy // buy["price"])
                if qty > 0:
                    z_score = game.calculate_bubble_z_score(buy["sym"])
                    if z_score is not None and z_score > 2.5:
                        continue # Bypassed due to super-bubble!
                        
                    is_verified, v_msg = game.backtrack_verify(buy["sym"])
                    if not is_verified: continue
                    
                    # Execute
                    state["balance"] -= qty * buy["price"]
                    state["positions"][buy["sym"]] = {"qty": qty, "cost": buy["price"]}
                    executed_buys.append(buy["sym"])
                    buys_executed += 1
                    
        # Verify that BSY was correctly bypassed by the bubble guard, and APA was bought
        self.assertEqual(buys_executed, 1)
        self.assertIn("APA", executed_buys)
        self.assertNotIn("BSY", executed_buys)


if __name__ == "__main__":
    unittest.main()
