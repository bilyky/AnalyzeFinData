"""
Project AETHER: Centralized Option Pricing & Premium Capture Engine Unit Tests (R&D #26)

Deterministically verifies the normal CDF, Black-Scholes pricing, OTM strike selection, and the
full Covered Call lifecycle (write -> worthless expiry / strike assignment / buy-to-close),
including the ledger invariant that a written premium is booked exactly once.
"""

import unittest
from aether import options


# Fake Research worksheet: Symbol at index 3, ATR at index 10 (NOT the old magic index 23),
# so the tests fail if column resolution reverts to a hardcoded position.
_HEADER = [None, None, None, "Symbol", None, None, None, None, None, None, "ATR"]


def _research_row(sym, atr):
    row = [None] * 11
    row[3] = sym
    row[10] = atr
    return row


class _FakeWS:
    """Minimal openpyxl-worksheet stand-in exposing iter_rows(min_row/max_row, values_only)."""

    def __init__(self, header, rows):
        self._all = [header] + list(rows)

    def iter_rows(self, min_row=1, max_row=None, values_only=True):
        start = min_row - 1
        end = max_row if max_row is not None else len(self._all)
        return iter(self._all[start:end])


class TestCoveredCallOptions(unittest.TestCase):

    def test_norm_cdf(self):
        """Pure Math: standard normal cumulative distribution N(x) at critical anchors."""
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
        """Verify OTM strike selection, standard-interval rounding, and the 5% OTM floor."""
        # Stock under $150 rounds to nearest $2.50. Here the 5% floor (141.20*1.05 = 148.26)
        # dominates 1.5*ATR (147.20); both round to 147.50, so this case does not exercise the floor.
        opt_under_150 = options.select_covered_call("AAPL", current_price=141.20, atr=4.0)
        self.assertEqual(opt_under_150["strike"], 147.50)
        self.assertGreater(opt_under_150["premium_price"], 0.0)

        # Stock above $150 rounds to nearest $5.00. Target 185 + 1.5*8 = 197 -> 195.00.
        opt_above_150 = options.select_covered_call("SAM", current_price=185.00, atr=8.0)
        self.assertEqual(opt_above_150["strike"], 195.00)

        # Floor-dominant case: tiny ATR so 1.5*ATR is negligible; the 5% OTM floor must set the
        # strike. Without the floor this would round to ~100 (<= price); with it, 100*1.05 -> 105.
        opt_floor = options.select_covered_call("XYZ", current_price=100.0, atr=0.5)
        self.assertEqual(opt_floor["strike"], 105.0)
        self.assertGreater(opt_floor["strike"], 100.0)

    def test_resolve_expiring_options_worthless_expiry(self):
        """Below-strike expiry keeps the stock; premium was booked at write, so expiry pnl is 0."""
        mock_state = {
            "balance": 1000.0,
            "positions": {
                "AAPL": {
                    "qty": 10, "cost": 100.0, "stop_loss": 105.0,
                    "written_call": {"strike": 115.0, "premium": 1.50, "expiration_date": "2026-08-14", "qty": 10},
                }
            },
            "history": [],
        }

        # Current price ($112) below strike ($115) -> option expires worthless.
        options.resolve_expiring_options(mock_state, today_str="2026-08-14", prices={"AAPL": 112.0})

        self.assertIn("AAPL", mock_state["positions"])
        self.assertNotIn("written_call", mock_state["positions"]["AAPL"])
        self.assertEqual(len(mock_state["history"]), 1)
        self.assertEqual(mock_state["history"][0]["type"], "OPTION_EXPIRY")
        # pnl is 0.0 here (premium already realized at OPTION_WRITE); no double-count.
        self.assertEqual(mock_state["history"][0]["pnl"], 0.0)
        # Settlement moved no cash on expiry.
        self.assertEqual(mock_state["balance"], 1000.0)

    def test_resolve_expiring_options_strike_assignment(self):
        """At/above-strike expiry calls the stock away; assignment pnl is the stock gain only."""
        mock_state = {
            "balance": 1000.0,
            "positions": {
                "AAPL": {
                    "qty": 10, "cost": 100.0, "stop_loss": 105.0,
                    "written_call": {"strike": 115.0, "premium": 1.50, "expiration_date": "2026-08-14", "qty": 10},
                }
            },
            "history": [],
        }

        # Current price ($118) above strike ($115) -> stock is called away at the strike.
        options.resolve_expiring_options(mock_state, today_str="2026-08-14", prices={"AAPL": 118.0})

        self.assertNotIn("AAPL", mock_state["positions"])
        # Cash credited with strike revenue (115 * 10 = $1,150) -> $2,150.
        self.assertEqual(mock_state["balance"], 2150.0)
        self.assertEqual(len(mock_state["history"]), 1)
        self.assertEqual(mock_state["history"][0]["type"], "OPTION_ASSIGNMENT")
        # pnl is the realized stock capital gain only ((115-100)*10 = $150); premium was booked at write.
        self.assertEqual(mock_state["history"][0]["pnl"], 150.0)

    def test_missing_quote_defers_settlement(self):
        """No live quote on the settlement day must DEFER (keep the liability), not settle on cost basis.

        Regression guard for the bug where a missing quote defaulted to cost (< strike), silently
        forcing the favorable 'expires worthless' branch.
        """
        mock_state = {
            "balance": 1000.0,
            "positions": {
                "AAPL": {
                    "qty": 10, "cost": 100.0, "stop_loss": 105.0,
                    "written_call": {"strike": 115.0, "premium": 1.50, "expiration_date": "2026-08-14", "qty": 10},
                }
            },
            "history": [],
        }

        options.resolve_expiring_options(mock_state, today_str="2026-08-14", prices={})  # no quote

        self.assertIn("written_call", mock_state["positions"]["AAPL"])  # deferred, not settled
        self.assertEqual(mock_state["history"], [])
        self.assertEqual(mock_state["balance"], 1000.0)

    def test_execute_weekly_pass_writes_on_winner_and_books_premium_once(self):
        """A risk-locked winner gets a call written with the premium booked exactly once; a
        non-risk-locked position is skipped. Then a worthless expiry must not re-book the premium."""
        state = {
            "balance": 0.0,
            "positions": {
                "AAPL": {"qty": 10, "cost": 100.0, "stop_loss": 100.0},  # winner, risk-locked -> qualifies
                "MSFT": {"qty": 5, "cost": 100.0, "stop_loss": 90.0},    # winner but NOT risk-locked -> skip
            },
            "history": [],
        }
        prices = {"AAPL": 110.0, "MSFT": 110.0}
        ws = _FakeWS(_HEADER, [_research_row("AAPL", 4.0), _research_row("MSFT", 4.0)])

        written = options.execute_weekly_covered_call_pass(state, "2026-08-17", prices, ws)

        self.assertEqual(len(written), 1)
        self.assertIn("written_call", state["positions"]["AAPL"])
        self.assertNotIn("written_call", state["positions"]["MSFT"])
        # Premium was added to the balance exactly once, matching the single OPTION_WRITE pnl.
        premium_usd = state["history"][0]["pnl"]
        self.assertGreater(premium_usd, 0.0)
        self.assertAlmostEqual(state["balance"], premium_usd)

        # Expire worthless next week: pnl must be 0 so the ledger stays consistent with the balance.
        call = state["positions"]["AAPL"]["written_call"]
        options.resolve_expiring_options(state, today_str=call["expiration_date"], prices={"AAPL": call["strike"] - 5.0})
        self.assertEqual(state["history"][-1]["type"], "OPTION_EXPIRY")
        self.assertEqual(state["history"][-1]["pnl"], 0.0)
        # Ledger invariant: total booked pnl equals the net cash the premium put on the balance.
        self.assertAlmostEqual(sum(t["pnl"] for t in state["history"]), state["balance"])

    def test_unwind_buys_back_short_call(self):
        """Buy-to-close debits the balance by the BS fair value and clears the liability."""
        state = {"balance": 1000.0, "positions": {}, "history": []}
        pos = {"qty": 10, "cost": 100.0,
               "written_call": {"strike": 115.0, "premium": 1.50, "expiration_date": "2026-08-24", "qty": 10}}

        options.unwind_option_liability_if_held("AAPL", pos, state, current_price=112.0, today_str="2026-08-17")

        self.assertNotIn("written_call", pos)
        self.assertLess(state["balance"], 1000.0)  # BTC cost debited
        self.assertEqual(state["history"][-1]["type"], "OPTION_BUY_TO_CLOSE")
        self.assertLess(state["history"][-1]["pnl"], 0.0)

    def test_unwind_malformed_call_clears_without_btc(self):
        """A written_call missing strike/qty is cleared without touching the balance."""
        state = {"balance": 1000.0, "positions": {}, "history": []}
        pos = {"qty": 10, "cost": 100.0, "written_call": {"premium": 1.50, "expiration_date": "2026-08-24"}}

        options.unwind_option_liability_if_held("AAPL", pos, state, current_price=112.0, today_str="2026-08-17")

        self.assertNotIn("written_call", pos)
        self.assertEqual(state["balance"], 1000.0)
        self.assertEqual(state["history"], [])


if __name__ == "__main__":
    unittest.main()
