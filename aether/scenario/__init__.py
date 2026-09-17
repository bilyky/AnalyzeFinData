"""aether.scenario — composable scenario package (BUILD phase, strangler-fig).

Decomposes the 2178-line procedural ``ai_portfolio_game.py`` into entity views,
action ports, and composable scenario runners, mirroring the shipped
``aether/etrade/`` hexagonal ports-and-adapters schema (design doc
``plans/scenario-refactor.md``, R&D #44).

**Additive during BUILD:** nothing here is wired into ``ai_portfolio_game`` yet —
the root script is untouched until the REPLACE phase, so the full test suite
cannot regress while the package is assembled. B1 lands the price ports/adapters;
later steps add ``schema``, ``steps``, and ``runners``.

Re-exports the price surface (B1) and the entity views (B2) so scenario code (and,
from the REPLACE phase, the root script) can compose a ``PriceSource`` or wrap the
raw ``state`` dicts without reaching into submodules.
"""
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
]
