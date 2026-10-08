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


def price_and_settle(state, all_syms, symbols_to_check, today):
    """Fetch live prices, gate on data availability, and settle the book (B6).

    Extracted verbatim from the block in ``run_daily_ai_management`` that runs
    between the symbol-universe assembly (:func:`assemble_symbol_universe`) and
    the queued-order / decision loops. It establishes the day's single source of
    price truth and settles the portfolio against it — five steps, all unchanged
    from the root:

    1. **Live price fetch** — ``get_live_prices(all_syms)`` (E*TRADE primary,
       Google Finance fallback, via the B1 price source).
    2. **Price Source Gate** — if *no* prices came back at all, raise
       ``RuntimeError`` rather than proceed on stale workbook prices: with no live
       source of truth the run must crash, not trade blind.
    3. **Circuit breaker** — ``circuit_breaker.enforce_circuit_breaker`` (the
       systemic-crash guard; may de-risk into cash — mutates ``state`` in place).
    4. **Options settlement** — ``options.resolve_expiring_options`` settles any
       weekly covered call expiring today (R&D #26; mutates ``state`` in place).
    5. **Equity mark** — Zero-Trust: *surface* held positions with no live quote
       via ``_log.console`` but do NOT abort over them (aborting would skip
       stop-loss enforcement on every other position, violating the Rule of Loss
       Minimization); then mark ``state["equity"]`` to live prices, unpriced
       names falling back to cost inside ``_live_equity``.

    Returns the ``prices`` dict for the caller (and, later, the B7
    ``RunContext``) to thread into the queued-order execution and the decision
    loops. Every collaborator (``get_live_prices``, ``circuit_breaker``,
    ``options``, ``_live_equity``, the ``_log`` sink) is resolved off the live
    root module at call time via :func:`_pkg`, so ``mock.patch.object(game, ...)``
    still intercepts and the circuit-breaker / options mutations land on the same
    ``state`` object the caller holds.
    """
    game = _pkg()

    prices = game.get_live_prices(all_syms)

    # --- Price Source Gate ---
    # Primary source: E*TRADE live API. Automatic fallback: Google Finance scraper.
    # If both fail (prices is empty), crash — no stale workbook prices allowed.
    if not prices:
        raise RuntimeError("Critical Data Failure: Both E*TRADE and Google Finance fallback returned no prices. No live source of truth available!")

    # --- Systemic Crash Circuit Breaker Guard ---
    game.circuit_breaker.enforce_circuit_breaker(state, prices)

    # --- Options Settlement Pass (R&D #26) ---
    # Settle any active weekly Covered Calls expiring today!
    game.options.resolve_expiring_options(state, today, prices)

    # Zero-Trust: surface held positions with no live quote, but do NOT abort the
    # run over them — aborting would skip stop-loss enforcement on every *other*
    # position too, violating the Rule of Loss Minimization. Unpriced names fall
    # back to cost for the equity figure (via _live_equity) and are held (their
    # cost-based price won't trip a stop) until a quote returns.
    missing_prices = [sym for sym in symbols_to_check if sym not in prices or not prices[sym] or prices[sym] <= 0]
    if missing_prices:
        game._log.console(f"  [AETHER] PORTFOLIO ERROR: No live quote found for held positions {missing_prices}! Using cost basis for their equity share and skipping their stop check this run.")

    state["equity"] = round(game._live_equity(state["balance"], state["positions"], prices), 0)

    return prices


def execute_queued_orders(state, queued, prices, rules, ws, today, now_time, new_transactions):
    """Execute queued strategic-override orders, then clear the queue (B6).

    Extracted verbatim from the ``# 0. Execute QUEUED ORDERS`` block in
    ``run_daily_ai_management`` — the first stage after the price gate / book
    settlement (:func:`price_and_settle`) and before the SELL decision loop. A
    queued order is a strategic override placed on a prior run (or by the
    operator) that fills at today's live price. Everything is unchanged from the
    root:

    * **Queued SELL** — unwinds any option liability first
      (``options.unwind_option_liability_if_held``), pops the position, credits
      the proceeds, appends the transaction to both ``state["history"]`` and
      ``new_transactions``, and logs the closed-trade DNA. A strategic/stop exit
      is intentionally NOT freshness-gated — it must always be allowed to run.
    * **Queued BUY** — re-applies the execution-time **Zero-Trust freshness
      gate**: a buy queued overnight may now sit on a stale OHLCV cache, and the
      ATR stop below is derived from that cache, so heal once and *skip* (drop)
      the order rather than fill on untrustworthy data. Then it sizes against the
      profile's slot / allocation limits (``calculate_share_qty``), sets a
      volatility ATR stop (8% fallback), resolves the buy DNA off the Research
      sheet (``r_row[3]``=symbol, ``[6]``=pgr, ``[4]``=industry, ``[24]``=s10,
      ``[25]``=l60), opens the position, and records the transaction.
    * A price of ``<= 0`` skips that order; a SELL for a symbol not held and a
      BUY for a symbol already held are both no-ops (the ``in`` / ``not in``
      guards).

    Finally ``state["queued_orders"]`` is reset to ``[]`` — but only when there
    *were* orders (an empty queue returns early, leaving the key untouched,
    exactly as the root's ``if queued:`` skip did). ``queued`` is passed in (the
    list captured before the price fetch) so the caller — and, later, the B7
    ``RunContext`` — owns the source; ``new_transactions`` is appended in place.
    Every collaborator (``_log``, ``options``, ``log_closed_trade_dna``,
    ``_cache_stale`` / ``_heal_symbol_cache`` / ``_MAX_STALE_DAYS``,
    ``calculate_share_qty``, ``risk_utils.calculate_atr``) is resolved off the
    live root module at call time via :func:`_pkg`, so ``mock.patch.object(game,
    ...)`` still intercepts and the mutations land on the caller's ``state``.

    The one non-behavioural change from the root text is cosmetic: the root's
    ``if price <= 0: continue`` one-liner is split across two lines to satisfy the
    package's lint gate (the root file predates it). Behaviour is identical.
    """
    game = _pkg()

    # 0. Execute QUEUED ORDERS (Strategic Overrides with Volatility Sizing)
    if not queued:
        return

    game._log.info("🤖 AI EXECUTING QUEUED STRATEGIC ORDERS...")
    for order in queued:
        sym = order["symbol"]
        price = prices.get(sym, 0)
        if price <= 0:
            continue

        if order["type"] == "SELL" and sym in state["positions"]:
            pos = state["positions"][sym]
            game.options.unwind_option_liability_if_held(sym, pos, state, price, today)
            state["positions"].pop(sym)
            proceeds = pos["qty"] * price
            state["balance"] += proceeds
            tx = {
                "date": today, "time": now_time, "type": "SELL",
                "symbol": sym, "price": price, "qty": pos["qty"],
                "pnl": round((price - pos["cost"]) * pos["qty"], 2),
                "details": f"Queued Sell: {order['reason']}",
                "stop_loss": pos.get("stop_loss"),
            }
            state["history"].append(tx)
            new_transactions.append(tx)
            game._log.info(f"🤖 AI QUEUED SELL EXECUTED: {sym} at ${price} (PnL: ${tx['pnl']})")
            game.log_closed_trade_dna(sym, pos, price, today)

        elif order["type"] == "BUY" and sym not in state["positions"]:
            # Zero-Trust Freshness Gate (execution-time): a queued BUY may have sat overnight,
            # so the screen-time gate that cleared it can be stale by now. The ATR stop below is
            # derived from this cache, so re-verify freshness before filling. Heal once, then
            # SKIP (drop) the order rather than execute on untrustworthy data — the screener
            # re-surfaces the name next run if it still qualifies. (Queued SELLs are intentionally
            # NOT gated: a strategic/stop exit must always be allowed to run.)
            if game._cache_stale(sym, max_stale_days=game._MAX_STALE_DAYS):
                game._heal_symbol_cache(sym)
                if game._cache_stale(sym, max_stale_days=game._MAX_STALE_DAYS):
                    game._log.warning(f"🛑 QUEUED BUY SKIPPED (Zero-Trust Freshness): {sym} - OHLCV cache stale/missing at execution; refusing to derive an ATR stop from untrustworthy data.")
                    continue
            max_positions = rules["max_positions"]
            available_slots = max_positions - len(state["positions"])
            if state["balance"] > 500 and available_slots > 0:
                max_allocation = state["equity"] * rules["max_allocation_pct"]
                cash_to_use = min(state["balance"] / available_slots, max_allocation)

                qty = game.calculate_share_qty(sym, cash_to_use, price)
                if qty > 0:
                    cost = qty * price
                    state["balance"] -= cost

                    # Volatility-Based Stop Loss customized by profile
                    atr = game.risk_utils.calculate_atr(sym)
                    if atr and atr > 0:
                        stop_loss = round(price - (rules["atr_multiplier"] * atr), 2)
                        stop_desc = f"ATR-based Stop: ${stop_loss} ({rules['atr_multiplier']} * ATR)"
                    else:
                        stop_loss = round(price * 0.92, 2)
                        stop_desc = f"8% Fallback Stop: ${stop_loss}"

                    # Resolve buy DNA for queued buys
                    q_pgr = "Neutral"
                    q_s10 = 0.0
                    q_l60 = 0.0
                    q_score = 0.0
                    q_industry = "Unknown"
                    for r_row in ws.iter_rows(min_row=2, values_only=True):
                        if r_row[3] == sym:
                            q_pgr = r_row[6] or "Neutral"
                            q_industry = r_row[4] or "Unknown"
                            try:
                                q_s10 = float(r_row[24] or 0.0)
                                q_l60 = float(r_row[25] or 0.0)
                                q_score = round(q_s10 + q_l60, 1)
                            except (ValueError, TypeError):
                                pass
                            break

                    state["positions"][sym] = {
                        "qty": qty,
                        "cost": price,
                        "stop_loss": stop_loss,
                        "buy_dna": {
                            "buy_date": today,
                            "pgr": q_pgr,
                            "s10": q_s10,
                            "l60": q_l60,
                            "score": q_score,
                            "z_score": 0.0,
                            "industry": q_industry
                        }
                    }
                    tx = {
                        "date": today, "time": now_time, "type": "BUY",
                        "symbol": sym, "price": price, "qty": qty,
                        "details": f"Queued Buy: {order['reason']} ({stop_desc})"
                    }
                    state["history"].append(tx)
                    new_transactions.append(tx)
                    game._log.info(f"🤖 AI QUEUED BUY EXECUTED: {qty} shares of {sym} at ${price} ({stop_desc})")

    state["queued_orders"] = []


def decide_exits(state, prices, rules, ws, today, now_time, new_transactions):
    """Run the per-position SELL decision loop; return the symbols to liquidate (B6).

    Extracted verbatim from the ``# SELL logic — unified deterministic exit
    policy`` block in ``run_daily_ai_management`` — the stage after
    :func:`execute_queued_orders` and before the live SELL execution loop. For
    every held position (iterated over a snapshot of the keys), unchanged from the
    root:

    * **Research-sheet read** — ``row[24]``=s10, ``row[25]``=l60 and the
      **SELL-side** prev-close ``row[8]`` (falling back to cost). ``row[8]`` is
      deliberately kept distinct from the BUY-side ``row[10]`` (design constraint
      2) — do not unify.
    * **Profit-Lock ratchet** (only with a positive ATR) — tracks
      ``pos["highest_close_since_acq"]``; once in profit by > 1.0x ATR trails the
      stop up to ``peak - multiplier*ATR`` (1.5x for scarcity, else the profile's
      ``atr_multiplier``, default 2.5), never lowering it; past 1.5x ATR the stop
      is lifted to the cost basis (breakeven lock).
    * **Bank-As-You-Go scale-out** (Defensive Overlay Rule B, market hours only)
      — ``risk_utils.scale_out_plan`` sizes a partial sale off the original lot,
      always keeping >= 1 share, advancing ``pos["banked_pct"]``; the partial SELL
      is recorded in ``state["history"]`` and ``new_transactions`` here. A
      high-conviction flower (``l60 >= CFG.system_covered_call_l60_ceiling``) is
      held and the hold is logged.
    * **Gap-Down Guard** — ``circuit_breaker.is_single_stock_gap_frozen`` freezes
      the stop (passes ``stop_loss=None``) so an opening whipsaw can't trip it.
    * **Exit decision** — ``decision_eval.build_entry`` is the single source of the
      action (``_sma50`` is read only when ``sell_rules.soft_exit`` fires).
    * **AI second-opinion override** (R&D #14) — a ``SELL`` is downgraded to
      ``HOLD`` / ``WATCH`` when a real-time shadow verdict, a stored position
      verdict key (``shadow_verdict`` / ``ai_verdict`` / ``verdict``), or the
      stored ``verdicts`` dict says ``HOLD`` / ``FLAG-FOR-REVIEW``, checked in that
      order.
    * **Routing** — a surviving ``SELL`` is queued in ``state["queued_orders"]``
      (deduped) when outside market hours, else collected for liquidation; a
      ``REVIEW`` is logged as winner-protected. All entries are then written via
      ``decision_eval.log_decisions`` (skipped when there were no positions).

    Returns ``symbols_to_sell`` — an insertion-ordered ``{symbol: exit_reason}``
    dict (the ``rules_reason``, or ``"Technical exit"`` when empty) that the
    caller's execution loop liquidates and records on each SELL tx (#136); the
    same ``exit_reason`` feeds the after-hours queue entry. This function sells
    nothing outright except the partial
    scale-out. ``is_market_hours`` is still evaluated late and per position, as in
    the root (design risk 3 — not hoisted). Every collaborator (``_log``,
    ``risk_utils``, ``is_market_hours``, ``CFG``, ``circuit_breaker``, ``_sma50``,
    ``sell_rules``, ``decision_eval``) is resolved off the live root module at
    call time via :func:`_pkg`, so ``mock.patch.object(game, ...)`` still
    intercepts and every mutation lands on the caller's ``state``.
    """
    game = _pkg()

    # SELL logic — unified deterministic exit policy (sell_rules.exit_decision):
    # hard ATR stop > soft momentum signal (winner-protected) > hold.
    symbols_to_sell = {}  # sym -> exit reason, recorded on the SELL tx
    decision_entries = []
    for sym in list(state["positions"].keys()):
        pos = state["positions"][sym]
        s10 = l60 = 0
        prev_close = pos.get("cost", 0.0)
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3] == sym:
                s10 = row[24] or 0
                l60 = row[25] or 0
                try:
                    prev_close = float(row[8] or pos.get("cost", 0.0))
                except Exception:
                    prev_close = pos.get("cost", 0.0)
                break
        price = prices.get(sym, pos.get("cost"))

        # ── AETHER Profit-Lock Trailing Stop-Loss Ratchet (Priority 1) ──
        atr = game.risk_utils.calculate_atr(sym)
        if atr and atr > 0:
            # 1. Peak Price Tracking: track highest close since acquisition
            highest_close = pos.get("highest_close_since_acq", 0.0)
            highest_close = max(highest_close, pos.get("cost", 0.0), price)
            pos["highest_close_since_acq"] = highest_close

            # Determine trailing multiplier (1.5x for Scarcity, profile rules-based for Standard)
            is_scarcity = pos.get("is_scarcity", False)
            multiplier = 1.5 if is_scarcity else rules.get("atr_multiplier", 2.5)

            # 2. Peter Lynch Flower Protection: Only ratchet stop upward once safely in profit by > 1.0x ATR
            if (price - pos.get("cost", 0.0)) > (1.0 * atr):
                recalculated_stop = round(highest_close - (multiplier * atr), 2)
                old_stop = pos.get("stop_loss", 0.0)
                if recalculated_stop > old_stop:
                    pos["stop_loss"] = recalculated_stop
                    game._log.info(f"[Profit-Lock] {sym} stop ratcheted: ${old_stop:.2f} -> ${recalculated_stop:.2f} (peak close ${highest_close:.2f})")
                    game._log.info(f"🛡️ [Profit-Lock] {sym} stop ratcheted upwards: ${old_stop:.2f} ➡️ ${recalculated_stop:.2f}")

            # 3. Breakeven Trigger: If price has rallied > 1.5x ATR, lock in exact purchase Cost Basis (Breakeven)
            if (price - pos.get("cost", 0.0)) > (1.5 * atr):
                old_stop = pos.get("stop_loss", 0.0)
                cost_basis = pos.get("cost", 0.0)
                if cost_basis > old_stop:
                    pos["stop_loss"] = cost_basis
                    game._log.info(f"[Breakeven Lock] {sym} stop raised to cost basis: ${old_stop:.2f} -> ${cost_basis:.2f}")
                    game._log.info(f"🛡️ [Breakeven Lock] {sym} stop bumped to Cost Basis: ${old_stop:.2f} ➡️ ${cost_basis:.2f}")
        # ── Bank-As-You-Go scale-out (Defensive Overlay — Rule B) ──
        # Take PARTIAL profit as a winner runs through ATR tiers — distinct from
        # the full, pop-based liquidation loop below. This only ever removes risk
        # (never scales a loser, never adds exposure), reduces pos["qty"] rather
        # than popping the position, and persists pos["banked_pct"] (the cumulative
        # fraction sold) so each tier fires exactly once. Executes only in market
        # hours; if a tier is crossed after-hours it fires on the next live cycle.
        if atr and atr > 0 and price and price > 0 and pos.get("qty", 0) > 1 and game.is_market_hours():
            banked_pct = float(pos.get("banked_pct", 0.0) or 0.0)
            # Pass the conviction bar into the pure planner: a high-conviction
            # flower (L60 >= the covered-call ceiling) is never trimmed, mirroring
            # the covered-call flower exclusion so the two winner-side mechanics
            # share ONE conviction bar (plans/roadmap.md, Jul-25 dont-sell-winners). The planner
            # returns frac 0.0 + a "held: high-conviction flower" reason when it
            # suppresses a would-be bank; surface that so the hold is visible.
            so_frac, so_reason = game.risk_utils.scale_out_plan(
                price, pos.get("cost", 0.0), atr, banked_pct,
                l60=l60, l60_ceiling=game.CFG.system_covered_call_l60_ceiling)
            if so_frac <= 0 and so_reason.startswith("held: high-conviction"):
                game._log.info(f"🌺 [Scale-Out] {sym}: {so_reason}")
            if so_frac > 0 and banked_pct < 1.0:
                # banked_pct is a fraction of the ORIGINAL lot; recover the original
                # size (works for legacy positions with no banked_pct) to size the sale.
                original_qty = pos["qty"] / (1.0 - banked_pct)
                sell_qty = int(round(so_frac * original_qty))
                # Keep at least one share so the trailing-stop exit path owns the
                # final close (log_closed_trade_dna / option unwind live there).
                sell_qty = max(0, min(sell_qty, pos["qty"] - 1))
                if sell_qty >= 1:
                    proceeds = sell_qty * price
                    state["balance"] += proceeds
                    pos["qty"] -= sell_qty
                    pos["banked_pct"] = round(banked_pct + so_frac, 6)
                    tx = {"date": today, "time": now_time, "type": "SELL",
                          "symbol": sym, "price": price, "qty": sell_qty,
                          "pnl": round((price - pos.get("cost", 0.0)) * sell_qty, 2),
                          "details": f"Scale-out (Bank-As-You-Go): {so_reason}"}
                    state["history"].append(tx)
                    new_transactions.append(tx)
                    game._log.info(f"🏦 [Scale-Out] {sym}: banked {sell_qty} sh at "
                              f"${price:.2f} — {so_reason}; {pos['qty']} sh left trailing")
        # ────────────────────────────────────────────────────────────────

        # Check Idiosyncratic Single-Stock Gap-Down Guard (Whipsaw protection)
        is_gap_frozen = False
        if game.circuit_breaker.is_single_stock_gap_frozen(sym, price, prev_close):
            is_gap_frozen = True
            gap_pct = round(((price - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0.0
            game._log.info(f"❄️ [Gap Guard] {sym} gapped down {gap_pct}% overnight. Holding stop wide to prevent opening whipsaw.")

        # Only pay the OHLCV read when the soft signal actually fires (winner-
        # protection is the only consumer of sma50); HOLD positions skip the I/O.
        sma50 = game._sma50(sym) if game.sell_rules.soft_exit(s10, l60) else None
        # build_entry is the single source of the decision: it runs the exit
        # policy once, logs it, and (run_shadow=None) shadow-runs AI only for
        # non-HOLD candidates. The action it returns drives the actual sell.
        entry = game.decision_eval.build_entry(
            symbol=sym, price=price, cost=pos.get("cost"),
            stop_loss=None if is_gap_frozen else pos.get("stop_loss"),
            s10=s10, l60=l60, sma50=sma50,
            date=today, run_shadow=None)

        # AI Second-Opinion Exit Override Gate (R&D Item 14)
        if entry["rules_action"] == "SELL":
            ai_override = False
            override_reason = ""
            override_verdict = ""

            # 1. Check real-time shadow verdicts generated in entry
            realtime_verdicts = entry.get("verdicts", {})
            for prov, v_info in realtime_verdicts.items():
                v = v_info.get("verdict", "").upper() if isinstance(v_info, dict) else str(v_info).upper()
                if v in ("FLAG-FOR-REVIEW", "HOLD"):
                    ai_override = True
                    override_verdict = v
                    override_reason = f"Real-time AI Shadow Heuristic ({prov}) returned {v}" + (f": {v_info.get('note', '')}" if isinstance(v_info, dict) and v_info.get('note') else "")
                    break

            # 2. Check stored position shadow verdicts if any
            if not ai_override:
                for key in ["shadow_verdict", "ai_verdict", "verdict"]:
                    if key in pos:
                        val = pos[key]
                        if isinstance(val, dict):
                            v = val.get("verdict", "").upper()
                            note = val.get("note", "")
                        else:
                            v = str(val).upper()
                            note = ""
                        if v in ("FLAG-FOR-REVIEW", "HOLD"):
                            ai_override = True
                            override_verdict = v
                            override_reason = f"Stored position {key} returned {v}" + (f": {note}" if note else "")
                            break

            # 3. Check verdicts dictionary inside the stored position
            if not ai_override:
                pos_verdicts = pos.get("verdicts", {})
                if isinstance(pos_verdicts, dict):
                    for prov, v_info in pos_verdicts.items():
                        v = v_info.get("verdict", "").upper() if isinstance(v_info, dict) else str(v_info).upper()
                        if v in ("FLAG-FOR-REVIEW", "HOLD"):
                            ai_override = True
                            override_verdict = v
                            override_reason = f"Stored position verdicts ({prov}) returned {v}" + (f": {v_info.get('note', '')}" if isinstance(v_info, dict) and v_info.get('note') else "")
                            break

            if ai_override:
                old_action = entry["rules_action"]
                if override_verdict == "HOLD":
                    new_action = "HOLD"
                else:
                    new_action = "WATCH"

                entry["rules_action"] = new_action
                entry["rules_reason"] = f"AI Override: {override_reason} (was {entry['rules_reason']})"

                game._log.info(
                    f"🛡️ [AI EXIT OVERRIDE] {sym} sell overridden! "
                    f"Decision downgraded from {old_action} to {new_action}. Reason: {override_reason}"
                )
                game._log.info(f"🛡️ [AI Override] Overriding sell of {sym} -> Downgrading to {new_action} due to: {override_reason}")

        decision_entries.append(entry)
        if entry["rules_action"] == "SELL":
            exit_reason = entry["rules_reason"] or "Technical exit"
            if not game.is_market_hours():
                # Queue the sell instead of executing immediately
                if not any(q["symbol"] == sym and q["type"] == "SELL" for q in state.get("queued_orders", [])):
                    state.setdefault("queued_orders", []).append({
                        "type": "SELL", "symbol": sym, "reason": f"Exit triggered: {exit_reason}"
                    })
                    game._log.info(f"📝 [Queued] After-hours SELL queued for {sym}: {entry['rules_reason']}")
            else:
                symbols_to_sell[sym] = exit_reason
        elif entry["rules_action"] == "REVIEW":
            # Winner above its 50-DMA on a soft signal — hold, don't dump.
            game._log.info(f"🌸 AI HOLD (winner-protected): {sym} — {entry['rules_reason']}")
    if decision_entries:
        game.decision_eval.log_decisions(decision_entries)
    return symbols_to_sell


def execute_exits(state, symbols_to_sell, prices, today, now_time, new_transactions):
    """Liquidate the positions :func:`decide_exits` picked (B6).

    Extracted verbatim from the live SELL execution loop in
    ``run_daily_ai_management`` — the stage right after :func:`decide_exits` and
    before BUY screening. For each ``{symbol: exit_reason}`` entry, in order,
    unchanged from the root:

    * the price falls back to cost when the symbol is unquoted;
    * ``options.unwind_option_liability_if_held`` buys back a written covered
      call first, so no naked call is left behind;
    * the position is popped from ``state["positions"]``;
    * **STP LMT fill** (R&D #8) — if the price is at or below a positive
      ``stop_loss``, the fill is the stop price, not the gapped market price, and
      the tx details get ``[STP LMT fill]``;
    * the proceeds are credited to ``state["balance"]``; the SELL tx (with
      ``pnl``, ``Exit: <reason>`` and the position's ``stop_loss``, #136) is
      appended to ``state["history"]`` and ``new_transactions``;
    * ``log_closed_trade_dna`` records the closed trade.

    Returns nothing; every change lands on the caller's ``state`` and
    ``new_transactions``. ``_log``, ``options`` and ``log_closed_trade_dna`` are
    resolved off the live root module at call time via :func:`_pkg`, so
    ``mock.patch.object(game, ...)`` still intercepts.
    """
    game = _pkg()

    for sym, exit_reason in symbols_to_sell.items():
        pos = state["positions"][sym]
        price = prices.get(sym, pos["cost"])
        game.options.unwind_option_liability_if_held(sym, pos, state, price, today)
        state["positions"].pop(sym)

        # Slippage-Protected Limit Stop (STP LMT - R&D #8): Execute at exactly the stop price
        # if the market close price dropped below our stop-loss floor, preventing slippage leaks.
        stop_loss = pos.get("stop_loss", 0.0)
        stop_fill = stop_loss > 0.0 and price <= stop_loss
        if stop_fill:
            game._log.info(f"🛡️ [STP LMT] Executed {sym} stop-loss at Limit price ${stop_loss:.2f} (protected against market gap ${price:.2f}).")
            price = stop_loss

        proceeds = pos["qty"] * price
        state["balance"] += proceeds
        tx = {"date": today, "time": now_time, "type": "SELL", "symbol": sym, "price": price, "qty": pos["qty"], "pnl": round((price - pos["cost"]) * pos["qty"], 2),
              "details": f"Exit: {exit_reason}" + (" [STP LMT fill]" if stop_fill else ""),
              "stop_loss": pos.get("stop_loss")}
        state["history"].append(tx)
        new_transactions.append(tx)
        game._log.info(f"🤖 AI LIVE SELL: {sym} at ${price} (Time: {now_time})")
        game.log_closed_trade_dna(sym, pos, price, today)
