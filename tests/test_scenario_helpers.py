"""B3 tests for aether.scenario.helpers — the stateless helper surface.

Two things are pinned:

1. **Delegation is call-time and patch-preserving.** Every wrapper forwards to the
   live ``ai_portfolio_game`` function resolved *at call time* (the ``_pkg()`` seam),
   so a ``mock.patch.object(game, name, ...)`` still intercepts through the package
   wrapper — and the wrapper forwards its arguments verbatim. This is the invariant
   that lets the REPLACE phase route callers through ``aether.scenario`` without
   breaking the pinned patches.

2. **The seam is wired to the real function.** For the hermetic (arg-only) helpers,
   an unpatched differential check asserts ``helpers.X(args) == game.X(args)`` — so a
   typo'd delegate name or wrong default would be caught, not silently pass.

Delegation inherently needs the root module, so — unlike the pure-lens schema tests
— this imports ``ai_portfolio_game`` (the same differential style as
``test_scenario_prices``). No network, no disk fixtures: the differential set is
limited to helpers that read only their arguments (+ ``CFG`` / ``instruments``).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import helpers  # noqa: E402

# (wrapper name, call args, args expected at the forwarded call).
# For all but check_failure_rules the two arg tuples are identical; the wrapper
# supplies check_failure_rules' s10 default (0.0) positionally, so the forwarded
# tuple is longer than the call tuple — pinning that the default is preserved.
_CASES = [
    ("is_market_hours", (), ()),
    ("get_market_regime", (), ()),
    ("get_strategy_rules", ("BALANCED",), ("BALANCED",)),
    ("calculate_ticker_trend_score", ("SPY",), ("SPY",)),
    ("calculate_bubble_z_score", ("SPY",), ("SPY",)),
    ("calculate_share_qty", ("AAPL", 1000.0, 100.0), ("AAPL", 1000.0, 100.0)),
    ("determine_max_positions", (0.20, 5, 5), (0.20, 5, 5)),
    ("adaptive_s10_floor", (0.30,), (0.30,)),
    ("should_pyramid_into_winner", (True, True, 5.0, 5.0), (True, True, 5.0, 5.0)),
    ("check_failure_rules", ("X", "Bull", 6.0, 1.0, "Tech"), ("X", "Bull", 6.0, 1.0, "Tech", 0.0)),
    ("is_bottom_confirmed", ("NVDA",), ("NVDA",)),
    ("backtrack_verify", ("NVDA",), ("NVDA",)),
    ("evaluate_momentum_rotation",
     ("BALANCED", False, 2, 5, {}, {}, [], {}),
     ("BALANCED", False, 2, 5, {}, {}, [], {})),
]


class TestDelegationForwards(unittest.TestCase):
    """Each wrapper resolves the game function at call time (so patches win) and
    forwards its arguments verbatim, returning whatever the game function returns."""

    def test_every_wrapper_forwards_and_is_patchable(self):
        for name, call_args, fwd_args in _CASES:
            with self.subTest(helper=name):
                sentinel = object()
                with mock.patch.object(game, name, return_value=sentinel) as m:
                    result = getattr(helpers, name)(*call_args)
                self.assertIs(result, sentinel, f"{name} did not return the game function's result")
                m.assert_called_once_with(*fwd_args)

    def test_exposed_surface_matches_cases(self):
        # Guard against a wrapper being added/renamed without a delegation case.
        from aether import scenario
        b3 = {
            "is_market_hours", "get_market_regime", "get_strategy_rules",
            "calculate_ticker_trend_score", "calculate_bubble_z_score",
            "calculate_share_qty", "determine_max_positions", "adaptive_s10_floor",
            "should_pyramid_into_winner", "check_failure_rules", "is_bottom_confirmed",
            "backtrack_verify", "evaluate_momentum_rotation",
        }
        self.assertEqual({c[0] for c in _CASES}, b3)
        # every B3 name is re-exported from the package root
        for name in b3:
            self.assertTrue(hasattr(scenario, name), f"{name} not re-exported from aether.scenario")


class TestRealPassThrough(unittest.TestCase):
    """Unpatched, the hermetic (arg-only) wrappers return exactly what the root
    function returns — proving the delegate is wired to the real implementation."""

    def test_determine_max_positions(self):
        self.assertEqual(helpers.determine_max_positions(0.20, 5, 5),
                         game.determine_max_positions(0.20, 5, 5))

    def test_adaptive_s10_floor(self):
        self.assertEqual(helpers.adaptive_s10_floor(0.30), game.adaptive_s10_floor(0.30))
        self.assertEqual(helpers.adaptive_s10_floor(0.0), game.adaptive_s10_floor(0.0))

    def test_should_pyramid_into_winner(self):
        for args in [(True, True, 5.0, 5.0), (False, True, 9.0, 9.0), (True, False, 1.0, 1.0)]:
            self.assertEqual(helpers.should_pyramid_into_winner(*args),
                             game.should_pyramid_into_winner(*args))

    def test_calculate_share_qty(self):
        self.assertEqual(helpers.calculate_share_qty("AAPL", 1000.0, 100.0),
                         game.calculate_share_qty("AAPL", 1000.0, 100.0))
        self.assertEqual(helpers.calculate_share_qty("AAPL", 0.0, 100.0),
                         game.calculate_share_qty("AAPL", 0.0, 100.0))

    def test_evaluate_momentum_rotation_balanced_noop(self):
        # BALANCED profile never rotates -> ([], slots unchanged, 0.0 cash).
        self.assertEqual(
            helpers.evaluate_momentum_rotation("BALANCED", True, 2, 5, {}, {}, [], {}),
            game.evaluate_momentum_rotation("BALANCED", True, 2, 5, {}, {}, [], {}),
        )


if __name__ == "__main__":
    unittest.main()
