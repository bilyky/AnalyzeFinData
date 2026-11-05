# 0004 — Many portfolios and many consumer projects

**Status:** Accepted, 2026-11-05.

## Context
- **One game portfolio.** Only one is possible today: the AI game, at a hard-coded path.
- **Separate copies of the rules.** The real E*TRADE accounts (copilot, oracle) and OceanView
  each re-implement their own rules on their own data shapes.
- **Many consumers.** Other projects need AETHER's data and decisions: the dashboard, OceanView,
  the backtest and future tools.

## Decision
- **Many portfolios.**
  - **Registry:** a `PortfolioRegistry` lists portfolios: `id`, `name`,
    `kind = paper | advisory`, `strategy_profile`, `starting_capital`, `enabled`, `config`.
  - **Storage:** the JSON adapter uses `config/portfolios.json`; the database adapter uses the
    `portfolios` table.
  - **`portfolio_id` everywhere.** Every service API takes it, and portfolio state and ledgers
    are keyed by it. Market data and research are shared, and read once per run batch.
  - **The current game becomes `portfolio_id = "ai_game"`, the default.** Its file stays
    `Data/ai_portfolio_game.json`. New portfolios use `Data/portfolios/<id>.json`, or database
    rows.
  - **Runs are locked per portfolio:** the daily run guard and stamp are
    `DailyRunGuard(f"portfolio_state:{id}")`. The CLI gets `--portfolio` (default `ai_game`).
  - **Execution depends on `kind`:** a `paper` portfolio uses paper execution and is traded; an
    `advisory` portfolio (real accounts) only gets recommendations and is never auto-traded.
- **Many consumer projects.**
  - **The contract:** each service's `api.py` is its contract, with frozen-dataclass DTOs
    (`to_dict` / `from_dict`) and a semver `API_VERSION`. A breaking change bumps the major
    version and keeps the old function for one release.
  - **Contract tests** per API.
  - **HTTP:** an optional `http.py` FastAPI router per service mounts in `server.py`, with
    OpenAPI generated automatically. Projects in other repos use HTTP with the same DTOs.

## Why
- One engine serves every portfolio. Real accounts are protected by design, because advisory
  execution can't change them.
- Consumers depend on a stable, versioned surface instead of internals.

## Rejected
- **One process per portfolio, or a copy of the code per portfolio:** that brings back the
  duplication.
- **Letting consumers import internals:** it breaks the boundaries in ADR 0001.

## Consequences
- **Opt-in moves only:** moving copilot, oracle or OceanView onto the engine is enabled, but each
  move is a separate decision.
- **The golden test still runs `ai_game` unchanged** after the multi-portfolio change.
