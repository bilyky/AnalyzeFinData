"""Pure-helper surface for the scenario package (hexagonal, BUILD phase B3).

WHAT THIS IS
------------
The third seam of the ``ai_portfolio_game`` -> ``aether/scenario`` refactor
(design doc ``plans/scenario-refactor.md``, R&D #44). B1 gave the *action* ports
(``prices.py``); B2 gave the *entity* views (``schema.py``); this gives the
package a self-contained handle on the game's stateless helper functions, so a
scenario runner (B7) can compose them from ``aether.scenario`` without reaching
into the root module — and, from the REPLACE phase, so ``ai_portfolio_game`` can
delegate these names *into* the package.

THIN DELEGATION, NOT REIMPLEMENTATION
-------------------------------------
Each wrapper is a one-line forward to the live ``ai_portfolio_game`` function,
resolved at call time via :func:`_pkg`. Nothing is re-implemented here: the root
function stays the single definition, so B3 is behaviour-identical *by
construction* (the wrapper literally calls the same object). The package is
"self-contained" in the sense of exposing the names, not of owning the logic —
that inversion happens later, one seam per PR, in the REPLACE phase.

THE ``_pkg()`` SEAM (load-bearing)
----------------------------------
The forward is deliberately a call-time attribute lookup on the module object,
never a module-level ``from ai_portfolio_game import ...``. Two reasons, exactly
as in ``prices.py`` / ``etrade/store.py``:

* **Patch-preservation** — the pinned suite patches these names on the module
  (e.g. ``mock.patch.object(game, "is_market_hours", ...)``,
  ``mock.patch.object(game, "calculate_bubble_z_score", ...)``,
  ``mock.patch.object(game, "get_strategy_rules", ...)``). A name bound at import
  would freeze the pre-patch function; ``_pkg().is_market_hours()`` re-resolves
  each call and so keeps seeing the patch after the REPLACE phase routes callers
  through this module.
* **No circular import** — the lazy ``import ai_portfolio_game`` inside the
  function avoids a top-level cycle once the root becomes a thin facade over the
  package.

Signatures are stated explicitly (rather than ``*args, **kwargs``) so this module
documents the helper surface; they mirror the root definitions verbatim,
including ``check_failure_rules``' ``s10`` default.
"""
from __future__ import annotations


def _pkg():
    """Return the ``ai_portfolio_game`` module, resolved lazily at call time.

    Imported inside the function (never at module import) so every helper is
    looked up on the live module object each call — a ``mock.patch.object(game,
    ...)`` then keeps intercepting after callers move into this package, and there
    is no circular-import hazard with the root script. Mirrors
    ``aether/scenario/prices.py::_pkg()`` and ``aether/etrade/store.py::_pkg()``.
    """
    import ai_portfolio_game as _p
    return _p


# ===========================================================================
# Market regime / strategy profile
# ===========================================================================

def is_market_hours() -> bool:
    """True during active US equity hours (6:30 AM–1:15 PM PST, weekdays, non-holiday)."""
    return _pkg().is_market_hours()


def get_market_regime() -> str:
    """The SPY-momentum regime label (``AGGRESSIVE`` / ``BALANCED`` / ``DEFENSIVE``),
    after the SPY-RSP breadth-divergence downgrade pass."""
    return _pkg().get_market_regime()


def get_strategy_rules(profile: str) -> dict:
    """The risk/size rule dict for ``profile`` (max positions, allocation caps, ATR
    multiplier, score threshold, cash buffer) — reads the live regime internally."""
    return _pkg().get_strategy_rules(profile)


# ===========================================================================
# Per-symbol trend / bubble scoring (read the local OHLCV cache)
# ===========================================================================

def calculate_ticker_trend_score(symbol: str):
    """Standardized SMA-stack trend score in [-10, +10], or ``None`` when the OHLCV
    cache has < 200 days (a distinct "no data" signal, not a flat 0.0)."""
    return _pkg().calculate_ticker_trend_score(symbol)


def calculate_bubble_z_score(symbol: str):
    """Z-score of the current price vs its 500-day mean, or ``None`` with < 500 days
    of history (``0.0`` when the 500-day standard deviation is non-positive)."""
    return _pkg().calculate_bubble_z_score(symbol)


# ===========================================================================
# Position sizing / slot allocation (stateless, arg-only)
# ===========================================================================

def calculate_share_qty(symbol: str, cash_to_use: float, price: float) -> float:
    """Share quantity for ``cash_to_use`` at ``price`` — fractional (3dp) for
    fractional-eligible symbols, whole shares otherwise; ``0`` on non-positive input."""
    return _pkg().calculate_share_qty(symbol, cash_to_use, price)


def determine_max_positions(cash_ratio: float, num_positions: int, base_max_positions: int) -> int:
    """Base max-positions after Dynamic Position-Slot Expansion (grow the cap while
    idle cash > 15% and slots are full)."""
    return _pkg().determine_max_positions(cash_ratio, num_positions, base_max_positions)


def adaptive_s10_floor(cash_pct: float) -> float:
    """The Short10 momentum floor for new buys — relaxed when idle-cash drag exceeds
    ``CFG.system_cash_drag_threshold``, else the stricter default (R&D #15)."""
    return _pkg().adaptive_s10_floor(cash_pct)


def should_pyramid_into_winner(is_winner: bool, has_peak: bool, s10: float, l60: float) -> bool:
    """Pyramiding gate (R&D #31): add to a profitable, risk-locked winner near its
    peak when short-term OR long-term momentum still supports it."""
    return _pkg().should_pyramid_into_winner(is_winner, has_peak, s10, l60)


# ===========================================================================
# Candidate screening / empirical verification
# ===========================================================================

def check_failure_rules(symbol, pgr, score, z_score, industry, s10=0.0) -> tuple[bool, str]:
    """``(is_toxic, reason)`` from the Failure-DNA rules + earnings-shock veto, with
    the R&D #13 PGR waivers (elite-breakout / bottom-confirmed) applied."""
    return _pkg().check_failure_rules(symbol, pgr, score, z_score, industry, s10)


def is_bottom_confirmed(symbol):
    """``(bool, reason)`` — whether the last 3 daily slopes trace a bottoming
    signature (turning positive, improving, +0.5% average)."""
    return _pkg().is_bottom_confirmed(symbol)


def backtrack_verify(symbol):
    """``(bool, reason)`` — whether the last 3 daily closes are stable (no vertical
    day-over-day collapse steeper than -2%)."""
    return _pkg().backtrack_verify(symbol)


# ===========================================================================
# Momentum rotation (stateless plan over passed state)
# ===========================================================================

def evaluate_momentum_rotation(
    profile: str,
    is_market_open_flag: bool,
    available_slots: int,
    max_positions: int,
    positions: dict,
    prices: dict,
    top_buys: list,
    active_position_scores: dict,
) -> tuple[list[str], int, float]:
    """Plan AGGRESSIVE-profile momentum rotations over the passed state, returning
    ``(symbols_to_sell, updated_available_slots, cash_proceeds_to_add)``. Pure —
    it reads the given dicts/lists and mutates nothing (R&D #27)."""
    return _pkg().evaluate_momentum_rotation(
        profile, is_market_open_flag, available_slots, max_positions,
        positions, prices, top_buys, active_position_scores,
    )
