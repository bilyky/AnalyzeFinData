"""
Tests for the custom-sprint features, exercising the REAL production helpers
(_active_setup_symbols, _execute_buys, calculate_bubble_z_score, watchdog.check_logs)
rather than copies of their logic, so a regression in the shipped code fails here.
E*TRADE / verify / ATR are mocked; no network.
"""
import os
import sys
import unittest
import tempfile
import json
import openpyxl
import pytz
from unittest import mock
from pathlib import Path
from datetime import datetime as real_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ai_portfolio_game as game
import watchdog


def _defensive_state():
    return {"balance": 7746.29, "equity": 10295.0,
            "positions": {"FANG": {"qty": 8, "cost": 184.37}, "HE": {"qty": 81, "cost": 13.57}},
            "history": [], "start_date": "2026-01-01", "profile": "DEFENSIVE"}


_RULES = {"max_positions": 3, "max_allocation_pct": 0.10, "atr_multiplier": 1.5,
          "min_score_threshold": 10.0, "cash_buffer_pct": 0.50}

_TOP_BUYS = [{"sym": "BSY", "price": 31.55, "total": 11.5, "bottom_desc": ""},
             {"sym": "APA", "price": 33.825, "total": 10.7, "bottom_desc": ""}]


class TestActiveSetupSymbols(unittest.TestCase):
    """Calls the real _active_setup_symbols filter (no row-50 cap; '1'/'OK' active)."""

    def _ws(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["spacer"] * 30)                 # row 1 header
        for _ in range(340):                       # deep rows, past any old row-50 slice
            ws.append([None] * 30)
        for sym, setup in [("APA", "1"), ("XYZ", "0"), ("MMM", "OK")]:
            r = [None] * 30
            r[3], r[20] = sym, setup
            ws.append(r)
        return ws

    def test_active_setups_extracted_deep_rows(self):
        syms = game._active_setup_symbols(self._ws())
        self.assertIn("APA", syms)          # setup '1'
        self.assertIn("MMM", syms)          # setup 'OK'
        self.assertNotIn("XYZ", syms)       # setup '0'
        self.assertEqual(len(syms), 2)


class TestExecuteBuys(unittest.TestCase):
    """Calls the real _execute_buys buy-slot loop."""

    def test_fallback_on_verify_failure(self):
        # BSY (top rank) fails verification -> slot falls through to APA.
        state = _defensive_state()
        new_tx = []
        def verify(sym): return (False, "Failed check") if sym == "BSY" else (True, "Verified")
        with mock.patch.object(game, "backtrack_verify", side_effect=verify), \
             mock.patch.object(game, "calculate_bubble_z_score", return_value=None), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            n = game._execute_buys(state, list(_TOP_BUYS), 1, state["equity"] * 0.50,
                                   _RULES, "2026-07-09", "10:00", new_tx)
        self.assertEqual(n, 1)
        self.assertIn("APA", state["positions"])
        self.assertNotIn("BSY", state["positions"])
        self.assertEqual([t["symbol"] for t in new_tx], ["APA"])
        # (7746.29 - 5147.50) / 1 // 33.825 = 76 shares
        self.assertEqual(state["positions"]["APA"]["qty"], 76)

    def test_bubble_guard_rejects_and_falls_through(self):
        # BSY trades >2.5 SD above its mean -> rejected; APA (0.9 SD) fills the slot.
        state = _defensive_state()
        new_tx = []
        def z(sym): return 3.1 if sym == "BSY" else 0.9
        with mock.patch.object(game, "calculate_bubble_z_score", side_effect=z), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            n = game._execute_buys(state, list(_TOP_BUYS), 1, state["equity"] * 0.50,
                                   _RULES, "2026-07-09", "10:00", new_tx)
        self.assertEqual(n, 1)
        self.assertIn("APA", state["positions"])
        self.assertNotIn("BSY", state["positions"])

    def test_slot_cap_respected(self):
        # Only one slot -> only one buy even when both candidates are clean.
        state = _defensive_state()
        new_tx = []
        with mock.patch.object(game, "calculate_bubble_z_score", return_value=None), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            n = game._execute_buys(state, list(_TOP_BUYS), 1, state["equity"] * 0.50,
                                   _RULES, "2026-07-09", "10:00", new_tx)
        self.assertEqual(n, 1)


class TestWatchdogLogScanning(unittest.TestCase):
    def test_ignore_success_with_0_errors(self):
        mock_log = "Status: [2026-07-09 05:36:24] OHLCV: 1 recovered, 514 already current, 0 errors\n"
        with mock.patch("builtins.open", mock.mock_open(read_data=mock_log)), \
             mock.patch("os.path.exists", return_value=True):
            errors = watchdog.check_logs()
        self.assertEqual(errors, [])


class TestBubbleZScoreCalculation(unittest.TestCase):
    def setUp(self):
        self._orig_symdir = game.SYMBOL_FULL_DIR
        self.temp_dir = tempfile.TemporaryDirectory()
        game.SYMBOL_FULL_DIR = Path(self.temp_dir.name) / "Data" / "Symbol_full"
        os.makedirs(game.SYMBOL_FULL_DIR, exist_ok=True)
        # 600 rising closes; zero-padded keys so sort == chronological order.
        ts = {f"{i:05d}": {"4. close": str(100.0 + i * 0.5)} for i in range(600)}
        with open(game.SYMBOL_FULL_DIR / "BUBBLE_daily.json", "w") as f:
            json.dump({"Time Series (Daily)": ts}, f)

    def tearDown(self):
        game.SYMBOL_FULL_DIR = self._orig_symdir
        self.temp_dir.cleanup()

    def test_sufficient_history_positive_z(self):
        score = game.calculate_bubble_z_score("BUBBLE")
        self.assertIsNotNone(score)
        self.assertGreater(score, 1.5)

    def test_insufficient_history_returns_none(self):
        ts = {f"{i:05d}": {"4. close": "100"} for i in range(250)}
        with open(game.SYMBOL_FULL_DIR / "SHORT_daily.json", "w") as f:
            json.dump({"Time Series (Daily)": ts}, f)
        self.assertIsNone(game.calculate_bubble_z_score("SHORT"))

    def test_missing_file_returns_none(self):
        self.assertIsNone(game.calculate_bubble_z_score("NOPE"))


class TestCoreSatelliteAllocation(unittest.TestCase):
    def test_scarcity_asset_downsizing_and_caps(self):
        # Scenario: Portfolio equity = $10,000. Scarcity Cap = 20% ($2,000). Standard Cap = 80% ($8,000).
        # Cash = $5,000. Min Cash required = $0.
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {
                # Already hold $1,500 of scarcity assets (75 shares * $20)
                "GLD": {"qty": 75, "cost": 20.0, "is_scarcity": True}
            },
            "history": []
        }
        
        # We try to buy a scarcity asset (SLV) which costs $10 per share.
        # Max additional room in scarcity bucket is $2,000 - $1,500 = $500.
        # With $5,000 cash and 1 available slot, dynamic sizing wants to buy $5,000 worth (500 shares).
        # But the 20% scarcity cap should restrict/downsize this to exactly 50 shares ($500).
        rules = {"scarcity_allocation_pct": 0.20, "max_allocation_pct": 0.50, "atr_multiplier": 2.5}
        top_buys = [{"sym": "SLV", "price": 10.0, "total": 12.0, "industry": "Gold Mining", "bottom_desc": ""}]
        new_tx = []
        
        # Mock instruments classification so SLV is treated as scarcity
        with mock.patch("instruments.is_scarcity_asset", return_value=True), \
             mock.patch.object(game, "calculate_bubble_z_score", return_value=None), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch("risk_utils.calculate_atr", return_value=1.0):
            
            n = game._execute_buys(state, top_buys, 1, 0.0, rules, "2026-07-11", "11:30", new_tx, prices={"GLD": 20.0, "SLV": 10.0})
            
        self.assertEqual(n, 1)
        self.assertIn("SLV", state["positions"])
        # Verified: Downsized to exactly 50 shares to fit the $500 remaining room in the 20% scarcity bucket!
        self.assertEqual(state["positions"]["SLV"]["qty"], 50)
        self.assertEqual(state["positions"]["SLV"]["is_scarcity"], True)


class TestAfterHoursOrderQueuing(unittest.TestCase):
    @mock.patch("ai_portfolio_game.is_market_hours", return_value=False)
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("openpyxl.load_workbook")
    def test_after_hours_orders_are_queued(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_market_hours):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        ws.append([1, None, None, "TSLA", "Technology", None, "Bu", None, None, None, 400.0, None, None, None, None, None, None, None, None, None, "1", None, None, 0.65, 5.0, 5.0])
        mock_load_wb.return_value = wb
        
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {},
            "queued_orders": []
        }
        mock_load_game.return_value = state
        mock_get_prices.return_value = {"TSLA": 400.0}
        
        game.run_daily_ai_management(force=True, manual_profile="BALANCED")
        
        # Verify that TSLA was queued for buying instead of executed immediately
        self.assertEqual(len(state["queued_orders"]), 1)
        self.assertEqual(state["queued_orders"][0]["symbol"], "TSLA")
        self.assertEqual(state["queued_orders"][0]["type"], "BUY")
        self.assertNotIn("TSLA", state["positions"])


class TestMarketHolidayChecks(unittest.TestCase):
    @mock.patch("ai_portfolio_game.etrade.get_tokens", return_value=None)
    @mock.patch("ai_portfolio_game.datetime.datetime")
    def test_market_hours_returns_false_on_holiday(self, mock_datetime, mock_get_tokens):
        # Friday, July 3, 2026 (Independence Day Observed) at 10:00 AM
        tz_la = pytz.timezone("America/Los_Angeles")
        mock_now = tz_la.localize(real_datetime(2026, 7, 3, 10, 0, 0))
        mock_datetime.now.return_value = mock_now
        
        hours_ok = game.is_market_hours()
        self.assertFalse(hours_ok)
        
    @mock.patch("ai_portfolio_game.etrade.get_tokens", return_value=None)
    @mock.patch("ai_portfolio_game.datetime.datetime")
    def test_market_hours_returns_true_on_standard_day(self, mock_datetime, mock_get_tokens):
        # Tuesday, July 14, 2026 at 10:00 AM PST (Standard market session)
        tz_la = pytz.timezone("America/Los_Angeles")
        mock_now = tz_la.localize(real_datetime(2026, 7, 14, 10, 0, 0))
        mock_datetime.now.return_value = mock_now
        
        hours_ok = game.is_market_hours()
        self.assertTrue(hours_ok)


class TestPersistentProfileModes(unittest.TestCase):
    @mock.patch("ai_portfolio_game.get_market_regime", return_value="DEFENSIVE")
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("openpyxl.load_workbook")
    def test_persistent_manual_profile_override(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_regime):
        # 1. First run: Explicit manual override via CLI should lock into MANUAL mode
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {},
            "queued_orders": [],
            "profile": "DEFENSIVE",
            "profile_mode": "ADAPTIVE"
        }
        mock_load_game.return_value = state
        mock_get_prices.return_value = {}
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        mock_load_wb.return_value = wb

        game.run_daily_ai_management(force=True, manual_profile="BALANCED")
        self.assertEqual(state["profile"], "BALANCED")
        self.assertEqual(state["profile_mode"], "MANUAL")
        
        # 2. Second run: Automated run (no manual_profile CLI arg passed)
        # It must respect the MANUAL locked profile, even though get_market_regime() is returning "DEFENSIVE"!
        game.run_daily_ai_management(force=True, manual_profile=None)
        self.assertEqual(state["profile"], "BALANCED")
        self.assertEqual(state["profile_mode"], "MANUAL")
        
        # 3. Third run: Restore adaptive pilot via manual_profile="ADAPTIVE"
        game.run_daily_ai_management(force=True, manual_profile="ADAPTIVE")
        self.assertEqual(state["profile"], "DEFENSIVE")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")

    @mock.patch("ai_portfolio_game._has_strong_setups_today", return_value=True)
    @mock.patch("ai_portfolio_game.get_market_regime", return_value="DEFENSIVE")
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("openpyxl.load_workbook")
    def test_adaptive_cash_deployment_upgrade(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_regime, mock_strong_setups):
        # Autopilot run: profile is DEFENSIVE but cash is 50% and we have strong setups!
        state = {
            "balance": 5000.0,
            "equity": 10000.0,
            "positions": {},
            "queued_orders": [],
            "profile": "DEFENSIVE",
            "profile_mode": "ADAPTIVE"
        }
        mock_load_game.return_value = state
        mock_get_prices.return_value = {}
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Research"
        ws.append(["Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR", "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other", "Win%", "Short10", "Long60"])
        mock_load_wb.return_value = wb

        game.run_daily_ai_management(force=True, manual_profile=None)
        # It must adaptively upgrade today's strategy profile to BALANCED!
        self.assertEqual(state["profile"], "BALANCED")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")


if __name__ == "__main__":
    unittest.main()
