"""
Project AETHER: Centralized Option Pricing & Premium Capture Engine Unit Tests (R&D #26)

Deterministically verifies normal CDF approximations, Black-Scholes pricing accuracy,
and full Covered Call lifecycle settlements (worthless expiry vs. strike assignment).
"""

import unittest
from aether import options


class TestCoveredCalloptions(unittest.TestCase):

    def test_norm_cdf(self):
        """Pure Math: Verify standard normal cumulative distribution approximation (N(x))."""
        # Critical anchors
        self.assertAlmostEqual(options.norm_cdf(0.0), 0.50, places=4)
        self.assertGreater(options.norm_cdf(1.0), 0.84)
        self.assertLess(options.norm_cdf(-1.0), 0.16)

    def test_black_scholes_call_pricing(self):
        """Pure Math: Verify Black-Scholes European Call pricing under varying volatilities."""
        # 1. At-The-Money (S = K = 100) Call price must be positive on active volatility
        c_atm = options.calculate_black_scholes_call(S=100.0, K=100.0, T=7.0/365.0, r=0.04, sigma=0.30)
        self.assertGreater(c_atm, 0.50)
        self.assertLess(c_atm, 2.50)
        
        # 2. Deep Out-Of-The-Money (S = 100, K = 120) Call price must decay close to 0.01 (min limit)
        c_otm = options.calculate_black_scholes_call(S=100.0, K=120.0, T=7.0/365.0, r=0.04, sigma=0.30)
        self.assertEqual(c_otm, 0.01)
        
        # 3. Deep In-The-Money (S = 100, K = 80) Call price must equal its intrinsic value (~$20)
        c_itm = options.calculate_black_scholes_call(S=100.0, K=80.0, T=7.0/365.0, r=0.04, sigma=0.30)
        self.assertGreaterEqual(c_itm, 19.90)

    def test_select_covered_call(self):
        """Pure Math: Verify OTM Covered Call target strike selection and standard interval rounding."""
        # Stock under $150 (rounds to nearest $2.50)
        opt_under_150 = options.select_covered_call("AAPL", current_price=141.20, atr=4.0)
        # Target: 141.20 + 1.5 * 4 = 147.20. Rounds to nearest $2.50 interval -> 147.50
        self.assertEqual(opt_under_150["strike"], 147.50)
        self.assertGreater(opt_under_150["premium_price"], 0.0)
        
        # Stock above $150 (rounds to nearest $5.00)
        opt_above_150 = options.select_covered_call("SAM", current_price=185.00, atr=8.0)
        # Target: 185.00 + 1.5 * 8 = 197.00. Rounds to nearest $5.00 interval -> 195.00 or 200.00 -> 195.00
        self.assertEqual(opt_above_150["strike"], 195.00)

    def test_resolve_expiring_options_worthless_expiry(self):
        """Lifecycle: Verify that Covered Calls expiring BELOW strike settle as a worthless expiry (we keep premium)."""
        mock_state = {
            "balance": 1000.0,
            "positions": {
                "AAPL": {
                    "qty": 10,
                    "cost": 100.0,
                    "stop_loss": 105.0,
                    "written_call": {
                        "strike": 115.0,
                        "premium": 1.50,
                        "expiration_date": "2026-08-14",
                        "qty": 10
                    }
                }
            },
            "history": []
        }
        
        # Current price ($112) is below strike ($115) -> option expires worthless!
        prices = {"AAPL": 112.0}
        options.resolve_expiring_options(mock_state, today_str="2026-08-14", prices=prices)
        
        # Settle asserts:
        # 1. Position remains active
        self.assertIn("AAPL", mock_state["positions"])
        # 2. Option liability is cleanly removed
        self.assertNotIn("written_call", mock_state["positions"]["AAPL"])
        # 3. Worthless expiry record logged to history
        self.assertEqual(len(mock_state["history"]), 1)
        self.assertEqual(mock_state["history"][0]["type"], "OPTION_EXPIRY")
        self.assertEqual(mock_state["history"][0]["pnl"], 15.00) # 1.50 * 10 qty

    def test_resolve_expiring_options_strike_assignment(self):
        """Lifecycle: Verify that Covered Calls expiring AT/ABOVE strike settle as an assignment (stock called away)."""
        mock_state = {
            "balance": 1000.0,
            "positions": {
                "AAPL": {
                    "qty": 10,
                    "cost": 100.0,
                    "stop_loss": 105.0,
                    "written_call": {
                        "strike": 115.0,
                        "premium": 1.50,
                        "expiration_date": "2026-08-14",
                        "qty": 10
                    }
                }
            },
            "history": []
        }
        
        # Current price ($118) is above strike ($115) -> stock is called away!
        prices = {"AAPL": 118.0}
        options.resolve_expiring_options(mock_state, today_str="2026-08-14", prices=prices)
        
        # Settle asserts:
        # 1. Position is deleted (called away!)
        self.assertNotIn("AAPL", mock_state["positions"])
        # 2. Cash balance is credited with strike revenue (115.0 * 10 = $1,150) -> total cash becomes $2,150
        self.assertEqual(mock_state["balance"], 2150.0)
        # 3. Assignment sale logged to history with combined P&L (Stock gain + premium kept = $150 stock + $15 premium = $165 total)
        self.assertEqual(len(mock_state["history"]), 1)
        self.assertEqual(mock_state["history"][0]["type"], "OPTION_ASSIGNMENT")
        self.assertEqual(mock_state["history"][0]["pnl"], 165.0)


if __name__ == "__main__":
    unittest.main()
