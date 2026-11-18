"""aether.scenario: the daily run as stages (R&D #44), being replaced by R&D #78.

``steps`` holds the stages of the daily run and ``runners.run_daily_management`` runs them
in order; ``ai_portfolio_game.run_daily_ai_management`` calls it. The stages are the only
copy of that logic. The unused price ports, entity views and helper wrappers (B1-B4) were
deleted in R&D #78 step S2; the plan (plans/aether-services.md) replaces this package with
the services under aether/services/.
"""
from aether.scenario.steps import (
    apply_preflight_gate,
    assemble_symbol_universe,
    decide_exits,
    deploy_capital,
    determine_profile,
    execute_exits,
    execute_queued_orders,
    finalize_run,
    open_research_workbook,
    price_and_settle,
    rotate_positions,
    screen_buys,
)
from aether.scenario.runners import run_daily_management

__all__ = [
    # steps (B5, B6)
    "determine_profile",
    "assemble_symbol_universe",
    "price_and_settle",
    "apply_preflight_gate",
    "execute_queued_orders",
    "decide_exits",
    "execute_exits",
    "screen_buys",
    "rotate_positions",
    "deploy_capital",
    "open_research_workbook",
    "finalize_run",
    # runners (B7)
    "run_daily_management",
]
