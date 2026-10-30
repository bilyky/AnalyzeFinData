"""Scenario runners: the stages in :mod:`aether.scenario.steps`, composed (B7).

``run_daily_management`` is ``ai_portfolio_game.run_daily_ai_management``
rebuilt from the B6 stages, in the root's order, with the root's own setup and
error handling around them. It is not wired in: the game still runs its own
function. ``tests/test_scenario_runner.py`` runs both under the same patches and
requires the same saved state, transactions, logs and exceptions.

Collaborators are read off the live root module at call time (``steps._pkg()``),
so ``mock.patch.object(game, ...)`` reaches the runner and every stage.
"""
from __future__ import annotations

from aether.scenario import steps


def run_daily_management(force=False, manual_profile=None):
    """One daily management run, stage by stage. Same contract as the root.

    * Market closed and not ``force``: logs and returns before loading state.
    * Workbook missing or stale (:func:`steps.open_research_workbook` returns
      ``None``): returns; state is still saved.
    * No live prices: the ``RuntimeError`` from :func:`steps.price_and_settle`
      propagates, after state is saved.
    * Any other exception: logged, not raised; state is still saved.

    :func:`steps.finalize_run` runs on every path, like the root's ``finally:``.
    Returns ``None``.
    """
    game = steps._pkg()
    state = None
    wb = None
    new_transactions = []
    try:
        open_status, msg = game.is_market_open()
        if not open_status and not force:
            game._log.info(f"Aborting AI Move: {msg}")
            return

        state = game.load_game()
        today = str(game.datetime.date.today())
        now_time = game.datetime.datetime.now().strftime("%H:%M:%S")

        profile = steps.determine_profile(state, manual_profile)
        rules = game.get_strategy_rules(profile)

        opened = steps.open_research_workbook(today)
        if opened is None:
            return
        wb, ws = opened

        universe = steps.assemble_symbol_universe(state, ws)
        # The root reads the queue here, between assembly and the price fetch.
        queued = state.get("queued_orders", [])
        prices = steps.price_and_settle(state, universe["all_syms"], universe["symbols_to_check"], today)

        steps.execute_queued_orders(state, queued, prices, rules, ws, today, now_time, new_transactions)
        symbols_to_sell = steps.decide_exits(state, prices, rules, ws, today, now_time, new_transactions)
        steps.execute_exits(state, symbols_to_sell, prices, today, now_time, new_transactions)

        buys = steps.screen_buys(state, prices, rules, ws, profile, today)
        available_slots = steps.rotate_positions(
            state, prices, profile, buys["available_slots"], buys["max_positions"], buys["top_buys"],
            buys["active_position_scores"], today, now_time, new_transactions)
        steps.deploy_capital(state, prices, rules, ws, buys["top_buys"], available_slots,
                             buys["min_cash_required"], today, now_time, new_transactions)
    except RuntimeError:
        raise  # critical failures (no prices, etc.) must propagate — never swallow
    except Exception as e:
        game._log.exception(f"run_daily_ai_management failed: {e}")
    finally:
        steps.finalize_run(state, wb, new_transactions)
