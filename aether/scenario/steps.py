"""Stateful scenario stages for the scenario package (hexagonal, BUILD phase B5).

WHAT THIS IS
------------
The fifth seam of the ``ai_portfolio_game`` -> ``aether/scenario`` refactor
(design doc ``plans/scenario-refactor.md``, R&D #44). B1 gave the *action* ports
(``prices.py``); B2 the *entity* views (``schema.py``); B3 the stateless helper
surface (``helpers.py``); B4 the ``ResearchRow`` lens (``schema.py``). This module
begins lifting the god-function's *stateful stages* out of
``run_daily_ai_management`` as plain functions that take the raw ``state`` dict,
mutate it in place, and return their result — the shape a ``RunContext`` step will
compose in B7.

FIRST STAGE: ``determine_profile``
----------------------------------
Extracted verbatim from the profile-determination block at the top of
``run_daily_ai_management`` (the four-branch Adaptive/Manual-override chain). Two
things it does, unchanged from the root:

* mutates ``state["profile"]`` and ``state["profile_mode"]`` in place, and
* returns the resolved ``profile`` string.

The one intentional cleanup is a **dedup**: the root had the "Adaptive
Cash-Deployment Upgrade Gate" copied byte-for-byte in two of the four branches
(the MANUAL-override auto-reset branch and the plain-autopilot ``else`` branch).
Here it lives once, in :func:`_apply_cash_deployment_gate`, called from both — the
same computation, so the behaviour is identical while the single source of truth
removes the copy-paste drift risk.

THE ``_pkg()`` SEAM (load-bearing)
----------------------------------
Like ``prices.py`` / ``helpers.py`` / ``etrade/store.py``, this resolves its
``ai_portfolio_game`` collaborators (``get_market_regime``,
``_has_strong_setups_today``, the ``_log`` sink) off the live module object **at
call time** via :func:`_pkg`, never a module-level ``from ai_portfolio_game
import ...``. That keeps the pinned ``mock.patch.object(game, ...)`` seams
intercepting after the REPLACE phase routes the root's block through this
function, and avoids a top-level import cycle once the root becomes a facade.
"""
from __future__ import annotations


def _pkg():
    """Return the ``ai_portfolio_game`` module, resolved lazily at call time.

    Imported inside the function (never at module import) so collaborators are
    looked up on the live module object each call — a ``mock.patch.object(game,
    ...)`` then keeps intercepting after callers move into this package, and there
    is no circular-import hazard with the root script. Mirrors
    ``aether/scenario/helpers.py::_pkg()`` and ``aether/etrade/store.py::_pkg()``.
    """
    import ai_portfolio_game as _p
    return _p


def _apply_cash_deployment_gate(state, profile):
    """The Adaptive Cash-Deployment Upgrade Gate (autopilot only).

    Verbatim from ``run_daily_ai_management`` — where it was duplicated in both
    the MANUAL-override auto-reset branch and the plain-autopilot ``else`` branch;
    hoisted here so the two branches share one definition. When cash is plentiful
    (> 40% of equity) and strong bottom setups exist, a ``DEFENSIVE`` regime is
    upgraded to ``BALANCED`` so idle cash can be deployed safely. Returns the
    (possibly upgraded) ``profile``; no state mutation.
    """
    game = _pkg()
    equity = state.get("equity", 0)
    cash_ratio = state.get("balance", 0) / equity if equity > 1.0 else 0
    if profile == "DEFENSIVE" and cash_ratio > 0.40 and game._has_strong_setups_today(min_score=9.5):
        game._log.console(f"  [AETHER] Cash is plentiful ({cash_ratio*100:.1f}%) and strong bottom setups are detected.")
        game._log.info("  -> Adaptively upgrading today's strategy profile from DEFENSIVE to BALANCED to deploy cash safely!")
        profile = "BALANCED"
    return profile


def determine_profile(state, manual_profile=None):
    """Resolve today's strategy profile (Adaptive vs. Manual Override).

    Extracted from the top of ``run_daily_ai_management``. Mutates
    ``state["profile"]`` and ``state["profile_mode"]`` in place and returns the
    resolved ``profile`` string. Behaviour is identical to the root block; the
    duplicated cash-deployment gate is the single :func:`_apply_cash_deployment_gate`.
    """
    game = _pkg()

    if manual_profile and manual_profile.upper() == "ADAPTIVE":
        profile = game.get_market_regime()
        state["profile"] = profile
        state["profile_mode"] = "ADAPTIVE"
        game._log.info(f"🤖 AI ACTIVE STRATEGY: {profile} (Adaptive pilot restored)")
    elif manual_profile:
        profile = manual_profile
        state["profile"] = profile
        state["profile_mode"] = "MANUAL"
        game._log.info(f"🤖 AI ACTIVE STRATEGY: {profile} (Manual Override - Locked)")
    elif state.get("profile_mode") == "MANUAL":
        # Auto-Reset Manual Override: A manual override is a one-time tactical choice.
        # On the next automated run (no CLI profile passed), we automatically reset back to ADAPTIVE autopilot.
        game._log.console("  [AETHER] Manual override expired. Automatically resetting back to Adaptive autopilot...")
        state["profile_mode"] = "ADAPTIVE"
        profile = game.get_market_regime()
        profile = _apply_cash_deployment_gate(state, profile)
        state["profile"] = profile
        game._log.info(f"🤖 AI ACTIVE STRATEGY: {profile} (Adaptive)")
    else:
        profile = game.get_market_regime()
        state["profile_mode"] = "ADAPTIVE"
        profile = _apply_cash_deployment_gate(state, profile)
        state["profile"] = profile
        game._log.info(f"🤖 AI ACTIVE STRATEGY: {profile} (Adaptive)")

    return profile


def assemble_symbol_universe(state, ws):
    """Build the day's symbol universe and pre-heal stale caches (B6).

    Extracted verbatim from the block in ``run_daily_ai_management`` that runs
    between the profile decision and the live price fetch. It does four things,
    all unchanged from the root:

    1. **Positions list** — ``symbols_to_check`` = the held-position keys.
    2. **Pre-flight OHLCV heal** — batch-heal any position whose cache is stale
       (via ``_cache_stale`` / ``_heal_symbol_cache``) *before* the decision loop,
       so ``_sma50`` never blocks on a live network call mid-iteration. The
       per-process ``_HEAL_ATTEMPTED`` guard lives on the root module and is
       preserved automatically: ``_heal_symbol_cache`` is resolved off the live
       module via :func:`_pkg`, so it mutates the same global set.
    3. **Legacy-position scarcity heal** — scans the Research sheet and stamps
       ``is_scarcity`` onto any held position that predates the scarcity core
       (mutates ``state["positions"][sym]`` in place).
    4. **Universe assembly** — the deduped ``all_syms`` set the price source is
       asked to quote: held + active-setup + queued + SPY (SPY is always included
       for the circuit breaker).

    Returns the four assembled lists as a dict
    ``{symbols_to_check, research_symbols, queued_syms, all_syms}`` so the caller
    (and, later, the B7 ``RunContext``) can thread them into the price gate and
    the decision loop. The live price fetch and the empty-prices gate stay with
    the caller — this stage only decides *which* symbols to price.
    """
    game = _pkg()

    symbols_to_check = list(state["positions"].keys())

    # Pre-flight: batch-heal stale OHLCV caches before the decision loop so
    # _sma50 never blocks on a live network call mid-iteration.
    stale_syms = [s for s in symbols_to_check if game._cache_stale(s, max_stale_days=game._MAX_STALE_DAYS)]
    if stale_syms:
        game._log.info(f"[Pre-flight] Healing {len(stale_syms)} stale OHLCV cache(s) before evaluation: {stale_syms}")
        for _s in stale_syms:
            game._heal_symbol_cache(_s)

    # Dynamically heal/classify legacy positions
    for row in ws.iter_rows(min_row=2, values_only=True):
        sym = row[3]
        if sym in state["positions"] and "is_scarcity" not in state["positions"][sym]:
            industry_str = row[4] or ""
            is_scarcity = game.instruments.is_scarcity_asset(sym, industry_str)
            state["positions"][sym]["is_scarcity"] = is_scarcity
            game._log.info(f"  [AETHER State Healer] Classified existing position {sym} as scarcity={is_scarcity}")

    research_symbols = game._active_setup_symbols(ws)

    queued = state.get("queued_orders", [])
    queued_syms = [q["symbol"] for q in queued]

    # Ensure we always fetch the live price of SPY for our Circuit Breaker
    all_syms = list(set(symbols_to_check + research_symbols + queued_syms + ["SPY"]))

    return {
        "symbols_to_check": symbols_to_check,
        "research_symbols": research_symbols,
        "queued_syms": queued_syms,
        "all_syms": all_syms,
    }
