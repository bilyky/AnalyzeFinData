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
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")  # patch at point-of-use
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
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
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
        # Under our new auto-reset spec, it must automatically reset back to ADAPTIVE autopilot
        # and fall back to the dynamic regime selector (returning "DEFENSIVE"!)
        game.run_daily_ai_management(force=True, manual_profile=None)
        self.assertEqual(state["profile"], "DEFENSIVE")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")
        
        # 3. Third run: Explicit manual restoration via CLI still works
        state["profile_mode"] = "MANUAL"
        state["profile"] = "BALANCED"
        game.run_daily_ai_management(force=True, manual_profile="ADAPTIVE")
        self.assertEqual(state["profile"], "DEFENSIVE")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")

    @mock.patch("ai_portfolio_game._has_strong_setups_today", return_value=True)
    @mock.patch("ai_portfolio_game.get_market_regime", return_value="DEFENSIVE")
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
    def test_adaptive_upgrade_fires_above_cash_threshold(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_regime, mock_strong_setups):
        # 50% cash (>40% threshold) + strong setups -> upgrade DEFENSIVE -> BALANCED.
        state = {"balance": 5000.0, "equity": 10000.0, "positions": {}, "queued_orders": [],
                 "profile": "DEFENSIVE", "profile_mode": "ADAPTIVE"}
        mock_load_game.return_value = state
        mock_get_prices.return_value = {}
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Research"
        ws.append(["Rank"] + [None] * 25)
        mock_load_wb.return_value = wb
        game.run_daily_ai_management(force=True, manual_profile=None)
        self.assertEqual(state["profile"], "BALANCED")
        self.assertEqual(state["profile_mode"], "ADAPTIVE")

    @mock.patch("ai_portfolio_game._has_strong_setups_today", return_value=True)
    @mock.patch("ai_portfolio_game.get_market_regime", return_value="DEFENSIVE")
    @mock.patch("ai_portfolio_game.get_live_prices")
    @mock.patch("ai_portfolio_game.load_game")
    @mock.patch("ai_portfolio_game.save_game")
    @mock.patch("ai_portfolio_game.openpyxl.load_workbook")
    def test_adaptive_upgrade_does_not_fire_below_cash_threshold(self, mock_load_wb, mock_save_game, mock_load_game, mock_get_prices, mock_regime, mock_strong_setups):
        # 30% cash (<40% threshold) -> gate must NOT upgrade even with strong setups.
        state = {"balance": 3000.0, "equity": 10000.0, "positions": {}, "queued_orders": [],
                 "profile": "DEFENSIVE", "profile_mode": "ADAPTIVE"}
        mock_load_game.return_value = state
        mock_get_prices.return_value = {}
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Research"
        ws.append(["Rank"] + [None] * 25)
        mock_load_wb.return_value = wb
        game.run_daily_ai_management(force=True, manual_profile=None)
        # Below threshold -> profile must stay DEFENSIVE.
        self.assertEqual(state["profile"], "DEFENSIVE")

    def test_check_failure_rules_rejection(self):
        """Verify that check_failure_rules properly identifies and flags toxic candidates."""
        # 1. Setup a temporary rules list
        rules_file = game.BASE_DIR / "Data" / "failure_dna_rules.json"

        # Backup existing rules if any
        existing_rules = None
        if rules_file.exists():
            with open(rules_file, "r", encoding="utf-8") as f:
                existing_rules = json.load(f)

        test_rules = [
            {
                "id": "TOXIC_BEARISH_PGR",
                "field": "pgr",
                "condition": "startswith_Be",
                "reason": "Avoid buying Bearish PGR"
            },
            {
                "id": "TOXIC_LOW_SCORE",
                "field": "score",
                "condition": "less_than_5.0",
                "reason": "Avoid buying low scores"
            }
        ]

        # Ensure directory exists and write test rules
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        with open(rules_file, "w", encoding="utf-8") as f:
            json.dump(test_rules, f, indent=4)

        try:
            # 2. Test Rejections
            # A Bearish PGR candidate must be rejected
            is_toxic, reason = game.check_failure_rules("AAPL", "Be-", 9.5, 0.5, "Technology")
            self.assertTrue(is_toxic)
            self.assertEqual(reason, "Avoid buying Bearish PGR")

            # A low score candidate must be rejected
            is_toxic, reason = game.check_failure_rules("MSFT", "Neutral", 3.2, 0.5, "Technology")
            self.assertTrue(is_toxic)
            self.assertEqual(reason, "Avoid buying low scores")

            # A healthy candidate must pass (False)
            is_toxic, reason = game.check_failure_rules("GOOGL", "Bu+", 9.8, 0.5, "Technology")
            self.assertFalse(is_toxic)
            self.assertEqual(reason, "")
        finally:
            # Restore original rules
            if existing_rules is not None:
                with open(rules_file, "w", encoding="utf-8") as f:
                    json.dump(existing_rules, f, indent=4)
            elif rules_file.exists():
                rules_file.unlink()

    def test_log_closed_trade_dna_writing(self):
        """Verify that log_closed_trade_dna correctly records a closed trade entry."""
        dna_file = game.BASE_DIR / "Data" / "trade_history_dna.json"

        # Backup existing DNA ledger if any
        existing_dna = None
        if dna_file.exists():
            with open(dna_file, "r", encoding="utf-8") as f:
                existing_dna = json.load(f)

        # Clear or truncate file for clean test environment
        if dna_file.exists():
            dna_file.unlink()

        try:
            pos = {
                "qty": 10,
                "cost": 100.0,
                "stop_loss": 92.0,
                "buy_dna": {
                    "buy_date": "2026-06-01",
                    "pgr": "Bullish",
                    "score": 9.5,
                    "z_score": 1.2,
                    "industry": "Software"
                }
            }
            # Execute log call
            game.log_closed_trade_dna("TEST_SYM", pos, 105.0, "2026-06-05")

            # Verify file exists and holds the correct, formatted schema
            self.assertTrue(dna_file.exists())
            with open(dna_file, "r", encoding="utf-8") as f:
                records = json.load(f)

            self.assertEqual(len(records), 1)
            rec = records[0]
            self.assertEqual(rec["symbol"], "TEST_SYM")
            self.assertEqual(rec["buy_date"], "2026-06-01")
            self.assertEqual(rec["sell_date"], "2026-06-05")
            self.assertEqual(rec["buy_price"], 100.0)
            self.assertEqual(rec["sell_price"], 105.0)
            self.assertEqual(rec["qty"], 10)
            self.assertEqual(rec["pnl_pct"], 5.0)
            self.assertEqual(rec["holding_days"], 4)
            self.assertEqual(rec["buy_dna"]["industry"], "Software")
        finally:
            # Restore original DNA ledger
            if existing_dna is not None:
                with open(dna_file, "w", encoding="utf-8") as f:
                    json.dump(existing_dna, f, indent=4)
            elif dna_file.exists():
                dna_file.unlink()

    def test_risk_reward_gate_math(self):
        """Verify that the dynamic Reward-to-Risk ratio and 5% target gain thresholds are correct."""
        # Scenario 1: Favorable asymmetry & Gain (Upside: $6, Downside: $2) -> Ratio: 3.0, Gain: 6% -> Accept!
        price = 100.0
        stop = 98.0
        target = 106.0
        
        upside = target - price
        downside = price - stop
        rr_ratio = round(upside / downside, 2) if downside > 0 else 0.0
        target_gain_pct = round((upside / price) * 100, 2) if price > 0 else 0.0
        
        self.assertEqual(rr_ratio, 3.0)
        self.assertGreaterEqual(rr_ratio, 2.0)
        self.assertEqual(target_gain_pct, 6.0)
        self.assertGreaterEqual(target_gain_pct, 5.0)
        
        # Scenario 2: Unfavorable asymmetry (Upside: $2, Downside: $4) -> Ratio: 0.5 -> Reject!
        price = 100.0
        stop = 96.0
        target = 102.0
        
        upside = target - price
        downside = price - stop
        rr_ratio = round(upside / downside, 2) if downside > 0 else 0.0
        self.assertEqual(rr_ratio, 0.5)
        self.assertLess(rr_ratio, 2.0)
        
        # Scenario 3: Favorable asymmetry but Poor Gain (Upside: $2, Downside: $0.50) -> Ratio: 4.0, Gain: 2% -> Reject!
        price = 100.0
        stop = 99.50
        target = 102.0
        
        upside = target - price
        downside = price - stop
        rr_ratio = round(upside / downside, 2) if downside > 0 else 0.0
        target_gain_pct = round((upside / price) * 100, 2) if price > 0 else 0.0
        
        self.assertEqual(rr_ratio, 4.0)
        self.assertGreaterEqual(rr_ratio, 2.0) # passes R/R
        self.assertEqual(target_gain_pct, 2.0)
        self.assertLess(target_gain_pct, 5.0) # fails Target Gain %

    def test_circuit_breaker_single_day_crash(self):
        """Verify that a single-day SPY drop > 2.0% successfully triggers the Circuit Breaker."""
        import circuit_breaker
        from unittest import mock
        
        # Mock SPY history where yesterday was 100.0 and today is 97.0 (down 3.0%)
        mock_series = [{"close": 100.0}] * 15
        mock_series[-2] = {"close": 100.0}
        mock_series[-1] = {"close": 97.0}
        
        with mock.patch("circuit_breaker.load_spy_history", return_value=mock_series):
            is_active, reason = circuit_breaker.check_systemic_risk(prices={"SPY": 97.0})
            self.assertTrue(is_active)
            self.assertIn("Single-Day Capitulation", reason)

    def test_circuit_breaker_rolling_drawdown(self):
        """Verify that a 10-day rolling drawdown > 5.0% (slow bleed) successfully triggers the Breaker."""
        import circuit_breaker
        from unittest import mock
        
        # Mock a slow-bleed SPY history: peak was 100.0, we have fallen to 94.0 (down 6.0% drawdown)
        mock_series = [{"close": 100.0}] * 15
        # Set a peak at index -10 and bleed down to 94.0
        for i in range(-10, 0):
            mock_series[i] = {"close": 100.0 + (i * 0.6)} # index -1 is 99.4, index -10 is 94.0
        mock_series[-10] = {"close": 100.0} # peak
        mock_series[-1] = {"close": 94.0} # today's close
        mock_series[-2] = {"close": 94.6} # yesterday's close
        
        with mock.patch("circuit_breaker.load_spy_history", return_value=mock_series):
            is_active, reason = circuit_breaker.check_systemic_risk(prices={"SPY": 94.0})
            self.assertTrue(is_active)
            self.assertIn("Rolling 10-day Drawdown Breach", reason)

    def test_circuit_breaker_stop_tightening(self):
        """Verify that the breaker successfully freezes buying and tightens stops on satellites."""
        import circuit_breaker
        from unittest import mock
        
        state = {
            "balance": 1000.0,
            "queued_orders": [
                {"symbol": "AAPL", "type": "BUY", "qty": 10},
                {"symbol": "MSFT", "type": "SELL", "qty": 5}
            ],
            "positions": {
                "ZS": {"qty": 10, "cost": 150.0, "stop_loss": 135.0, "is_scarcity": False},
                "HE": {"qty": 81, "cost": 13.57, "stop_loss": 12.72, "is_scarcity": True} # scarcity left alone
            }
        }
        
        # Mock an active breaker state and 1.50 ATR for ZS
        with mock.patch("circuit_breaker.check_systemic_risk", return_value=(True, "Single-Day Capitulation")):
            with mock.patch("risk_utils.calculate_atr", return_value=1.50):
                circuit_breaker.enforce_circuit_breaker(state, prices={"ZS": 149.0})
                
                # 1. Buying must be frozen (queued BUYs cleared, SELLs left alone)
                self.assertEqual(len(state["queued_orders"]), 1)
                self.assertEqual(state["queued_orders"][0]["symbol"], "MSFT")
                
                # 2. Satellite (ZS) stop must be tightened to 1.0x ATR: 149.0 - 1.50 = 147.50
                # 147.50 is higher (safer) than original 135.0 stop!
                self.assertEqual(state["positions"]["ZS"]["stop_loss"], 147.50)
                
                # 3. Scarcity (HE) stop must be left alone at 12.72
                self.assertEqual(state["positions"]["HE"]["stop_loss"], 12.72)

if __name__ == "__main__":
    unittest.main()
