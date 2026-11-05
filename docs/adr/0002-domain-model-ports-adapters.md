# 0002 — Domain model behind ports and adapters

**Status:** Accepted, 2026-11-05.

## Context
- **Raw dicts, changed in place:** portfolio, positions, orders and transactions are raw dicts
  that any function can change. The rules are enforced nowhere:
  - the SELL transaction is built 4 ways;
  - "close a position" exists 3 times;
  - "open a position" exists twice, and the two copies have drifted;
  - nothing guarantees that a stop is never lowered.
- **Hidden, repeated I/O:** about 6 live E*TRADE clock calls and 5+N Research-sheet scans per run.
- **Tests patch many attributes:** they patch 20+ module attributes per case.

## Decision
- **DDD tactical patterns.**
  - Value objects (frozen dataclasses): `Quote`, `BuyDNA`, `WrittenCall`, `Transaction`,
    `QueuedOrder`, `StrategyProfile`, `ResearchRow`, `MarketSnapshot`, `ExitDecision`,
    `Reject`.
  - Entity `Position`. It owns its own rules: `raise_stop` never lowers, `scale_out` keeps at
    least 1 share, `track_peak`.
  - Aggregate root `Portfolio`, the only way to change positions, cash, history or the queue. It
    records domain events.
- **Ports are `typing.Protocol` classes:** `PriceFeed`, `MarketClock`, `OhlcvStore`,
  `ResearchSource`, `PortfolioRepository`, `Notifier`, `TradeLedger`, `ExecutionPort`, `Clock`.
  Adapters implement them.
- **One composition root:** `aether/bootstrap.py` builds the adapters from config and injects
  them by constructor. Tests call `bootstrap(test=True)` with in-memory fakes.
- **Other patterns:**
  - Chain of Responsibility for the price ladder.
  - Strategy for profiles and exit, trailing-stop and sizing policies.
  - Specification for buy gates, each returning `Reject(reason, values)`.
  - Domain events + Observer for side effects: trade DNA, the excel log, the decision log, the
    option-unwind hook.
  - An `ExecutionPort` with a paper adapter (changes the portfolio) and an advisory adapter
    (recommends only).
  - A read model (`PortfolioQueries`) for the dashboard and reports.

## Why
- These patterns are the standard way to keep related rules always true. They remove the
  duplicated code by construction and give each rule one tested home.
- Ports and one composition root replace call-time lookups, so dependencies point inward.
- Facts are read once per run and passed in, which removes the hidden repeated I/O.

## Rejected
- **pydantic or attrs in the domain:** a new dependency. Stdlib `dataclasses` + `Protocol` match
  the repo's style, and FastAPI's pydantic stays at the `http.py` edge.
- **SQLAlchemy ORM entities as the domain:** that couples the domain to the database. Data mappers
  keep the domain free of persistence.
- **A DI container:** one composition root is enough at this size.
- **`abc.ABC` ports like `aether/etrade/store.py`:** `Protocol` needs no inheritance.

## Consequences
- **Migration preserves today's behaviour exactly.** The circuit breaker's current stop restore,
  which can lower a stop, goes through an explicit `Position.restore_stop()` until the F8 study
  decides. No new invariant may reject a move that is legal today; new guards are separate fixes.
