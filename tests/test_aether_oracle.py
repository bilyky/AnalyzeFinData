"""
Dedicated unit tests for the AETHER Oracle financial advisory logic.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aether_oracle as oracle

class TestAETHEROracle(unittest.TestCase):

    def test_get_oracle_account_returns_none_on_error(self):
        """Zero-Trust: when data_api raises, get_oracle_account returns None (never a fabricated balance)."""
        with mock.patch.object(oracle.CFG, "oracle_account", "9999"), \
             mock.patch("data_api.read_accounts", side_effect=Exception("API Error")):
            self.assertIsNone(oracle.get_oracle_account())

    def test_get_oracle_account_returns_none_when_not_found(self):
        """When the configured account is absent from live data, return None rather than a guess."""
        mock_data = {"accounts": [{"id": "1111", "label": "Other Account", "equity": 1000}]}
        with mock.patch.object(oracle.CFG, "oracle_account", "9999"), \
             mock.patch("data_api.read_accounts", return_value=mock_data):
            self.assertIsNone(oracle.get_oracle_account())

    def test_get_oracle_account_finds_configured(self):
        """get_oracle_account scans accounts and returns the one matching the configured id."""
        mock_data = {
            "accounts": [
                {"id": "1111", "label": "Other Account", "equity": 1000},
                {"id": "9999", "label": "Target Account", "equity": 24000}
            ]
        }
        with mock.patch.object(oracle.CFG, "oracle_account", "9999"), \
             mock.patch("data_api.read_accounts", return_value=mock_data):
            acct = oracle.get_oracle_account()
            self.assertEqual(acct["id"], "9999")
            self.assertEqual(acct["label"], "Target Account")
            self.assertEqual(acct["equity"], 24000)

    def test_run_advisory_unavailable_when_account_none(self):
        """run_oracle_advisory renders the data-unavailable notice instead of advising on nothing."""
        with mock.patch("aether_oracle.get_oracle_account", return_value=None):
            html = oracle.run_oracle_advisory()
        self.assertIn("unavailable", html.lower())

    def test_audit_oracle_portfolio(self):
        """audit_oracle_portfolio detects stop breaches and momentum decay correctly."""
        acct = {
            "id": "9999",
            "holdings": [
                # Normal healthy holding
                {"symbol": "AAPL", "qty": 10, "buy": 150.0, "current": 160.0, "stop": 140.0, "pnl_pct": 6.67, "s10": 3.0, "l60": 5.0, "status": "Hold"},
                # Stop breach (current <= stop)
                {"symbol": "MSFT", "qty": 5, "buy": 400.0, "current": 370.0, "stop": 380.0, "pnl_pct": -7.5, "s10": -1.0, "l60": 2.0, "status": "Neutral"},
                # Momentum decay (combined score < -2.0)
                {"symbol": "TSLA", "qty": 8, "buy": 220.0, "current": 210.0, "stop": 190.0, "pnl_pct": -4.54, "s10": -4.0, "l60": -1.0, "status": "REDUCE"},
                # Overridden Stop breach (current <= stop, but has shadow_verdict: HOLD)
                {"symbol": "GOOG", "qty": 5, "buy": 150.0, "current": 140.0, "stop": 150.0, "pnl_pct": -6.67, "s10": -1.0, "l60": -1.0, "status": "Neutral", "shadow_verdict": "HOLD"},
                # Overridden Momentum decay (combined score < -2.0, but has shadow_verdict: FLAG-FOR-REVIEW)
                {"symbol": "META", "qty": 5, "buy": 350.0, "current": 340.0, "stop": 300.0, "pnl_pct": -2.85, "s10": -4.0, "l60": -1.0, "status": "REDUCE", "shadow_verdict": "FLAG-FOR-REVIEW"}
            ]
        }
        
        sells, holds = oracle.audit_oracle_portfolio(acct)
        
        # We expect AAPL, GOOG and META to be in holds, MSFT and TSLA to be in sells
        self.assertEqual(len(holds), 3)
        hold_syms = {h["symbol"] for h in holds}
        self.assertIn("AAPL", hold_syms)
        self.assertIn("GOOG", hold_syms)
        self.assertIn("META", hold_syms)
        
        # Verify the overridden actions and reasons
        goog_pos = next(h for h in holds if h["symbol"] == "GOOG")
        self.assertEqual(goog_pos["action"], "HOLD")
        self.assertIn("[AI EXIT OVERRIDE]", goog_pos["reason"])
        
        meta_pos = next(h for h in holds if h["symbol"] == "META")
        self.assertEqual(meta_pos["action"], "WATCH")
        self.assertIn("[AI EXIT OVERRIDE]", meta_pos["reason"])
        
        self.assertEqual(len(sells), 2)
        sell_syms = {s["symbol"] for s in sells}
        self.assertIn("MSFT", sell_syms)
        self.assertIn("TSLA", sell_syms)

    def test_get_oracle_buy_candidates(self):
        """get_oracle_buy_candidates filters held positions, checks setups, and sorts by combined score descending."""
        acct = {
            "id": "9999",
            "holdings": [
                {"symbol": "AAPL", "qty": 10, "buy": 150.0}
            ]
        }
        
        mock_research = {
            "rows": [
                # Held symbol (AAPL) - must be ignored
                {"symbol": "AAPL", "setup": True, "s10": 4.0, "l60": 6.0, "combined": 10.0, "price": 160.0, "stop": 150.0, "target": 180.0, "pgr": "Bu"},
                # Not a setup - must be ignored
                {"symbol": "MSFT", "setup": False, "s10": 5.0, "l60": 5.0, "combined": 10.0, "price": 400.0, "stop": 380.0, "target": 440.0, "pgr": "Bu"},
                # High score candidate (favorable R:R = 2.0 >= 1.5)
                {"symbol": "GOOGL", "setup": True, "s10": 5.0, "l60": 6.0, "combined": 11.0, "price": 180.0, "stop": 160.0, "target": 220.0, "pgr": "Bu"},
                # Low score candidate (below momentum floor s10 < 2.5)
                {"symbol": "AMZN", "setup": True, "s10": 1.5, "l60": 8.0, "combined": 9.5, "price": 190.0, "stop": 170.0, "target": 230.0, "pgr": "Bu"},
                # Unfavorable R:R candidate (R:R = 0.5 < 1.5) - must be ignored
                {"symbol": "QYLD", "setup": True, "s10": 5.0, "l60": 5.0, "combined": 10.0, "price": 18.0, "stop": 17.0, "target": 18.5, "pgr": "Be"},
                # Valid medium candidate (favorable R:R = 2.0 >= 1.5)
                {"symbol": "NVDA", "setup": True, "s10": 3.0, "l60": 4.0, "combined": 7.0, "price": 120.0, "stop": 100.0, "target": 160.0, "pgr": "Bu"}
            ]
        }
        
        with mock.patch("data_api.read_research", return_value=mock_research), \
             mock.patch("ai_portfolio_game.calculate_bubble_z_score", return_value=1.0):
            buys = oracle.get_oracle_buy_candidates(acct)
            
            # We expect GOOGL, NVDA, and QYLD (QYLD is waived due to elite Combined >= 8.0 score!)
            self.assertEqual(len(buys), 3)
            self.assertEqual(buys[0]["symbol"], "GOOGL")
            self.assertEqual(buys[1]["symbol"], "QYLD")
            self.assertEqual(buys[2]["symbol"], "NVDA")

    def test_generate_oracle_html(self):
        """generate_oracle_html runs successfully and contains standard Oracle copy."""
        acct = {"id": "9999", "balance": 100.0, "equity": 20000.0, "holdings": []}
        sells = [{"symbol": "MSFT", "qty": 5, "cost": 400.0, "current": 370.0, "stop": 380.0, "pnl_pct": -7.5, "s10": -1.0, "l60": 2.0, "reason": "Breached"}]
        holds = [{"symbol": "AAPL", "qty": 10, "cost": 150.0, "current": 160.0, "pnl_pct": 6.67, "total": 8.0}]
        buys = [{"symbol": "GOOGL", "price": 180.0, "stop": 160.0, "target": 210.0, "combined": 11.0, "pgr": "Bu", "patterns": "Breakout"}]
        
        html = oracle.generate_oracle_html(acct, sells, holds, buys)
        self.assertIn("AETHER Oracle Market Advisory", html)
        self.assertIn("Double Real Account (...9999)", html)
        self.assertIn("GOOGL", html)
        self.assertIn("MSFT", html)
        self.assertIn("AAPL", html)

if __name__ == "__main__":
    unittest.main()
