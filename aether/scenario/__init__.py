"""aether.scenario — composable scenario package (BUILD phase, strangler-fig).

Decomposes the 2178-line procedural ``ai_portfolio_game.py`` into entity views,
action ports, and composable scenario runners, mirroring the shipped
``aether/etrade/`` hexagonal ports-and-adapters schema (design doc
``plans/scenario-refactor.md``, R&D #44).

**Additive during BUILD:** nothing here is wired into ``ai_portfolio_game`` yet —
the root script is untouched until the REPLACE phase, so the full test suite
cannot regress while the package is assembled. B1 lands the price ports/adapters;
later steps add ``schema``, ``steps``, and ``runners``.

Re-exports the price surface (B1), the entity views (B2) and the stateless helper
surface (B3) so scenario code (and, from the REPLACE phase, the root script) can
compose a ``PriceSource``, wrap the raw ``state`` dicts, or call the game's pure
helpers without reaching into submodules.
"""
from aether.scenario.helpers import (
    adaptive_s10_floor,
    backtrack_verify,
    calculate_bubble_z_score,
    calculate_share_qty,
    calculate_ticker_trend_score,
    check_failure_rules,
    determine_max_positions,
    evaluate_momentum_rotation,
    get_market_regime,
    get_strategy_rules,
    is_bottom_confirmed,
    is_market_hours,
    should_pyramid_into_winner,
)
from aether.scenario.prices import (
    ChainedPriceSource,
    EtradeSource,
    GoogleSource,
    JsonCacheSource,
    PriceSource,
    WorkbookSource,
    make_price_source,
)
from aether.scenario.schema import (
    Order,
    Portfolio,
    Position,
    Quote,
)
from aether.scenario.steps import (
    assemble_symbol_universe,
    determine_profile,
    price_and_settle,
)

__all__ = [
    # prices (B1)
    "PriceSource",
    "JsonCacheSource",
    "GoogleSource",
    "EtradeSource",
    "WorkbookSource",
    "ChainedPriceSource",
    "make_price_source",
    # schema (B2)
    "Portfolio",
    "Position",
    "Order",
    "Quote",
    # helpers (B3)
    "is_market_hours",
    "get_market_regime",
    "get_strategy_rules",
    "calculate_ticker_trend_score",
    "calculate_bubble_z_score",
    "calculate_share_qty",
    "determine_max_positions",
    "adaptive_s10_floor",
    "should_pyramid_into_winner",
    "check_failure_rules",
    "is_bottom_confirmed",
    "backtrack_verify",
    "evaluate_momentum_rotation",
    # steps (B5, B6)
    "determine_profile",
    "assemble_symbol_universe",
    "price_and_settle",
]
