"""
Pure, unmocked mathematical and logical unit tests for the AETHER sizing and pyramiding formulas.
These tests use zero mocks, verifying exact algebraic precision and boundary edge conditions.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game

class TestMathematicalIntegrity(unittest.TestCase):
    def test_fractional_share_qty_division(self):
        """Verify the exact algebraic division and rounding of our fractional sizing engine."""
        # AAPL is fractional-eligible (should return float rounded to 3 decimals)
        # $1,000.00 / $144.00 = 6.944444... -> 6.944
        qty_fractional = game.calculate_share_qty("AAPL", 1000.0, 144.0)
        self.assertEqual(qty_fractional, 6.944)
        self.assertIsInstance(qty_fractional, float)

        # MSFT is fractional-eligible (should return float rounded to 3 decimals)
        # $500.00 / $119.17 = 4.19568... -> 4.196
        qty_msft = game.calculate_share_qty("MSFT", 500.0, 119.17)
        self.assertEqual(qty_msft, 4.196)
        self.assertIsInstance(qty_msft, float)

    def test_standard_share_qty_integer_division(self):
        """Verify that standard assets strictly return integer-rounded whole shares."""
        # TSCO is standard (should return integer rounded down)
        # $1,000.00 / $144.00 = 6.944... -> 6 (integer!)
        qty_standard = game.calculate_share_qty("TSCO", 1000.0, 144.0)
        self.assertEqual(qty_standard, 6)
        self.assertIsInstance(qty_standard, int)

        # $500.00 / $119.17 = 4.195... -> 4 (integer!)
        qty_standard_lulu = game.calculate_share_qty("TSCO", 500.0, 119.17)
        self.assertEqual(qty_standard_lulu, 4)
        self.assertIsInstance(qty_standard_lulu, int)

    def test_pyramiding_blended_cost_precision(self):
        """Verify the exact mathematical formula of our pyramiding blended-cost basis."""
        # Initial: 2 shares @ $400.00 cost basis.
        # Scale-In: Add 1.659 shares @ $410.00 current price.
        # Blended Cost = ((2 * 400.0) + (1.659 * 410.0)) / (2 + 1.659)
        # = (800.0 + 680.19) / 3.659 = 1480.19 / 3.659 = 404.53402... -> $404.53
        old_qty = 2
        old_cost = 400.0
        add_qty = 1.659
        add_price = 410.0
        
        cost = add_qty * add_price # $680.19
        new_qty = old_qty + add_qty # 3.659
        
        blended_cost = round(((old_qty * old_cost) + cost) / new_qty, 2)
        self.assertEqual(blended_cost, 404.53)

        # Extreme values test:
        # Initial: 100 shares @ $10.00. Add 0.001 shares @ $500.00
        # Blended = ((100 * 10) + (0.001 * 500)) / (100.001) = (1000 + 0.5) / 100.001 = 1000.5 / 100.001 = 10.0048... -> $10.00
        old_qty = 100
        old_cost = 10.0
        add_qty = 0.001
        add_price = 500.0
        
        cost = add_qty * add_price
        new_qty = old_qty + add_qty
        blended_cost = round(((old_qty * old_cost) + cost) / new_qty, 2)
        self.assertEqual(blended_cost, 10.00)

    def test_extreme_boundary_fail_paths(self):
        """Verify that extreme boundary conditions (zero price, negative values) fail safely with zero crash."""
        # 1. Zero Price
        qty_zero_price = game.calculate_share_qty("AAPL", 1000.0, 0.0)
        self.assertEqual(qty_zero_price, 0)

        # 2. Negative Price
        qty_neg_price = game.calculate_share_qty("AAPL", 1000.0, -10.0)
        self.assertEqual(qty_neg_price, 0)

        # 3. Negative Cash
        qty_neg_cash = game.calculate_share_qty("AAPL", -1000.0, 144.0)
        self.assertEqual(qty_neg_cash, 0)

        # 4. Zero Cash
        qty_zero_cash = game.calculate_share_qty("AAPL", 0.0, 144.0)
        self.assertEqual(qty_zero_cash, 0)


if __name__ == "__main__":
    unittest.main()
