import os
import sys
import unittest
from pathlib import Path


# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etrade
import powergauge


_LIVE = os.getenv("AETHER_LIVE_TESTS")

class TestLiveApiContract(unittest.TestCase):
    @unittest.skipUnless(_LIVE, "Set AETHER_LIVE_TESTS=1 to run live broker/API tests")
    def test_chaikin_live_connection(self):
        """Verify that the automated Chaikin login and API are 100% online and authenticated."""
        print("\n[LIVE TEST] Testing Chaikin Analytics API connection...")
        try:
            # Attempts automated login (using the Playwright browser/session.json)
            session_data = powergauge.login(interactive=False)
            self.assertTrue(isinstance(session_data, dict) and session_data.get("jsessionid"), 
                            "Failed to retrieve a valid Chaikin session ID!")
            
            # Validates that the session actually works for live data queries
            is_valid = powergauge._validate_session(session_data)
            self.assertTrue(is_valid, "Captured Chaikin session token was rejected by Tomcat! Check your credentials/API keys.")
            
            # Fetch real symbol data
            pg = powergauge.get_symbol_data("AAPL", None, False, session_data)
            self.assertGreater(pg.price, 0, "Failed to retrieve a valid price for AAPL from Chaikin API!")
            print("  [Chaikin] Live connection 100% authenticated and online!")
        except Exception as e:
            self.fail(f"Chaikin Live Connection Contract failed: {e}")

    @unittest.skipUnless(_LIVE, "Set AETHER_LIVE_TESTS=1 to run live broker/API tests")
    def test_etrade_live_connection(self):
        """Verify that the production E*TRADE API is 100% online and authenticated."""
        print("\n[LIVE TEST] Testing E*TRADE Production API connection...")
        try:
            tokens = etrade.get_tokens("production")
            self.assertTrue(tokens, "Failed to retrieve active E*TRADE production tokens!")
            
            accts_api = etrade.get_accounts(tokens, "production")
            resp = accts_api.list_accounts(resp_format="json")
            acct_list = resp.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])
            self.assertTrue(len(acct_list) > 0, "No active accounts returned by E*TRADE, or authorization failed!")
            print("  [E*TRADE] Live connection 100% authenticated and online!")
        except Exception as e:
            self.fail(f"E*TRADE Live Connection Contract failed: {e}")

    @unittest.skipUnless(_LIVE, "Set AETHER_LIVE_TESTS=1 to run live broker/API tests")
    def test_accounts_api_contract(self):
        """Verify that the accounts endpoint returns complete, populated, and valid holdings data."""
        print("\n[LIVE TEST] Testing /api/accounts Data Contract...")
        try:
            import data_api
            res = data_api.read_accounts()
            self.assertTrue(isinstance(res, dict) and "accounts" in res, "API response must contain an 'accounts' list!")
            
            accounts = res["accounts"]
            self.assertTrue(len(accounts) > 0, "No accounts returned by the data API!")
            
            for acct in accounts:
                acct_id = acct.get("id")
                label = acct.get("label")
                holdings = acct.get("holdings", [])
                print(f"  Validating Account: {label} ({len(holdings)} holdings)...")
                
                for h in holdings:
                    sym = h.get("symbol")
                    # Delisted CVRs have no data; skip validation for them
                    if "CVR" in sym:
                        continue
                        
                    # 1. Core pricing and quantities must be populated
                    self.assertGreater(h.get("qty", 0.0), 0, f"Holding {sym} has invalid or missing quantity!")
                    self.assertGreater(h.get("buy", 0.0), 0, f"Holding {sym} has invalid or missing buy cost!")
                    self.assertGreater(h.get("current", 0.0), 0, f"Holding {sym} has invalid or missing current price!")
                    
                    # 2. Stops and targets must be populated
                    self.assertIsNotNone(h.get("stop"), f"Holding {sym} has empty Stop Price (None)!")
                    self.assertGreater(h.get("stop", 0.0), 0, f"Holding {sym} has invalid or missing Stop Price!")
                    
                    self.assertIsNotNone(h.get("target"), f"Holding {sym} has empty Target Price (None)!")
                    self.assertGreater(h.get("target", 0.0), 0, f"Holding {sym} has invalid or missing Target Price!")
                    
                    # 3. P&L and P&L % must be populated floats
                    self.assertIsNotNone(h.get("pnl"), f"Holding {sym} has empty P&L (None)!")
                    self.assertIsNotNone(h.get("pnl_pct"), f"Holding {sym} has empty P&L % (None)!")
                    
                    # 4. If it is the game account, days_held must be an integer >= 0
                    if acct_id == "game":
                        self.assertIsNotNone(h.get("days_held"), f"Virtual holding {sym} has empty Days Held (None)!")
                        self.assertGreaterEqual(h.get("days_held", -1), 0, f"Virtual holding {sym} has invalid Days Held!")
            print("  [Accounts API] Data contract is 100% complete with no empty columns!")
        except Exception as e:
            self.fail(f"Accounts API Data Contract failed: {e}")

if __name__ == "__main__":
    unittest.main()
