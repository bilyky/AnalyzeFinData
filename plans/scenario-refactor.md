# Plan: Decompose `ai_portfolio_game.py` into an `aether/scenario/` package (entities + action ports + composable scenarios)

> Reference plan saved 2026-09-16. Status: **approved, not yet implemented** (docs land first; code follows in small verifiable PRs).
> Origin: request to refactor `ai_portfolio_game.py` ("that is total mess") into **objects for entities** and **interfaces for actions**, with the script becoming a **starting place for different scenarios** — "even maybe split it on smaller scenario-specific entry points." Refined through dialogue: mirror the existing `aether/etrade/` schema ("we have etrade schema to follow already"), **build-then-replace** ("build object and functionality and then replace it inside ai_portfolio_game script"), **prepare everything first, then refactor in small verifiable steps**, and keep it **generic** — use `scenario`, not "game."

## Context

**Why:** `ai_portfolio_game.py` is a 2178-line procedural module whose core, `run_daily_ai_management(force, manual_profile)` (≈ lines 1394-2079), is a single ~685-line god-function running **15 sequential stages** inside one `try/except/finally`. There are **no entity objects** (portfolio, positions, orders, quotes are raw JSON dicts threaded by hand), **no action interfaces** (price fetch, sell decision, buy screening are all inline), and the profile cash-deployment gate is **duplicated verbatim** (lines 1425-1433 and 1439-1447). Adding a new scenario today — an OceanView advisory run, a backtest/replay, a dry-run — means **forking the god-function**.

**Goal:** (1) **entity objects** for the portfolio state, (2) **action ports** (interfaces) for the side-effecting steps, and (3) **composable scenario entry points** so a new scenario is a short composition, not a fork.

**Method — strangler-fig / build-then-replace (the repo's own E*TRADE playbook):** build the whole `aether/scenario/` package **additively and fully unit-tested first** — nothing in the root script changes, so the ~824-test suite cannot regress — **then** route one call-site at a time through it, **one PR per seam**. This is exactly how `aether/etrade/` was done (build the store fully, leave it unwired, wire one port per PR).

## Hard constraints from exploration (drive the design)

1. **Entities must be VIEWS over the raw dicts, not replacements.** `circuit_breaker.enforce_circuit_breaker`, `options.resolve_expiring_options`, and `options.execute_weekly_covered_call_pass` mutate the raw `state` dict **by reference**. So `Portfolio`/`Position` wrap the raw dict as their backing store; `from_dict` **never copies** and `to_dict()` returns the **same object** (identity). Precedent: `options_adviser.normalize_chain` builds `OptionQuote` from a raw dict via `_one_option` (dict → dataclass view), whereas `Position` there is an *input* entity constructed by the caller — we follow the `OptionQuote` (wrap-a-dict) precedent, not a deep-copy.

2. **Call-time collaborator lookup (the `_pkg()` shim) is load-bearing.** Extracted adapters/steps must resolve patched collaborators (`is_market_hours`, `get_google_prices_fallback`, `get_json_prices_fallback`, `etrade.fetch_quotes`, …) off the `ai_portfolio_game` module object **at call time** (lazy `import ai_portfolio_game` inside the function), exactly like `aether/etrade/store.py::_pkg()`. `tests/test_game_pricing.py` does `mock.patch.object(game, "is_market_hours", …)`, `mock.patch.object(game.etrade, "fetch_quotes", …)`, `mock.patch.object(game, "get_google_prices_fallback", …)` — those patches must keep intercepting after the logic physically moves into the package.

3. **Root file stays at repo root and stays compilable.** `tests/test_executables.py::test_compilation` asserts `ai_portfolio_game.py` exists and `py_compile`s. The end state is a **thin facade** re-exporting the test-pinned surface, not a deleted file.

4. **Preserve the Research-sheet magic indices per-site — do NOT unify.** `row[3]`=symbol, `[4]`=industry, `[6]`=pgr, `[9]`=stop, `[11]`=target, `[20]`=setup, `[24]`=s10, `[25]`=l60, and prev-close is read at **`[8]` in the SELL loop (line 1624)** but **`[10]` in BUY screening / `show_report` / after-hours (lines 1859, 1181, 2108)**. The `[8]`/`[10]` split is a verified intentional inconsistency; the reader exposes both and each caller keeps its existing choice.

## Package layout — `aether/scenario/` (mirrors `aether/etrade/`)

```
aether/scenario/
  __init__.py     # composition root + BACK-COMPAT re-exports of the test-pinned surface
  schema.py       # entity VIEWS over raw dicts: Portfolio / Position / Order / Quote (from_dict=wrap, to_dict=identity)
  prices.py       # PriceSource(abc.ABC) port + Etrade/Google/JsonCache/Workbook adapters + ChainedPriceSource + make_price_source()  [uses _pkg()]
  steps.py        # RunContext dataclass + the 15 stages as step(ctx) -> None functions
  runners.py      # scenario compositions: DailyManagement / Report / Summary / Backtest
```
Named `runners.py` (not `scenarios.py`) to avoid an `aether.scenario.scenarios` stutter. `prices.py` is the E*TRADE `store.py` analogue (ports + file adapters + factory); `runners.py` is the `client.py` facade analogue (composes the steps).

## BUILD order (additive — root script untouched; suite stays green throughout)

- **B1 `prices.py`** — first; least-abstracted seam, already has `tests/test_game_pricing.py` as its characterization anchor. `PriceSource` ABC + the four adapters + `ChainedPriceSource` reproducing the exact `get_live_prices` fallback chain (weekend gate → JSON cache → after-hours+fresh-workbook → cache → E*TRADE `get_tokens`+`fetch_quotes` → Google on failure/gap), all via `_pkg()`. `make_price_source()` factory.
- **B2 `schema.py`** — `Position`/`Order`/`Portfolio`/`Quote` views. `from_dict`=wrap (no copy), `to_dict`=identity. Encode the documented key shapes (pos: `qty`, `cost`, `stop_loss`, `is_scarcity`, `buy_dna`, `highest_close_since_acq`, `written_call`, read-only verdict fields; state: `balance`, `equity`, `positions`, `history`, `start_date`, `profile`, `profile_mode`, `queued_orders`).
- **B3 pure-helper wrappers** — thin delegation so the package is self-contained (sign/behavior unchanged): `is_market_hours`, `get_market_regime`, `get_strategy_rules`, `calculate_ticker_trend_score`, `calculate_bubble_z_score`, `calculate_share_qty`, `check_failure_rules`, `is_bottom_confirmed`, `backtrack_verify`, `evaluate_momentum_rotation`, `adaptive_s10_floor`, `should_pyramid_into_winner`, `determine_max_positions`.
- **B4 `ResearchRow`** — reader for the openpyxl Research-sheet row-scan, exposing both prev-close indices per constraint 4.
- **B5 `determine_profile`** — the profile-determination stage with the **duplicated cash-deployment gate deduped once** (behavior identical).
- **B6 stateful stages** as `step(ctx)` functions: pre-heal + workbook/symbol assembly, price gate (raises `RuntimeError` on empty), circuit breaker, options settlement, queued-order exec, SELL decision loop (profit-lock ratchet, gap guard, AI override), SELL exec (STP-LMT slippage), BUY screening, momentum rotation, BUY exec/after-hours queue, pyramiding, covered-call write, finally-persistence. Globals `_HEAL_ATTEMPTED` and `_SYMBOL_DAY_CACHE` stay the **same object** (referenced via `_pkg()`), never re-created in the package.
- **B7 `RunContext` + `runners.py`** — `RunContext` carries state/workbook/prices/config/log-sink; `DailyManagement` composes B5+B6 in the current stage order; `Report`/`Summary`/`Backtest` are thin compositions.

Each build step ships its own `tests/test_scenario_*.py` (hand-written fakes injected via constructor args, mirroring the E*TRADE per-door test style) and keeps `python -m unittest discover -s tests` fully green.

## REPLACE order (one seam per PR — characterization harness built before R3)

`tests/test_scenario_characterization.py` — snapshot `run_daily_ai_management` on a frozen fixture (final `state` dict, ordered `new_transactions`, ordered `_log` calls); behavior-identical = snapshot unchanged. Run every replace PR.

R1 `get_live_prices` → `make_price_source` (guarded by `test_game_pricing.py`) · R2 `load_game`/`save_game` → `FilePortfolioStore` (Mandatory Backup Policy: timestamped `Data/Backup/` before any state write) · R3 profile block → `steps.determine_profile` · R4 pre-heal + assembly · R5 price gate + circuit breaker + options settlement · R6 queued-order exec · R7 SELL decision loop · R8 SELL exec · R9 BUY screening · R10 momentum rotation · R11 BUY exec / pyramid / covered-call · R12 collapse god-function body to `runners.DailyManagement(...)` · R13 convert root `ai_portfolio_game.py` to a thin facade.

## Invariants that must NOT break

- **Test-pinned module surface** stays as `ai_portfolio_game` attributes (re-exported from the package): public `run_daily_ai_management`, `get_live_prices`, `get_google_prices_fallback`, `load_game`, `save_game`, `is_market_hours`, `get_market_regime`, `get_strategy_rules`, `calculate_ticker_trend_score`, `calculate_bubble_z_score`, `calculate_share_qty`, `check_failure_rules`, `is_bottom_confirmed`, `backtrack_verify`, `evaluate_momentum_rotation`, `adaptive_s10_floor`, `should_pyramid_into_winner`, `determine_max_positions`, `send_daily_summary`, `log_closed_trade_dna`; private `_heal_symbol_cache`, `_cache_stale`, `_has_strong_setups_today`, `_execute_buys`, `_active_setup_symbols`, `_live_equity`, `_HEAL_ATTEMPTED`; constants `XLSX_FILE`, `SYMBOL_FULL_DIR`, `BASE_DIR`; patched-through submodules `openpyxl`, `datetime`, `etrade`, `rapidapi`, `circuit_breaker`, `instruments`.
- **Non-test consumers keep importing from `ai_portfolio_game`:** `data_api.py` (`get_live_prices`, `get_google_prices_fallback`; actions `ai_game_run`/`summary`/`force`), `server.py` (`get_live_prices`, `get_strategy_rules`), `real_copilot.py` (`load_game`, `is_bottom_confirmed`), `aether_oracle.py` (`calculate_bubble_z_score`), `scripts/utils/intraday_monitor.py`. **CLI unchanged:** `--run`/`--profile`/`--force`/`--report`/`--summary` (invoked by `watchdog.py`, `register_agent_tasks.ps1`).

## Risks (carry into every PR)

1. By-reference `state` mutation shared with `circuit_breaker`/`options` → entities must be views (no copy).
2. `ws` row-scan magic indices — preserve per-site `[8]` (SELL) vs `[10]` (BUY/report), do not unify.
3. `is_market_hours` is evaluated late and repeatedly — keep call timing, don't hoist.
4. `_HEAL_ATTEMPTED` / `_SYMBOL_DAY_CACHE` must stay the same object via `_pkg()`.
5. Mandatory Backup Policy on any `save_game`/state-write seam.
6. Non-test consumers need the re-export surface intact (R13 facade).
7. Root-file compile constraint (`test_executables`).
8. Circular import avoided via lazy `_pkg()` (`import ai_portfolio_game` inside functions).
9. Direct `git push` is unavailable in this environment, so each PR lands through the sanctioned API push path; keep every PR small and self-contained.

## Critical files

- Refactor target: `ai_portfolio_game.py` (stays at root; ends as a thin facade).
- New package: `aether/scenario/{__init__,schema,prices,steps,runners}.py`.
- Template to mirror: `aether/etrade/{store,client,__init__}.py` (ports/adapters/factory + `_pkg()` seam), `aether/options_adviser.py` (dataclass views), `aether/sell_rules.py` (`exit_decision` 2-tuple pure-decision interface).
- Reuse (do NOT reimplement): `sell_rules.exit_decision`, `circuit_breaker.*`, `options.*`, `decision_eval.build_entry`, `instruments.*`, `CFG`.
- New tests: `tests/test_scenario_*.py` + `tests/test_scenario_characterization.py`.

## Verification (every PR)

1. `python -m unittest discover -s tests -v` — full suite green (baseline ~824 OK).
2. `python -m unittest tests.test_executables` — root-file compile guard.
3. From R3 on: `tests/test_scenario_characterization.py` snapshot unchanged.
4. New package tests (`tests/test_scenario_*.py`) green; `tests/test_game_pricing.py` green across the R1 boundary.
5. Branch from latest `origin/main` in an isolated worktree; keep each PR small and self-contained.
