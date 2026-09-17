"""aether.scenario — composable scenario package (BUILD phase, strangler-fig).

Decomposes the 2178-line procedural ``ai_portfolio_game.py`` into entity views,
action ports, and composable scenario runners, mirroring the shipped
``aether/etrade/`` hexagonal ports-and-adapters schema (design doc
``plans/scenario-refactor.md``, R&D #44).

**Additive during BUILD:** nothing here is wired into ``ai_portfolio_game`` yet —
the root script is untouched until the REPLACE phase, so the full test suite
cannot regress while the package is assembled. B1 lands the price ports/adapters;
later steps add ``schema``, ``steps``, and ``runners``.

Re-exports the price surface so scenario code (and, from R1, the root script's
``get_live_prices``) can compose a ``PriceSource`` without reaching into submodules.
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

__all__ = [
    "PriceSource",
    "JsonCacheSource",
    "GoogleSource",
    "EtradeSource",
    "WorkbookSource",
    "ChainedPriceSource",
    "make_price_source",
]
