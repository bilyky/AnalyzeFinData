# Plan: AETHER as modular services, with a real domain model, many portfolios, and a database-ready data layer

> R&D #78 (approved 2026-11-05). Replaces the R&D #44 "scenario" refactor ([scenario-refactor.md](./scenario-refactor.md), now superseded).
> The decisions are recorded as ADRs in [`docs/adr/`](../docs/adr/). Progress is tracked in R&D #78 in [`roadmap.md`](./roadmap.md).

## Context — why we are re-planning

R&D #44 (plans/scenario-refactor.md) lifted the 720-line `run_daily_ai_management` into 11
verbatim stages in `aether/scenario/steps.py`, and #228 made the game run them. The extraction
was verified, with a golden snapshot, 155/155 mutants caught, and a byte-identical real-data dry
run. But the design is poor. Findings on origin/main (measured at `abde1a2`, 2026-11-04; the counts are a
snapshot, and the Development > Project Model page shows them live):

- **No object model.** Portfolio, positions, orders and transactions are raw dicts changed in
  place by any function. The SELL transaction is built 4 ways, "close a position" exists 3
  times, and "open" exists twice and has drifted.
- **Dependencies point the wrong way.** Every stage does `game = _pkg()` (a lazy
  `import ai_portfolio_game`) and makes 130 `game.X` calls. The package depends on the root
  script, only so tests can patch it.
- **One god-file.** `ai_portfolio_game.py` is 1,531 lines and 40 functions, mixing storage,
  prices, calendar, cache healing, buy execution and reporting. It and `aether_oracle` import
  each other, so in script mode the file loads twice.
- **Hidden, repeated I/O.** About 6 live E*TRADE clock calls and 5+N Research-sheet scans per
  run, a workbook read hidden inside `get_strategy_rules`, 38 magic `row[N]` indexes, and about
  10 modules that open `ai_portfolio_game.json` directly.
- **About 940 dead lines.** `scenario/prices.py`, `schema.py`, `helpers.py`.
- **Only one portfolio is possible.** The state path is hard-coded, and the real accounts
  (copilot, oracle) and OceanView each re-implement their own rules on their own data shapes.
- **Real bugs:**
  - the state file can be written without a backup, and not atomically;
  - a corrupt state file silently becomes a fresh $10k portfolio;
  - queued buys skip the buy safety checks;
  - the circuit breaker lowers stops when it clears (confirmed);
  - the run gate ignores holidays;
  - 7 OHLCV readers are not split-adjusted.

**What the user asked for (2026-11-04/05):**
- organize by concern;
- well-known patterns and a proper object model;
- best software-design practice;
- a proper mini-service architecture;
- a data layer ready to switch to a database;
- support any number of projects: **both** many portfolios/strategies **and** many consumer
  projects;
- show all the reasoning;
- state-file safety first; study the circuit breaker first; split the golden test.

## Decisions taken with the user
| Question | Choice | Reasoning |
|---|---|---|
| Target | Organize by concern with a real domain model | One owner per concern. Rules are enforced by objects, and dependencies point inward. |
| Services | **Modular services, one process first** | Each service has a public interface, owns its data, and can't be reached into. All run in one process today (CLI + FastAPI dashboard). Any service can later get its own FastAPI process without callers changing. Separate processes now would add ports, startup order and retries on one Windows box, for no current gain. |
| Database | **PostgreSQL via SQLAlchemy + Alembic** | Matches what exists (`database.url` / `DATABASE_URL`, SQLAlchemy, the E*TRADE DB-store stub). JSON files stay the default until an explicit opt-in. That's the lesson from #144: a URL alone must not switch the store. |
| Projects | **Many portfolios AND many consumer projects** | `portfolio_id` runs through everything, and services expose stable versioned interfaces that other projects use. |
| Fix order | State-file safety first, trading fixes last | F1/F2 change no trading decision and protect real data during the migration. Trading fixes need evidence first anyway. |
| Circuit breaker | Study first | It's a trading-rule change, so the backtest comes first. |
| Golden | Split it | Behaviour stays pinned. The required log lines become explicit assertions, so code moves don't churn the golden. |

## Architecture

### Modular services (a "modular monolith" that can later be split)
```
consumer projects: CLI (ai_portfolio_game.py) · FastAPI dashboard (server.py) · OceanView ·
                   backtest · copilot/oracle · future tools
                                │ only through each service's public api (Python now, HTTP later)
 ┌──────────────┬───────────────┼───────────────┬────────────────┬───────────────┐
 │ market_data  │ research      │ portfolio     │ trading        │ reporting     │ ledger
 │ prices,      │ screener      │ Portfolio     │ daily run per  │ summaries,    │ trade DNA,
 │ OHLCV bars,  │ output →      │ aggregate,    │ portfolio:     │ email, excel  │ decision log,
 │ freshness/   │ ResearchRow,  │ registry of   │ exits, entries,│ perf log;     │ failure rules,
 │ heal,        │ regime inputs │ portfolios,   │ sizing, breaker│ reads via     │ scorecard;
 │ calendar,    │               │ repository    │ covered calls  │ portfolio api │ event
 │ session      │               │ (JSON | DB)   │                │               │ subscribers
 └──────────────┴───────────────┴───────────────┴────────────────┴───────────────┘
                     shared kernel: aether/core (ids, Clock, config, logging, errors, event bus)
```
`trading` calls `market_data`, `research`, `portfolio` and `ledger` through their APIs.
`reporting` reads through `portfolio`'s read API. No service imports another service's
internals.

### Each service has the same internal layout (Hexagonal / Clean)
```
aether/services/<name>/
  api.py        PUBLIC: facade functions + DTOs (frozen dataclasses) + API_VERSION   ← the only import surface
  domain/       entities, value objects, policies (pure, no I/O)
  ports.py      Protocol interfaces the domain/app needs (repositories, feeds, clocks)
  app.py        use cases (application services)
  adapters/     JSON-file, SQL, E*TRADE, Google, workbook, SMTP … implementations of ports
  http.py       optional FastAPI router (mounted in server.py now; standalone process later)
```
**Dependency rule:** `adapters → ports ← app → domain`. `api` exposes `app`. Domain imports no
adapters. An **architecture test** (an import-linter-style AST check) fails the build if:
- a module imports another service's internals instead of its `api`;
- a domain module imports I/O;
- anything under `aether/` imports `ai_portfolio_game`.

### Composition root
`aether/bootstrap.py` builds the adapters from config (JSON or DB, real or fake) and wires them
into each service's `app`. The CLI and `server.py` call `bootstrap` once. Tests call
`bootstrap(test=True)` with fakes. This replaces the `_pkg()` call-time lookups.

## Domain model (well-known patterns, with the reason for each)

**P1. DDD tactical patterns, in the `portfolio` and `trading` domains**
- **Value objects** (frozen `@dataclass`): `PortfolioId`, `Quote`, `BuyDNA`, `WrittenCall`,
  `Transaction`, `QueuedOrder`, `StrategyProfile`, `ResearchRow`, `MarketSnapshot`,
  `ExitDecision`, `Reject(reason, values)`.
- **Entity** `Position`. It owns its rules: `raise_stop()` never lowers the stop,
  `scale_out()` keeps at least 1 share, and `track_peak()` keeps the high close.
- **Aggregate root** `Portfolio(id, kind, cash, positions, history, queue, profile)`. It is the
  only way to change any of those: `open_position`, `close_position`, `scale_in`/`scale_out`,
  `queue`, `mark_to_market`. It records domain events.
- **Why:** these invariants have to hold together. This removes the duplicated
  open/close/SELL code by construction and gives one tested home per rule.
- **Migration rule:** today's behaviour is reproduced exactly. Today's breaker restore, which
  can lower stops, goes through an explicit `restore_stop()` pending the F8 study. No new
  invariant may reject a move that is legal today; new guards are F-fixes.

**P2. Repository + Data Mapper + Unit of Work: the data layer, ready for a database**
- Ports: `PortfolioRepository.load(id)` / `save(portfolio)`, `PortfolioRegistry.list()` /
  `get(id)`, `PortfolioQueries` (read model for the dashboard and reports).
- Adapters:
  - **`JsonPortfolioRepository`** (the default) owns the file, the backups (keep 15), the atomic
    write and the restore from backup. Its mapper **round-trips today's JSON byte-for-byte**:
    unknown keys are kept, and the key order and per-transaction-type key sets are reproduced.
  - **`SqlPortfolioRepository`** uses SQLAlchemy Core tables (`portfolios`, `positions`,
    `transactions`, `queued_orders`, `outbox`) with its own SQL mapper and Alembic migrations.
  - Both pass **the same repository contract test suite**.
- **`UnitOfWork`** loads, lets the service change the portfolio, then commits once: a backup
  plus an atomic write for JSON, a transaction for SQL. It also saves the domain events to an
  **outbox** in the same commit.
- **Switching store:** explicit opt-in, `AETHER_PORTFOLIO_STORE=db` (or `portfolio.store: db` in
  config). A `DATABASE_URL` alone never switches it (lesson from #144). A one-shot backfill tool
  copies JSON into the DB. A shadow phase reads both stores and diffs them before the cutover.
- **Backup policy for the DB:** a `pg_dump` before migrations, plus point-in-time backups,
  replace the file copy. To be confirmed with the user at the cutover.
- **Why:** domain code never sees storage, so moving to PostgreSQL is a new adapter plus a
  config switch, not a rewrite. The repository is also where F1/F2 (state safety) live.

**P3. Ports & Adapters with constructor Dependency Injection**
- Ports are `typing.Protocol`: `PriceFeed`, `MarketClock`, `OhlcvStore`, `ResearchSource`,
  `Notifier`, `TradeLedger`, `ExecutionPort`, `Clock`.
- Adapters wrap today's code, moved verbatim first.
- **Why `Protocol`:** no inheritance needed (unlike the `abc` classes in `etrade/store.py`).
- **Why no DI container:** one composition root is enough at this size.

**P4. Chain of Responsibility: the price ladder in `market_data`**
`ChainedPriceFeed([etrade, google, ohlcv_cache])` keeps today's exact rules:
- weekend → cache, then Google;
- after hours with a fresh workbook → cache, then Google;
- otherwise E*TRADE, with Google filling gaps;
- any exception → Google.

It replaces `get_live_prices` and the unused copy in `scenario/prices.py`.

**P5. Strategy pattern, in `trading`**
- **`StrategyProfile`** per profile and per portfolio. It is built from the regime passed in;
  there's no hidden workbook read.
- **Policies:** `ExitPolicy` wraps `sell_rules.exit_decision` and `ai_exit_override`.
  `TrailingStopPolicy` handles the ratchet and breakeven, with one 8% default constant.
  `SizingPolicy` handles share qty, the scarcity, conviction and bucket caps, and
  `risk_capped_cash`.
- **Why:** each portfolio picks its rules as data plus small classes. The backtest can import a
  profile where its thresholds truly match, instead of copying them.

**P6. Specification pattern: buy gates**
- Each gate is a spec with `check(candidate, ctx) -> None | Reject(reason, values)`:
  - momentum floor, cache freshness, gap-down trap, reward/risk (with the elite waiver), target
    gain, profile threshold, overbought penalty;
  - bubble z, backtrack trend, failure DNA, bucket cap, risk cap, cash buffer.
- They compose into named spec sets, and the service writes each `🛑 AI BUY REJECTED` line
  with its values.
- **Migration:** today's live and queued spec sets are reproduced exactly. Unifying them is F7.

**P7. Domain events + Observer + Outbox**
- `Portfolio` records `PositionOpened`, `PositionClosed`, `ScaledOut`, `ScaledIn`,
  `OrderQueued`, `StopChanged`.
- After commit they go, in order, to the ledger subscribers (trade DNA, decision log), the
  reporting subscriber (excel log), and the option-unwind hook.
- With the database they go through the outbox table, which is also the future hand-off to
  separate processes.
- **Why:** side effects leave the domain, each side effect has one writer, and splitting
  services into processes later needs no redesign.

**P8. Execution as a port: paper vs advisory**
- `ExecutionPort` has two adapters:
  - **`PaperExecution`** changes the portfolio. That's the AI game today.
  - **`AdvisoryExecution`** only records recommendations, for real E*TRADE accounts, copilot,
    oracle and OceanView.
- Each portfolio's `kind` picks one.
- **Why:** "many portfolios" includes real accounts that must never be auto-traded. One engine
  serves both, safely. Moving copilot/oracle onto this engine comes later and stays optional.

**P9. Read models (CQRS-lite) and the ResearchRow column map**
- `PortfolioQueries` serve the dashboard, reports and other projects without loading the
  aggregate.
- `research` builds `ResearchRow` once per run from one column map, using
  `workbook_write.RESEARCH_HEADERS` plus named constants for user columns 3 and 8. The map is
  checked against row 1, and a mismatch fails loudly.
- The SELL `row[8]` and BUY `row[10]` previous closes stay as two separate fields.
- This fixes the fallback in `options.py:292` that reads column 23 ("Win% 10d") as ATR.

## Many portfolios (multi-tenant by `portfolio_id`)
- **Registry:** `PortfolioRegistry` holds `portfolios(id, name, kind=paper|advisory,
  strategy_profile, starting_capital, enabled, config)`.
  - JSON adapter: `config/portfolios.json`.
  - DB adapter: the `portfolios` table.
- **Today's game becomes `portfolio_id="ai_game"`** (the default). Its file stays
  `Data/ai_portfolio_game.json` for compatibility. New portfolios use
  `Data/portfolios/<id>.json`, or rows in the DB.
- **Every API takes `portfolio_id`:** `trading.api.run_daily(portfolio_id, …)`,
  `portfolio.api.load(portfolio_id)`, and so on. The CLI gets `--portfolio ai_game`, which is
  the default.
- **The daily guard and the stamp are per portfolio:** `DailyRunGuard(f"portfolio_state:{id}")`.
- **Shared inputs:** market data and research are shared across portfolios and read once per
  run batch. Portfolio state and ledgers are keyed by `portfolio_id`.

## Many consumer projects (services as a platform)
- **Stable surface:** each service's `api.py` is its contract. Its DTOs are frozen dataclasses
  with `to_dict`/`from_dict`. `API_VERSION` follows semver; breaking changes bump the major
  version and keep the old function for one release.
- **Contract tests** per `api.py`. A consumer project depends only on these.
- **HTTP:** each service's optional `http.py` FastAPI router mounts in `server.py`. Projects in
  other repos use HTTP with the same DTOs as JSON, and an OpenAPI schema is generated
  automatically.
- **Splitting a service out later:** run its router as its own process. In-repo callers swap to
  an `HttpClient` adapter of the same port. No caller code changes.

## Rejected alternatives (and why)
- **Keep the scenario stages and `_pkg()`.** That's the inverted dependency we're fixing.
- **Separate processes now.** Operational cost on one machine with no current need. The
  boundaries allow it later.
- **A generic pipeline engine** (RunContext, Step protocol). The application services are
  clearer, and there's no second consumer of a generic engine.
- **pydantic or attrs for the domain.** That's a new dependency. Stdlib `dataclasses` +
  `Protocol` match the repo's style. FastAPI's pydantic stays in the `http.py` edge only.
- **ORM entities as the domain** (SQLAlchemy declarative models). That couples the domain to
  the DB. Data mappers keep the domain persistence-free.
- **Changing the JSON schema now.** About 10 readers depend on it. The mapper preserves it, and
  readers move to `PortfolioQueries`.
- **Switching to the DB as soon as `DATABASE_URL` is set.** That caused #144. Opt-in only.

## Testing and engineering practices
- **Test pyramid:**
  - pure domain unit tests on real numbers (no mocks);
  - application tests with in-memory fakes from `bootstrap(test=True)`;
  - adapter tests (JSON in a temp dir; SQL via SQLAlchemy on SQLite in-memory, plus PostgreSQL
    behind `AETHER_LIVE_TESTS`);
  - **repository contract tests** run against every adapter;
  - **api contract tests**;
  - the **architecture test** (dependency rules).
- **Golden split:**
  - **(a)** a behaviour golden for the current 11 cases: saved state, transactions, decisions,
    DNA, emails, covered-call calls, exceptions;
  - **(b)** `test_daily_run_audit_logs.py`, with about 15 assertions on the required lines.

  Every structural PR keeps (a) byte-identical. After the multi-portfolio change, the golden runs
  as `portfolio_id="ai_game"`.
- **CI:** add a job that runs the unit, contract and architecture suites (today CI runs only
  compile, the validator and ruff).
- **Retire:** `mutate_scenario_steps.py`, only after the new unit tests catch all 155 of its mutants
  (or a mapped equivalent of each). That's a measured gate in S9, not a promise.
- **Hermetic tests:** every new path or store gets its redirect in `tests/__init__.py` in the
  same PR.
- **Every PR:** red-then-green tests, small diffs, merge-in (not rebase), ruff, the validator.
- **Docs:** each service has a short `README` (purpose, public API, data owned, events).
  ADRs go in `docs/adr/` for these decisions: modular monolith, the DB opt-in, multi-portfolio,
  and the event outbox.

## Fixes (each its own PR, red test first; trading changes need evidence)
| # | Fix | Trading? | Evidence | When |
|---|---|---|---|---|
| F1 | Repository: abort and alert if the backup fails; atomic write | no | unit tests | right after the JSON repository |
| F2 | Fresh $10k only on a true first run; corrupt state with no restorable backup → raise | no | unit tests | with F1 |
| F3 | The run gate uses holiday-aware calendar/session | holidays | red test 2026-11-26 | early, before 2026-11-26 (step 1c) |
| F4 | Remove the 4 doubled log lines | no | audit test | after the golden split |
| F5 | The dashboard regime equals the traded regime (one function) | display | unit test | after `trading` strategy |
| F6 | Column 23 ≠ ATR (`options.py:292`) | header-missing case | red test | with the `research` column map |
| F7 | Queued BUY uses the live spec set | **yes** | replay past queued buys: rejects and P&L | end |
| F8 | Circuit-breaker restore lowers stops: **study** max(original, ratchet) vs never-restore | **yes** | backtest on trigger dates | end |
| F9 | The 7 raw OHLCV readers move to split-adjusted bars, one at a time | **yes** | backtest each | end |
| F10 | Unify the SELL keys and the open-position shape | data shape | reviewed golden update | end |
| B9 | Game state follows `$AETHER_DATA_DIR`? It also moves the E*TRADE token | ops | check the PROD task's environment | user decision |

## Migration (strangler, small PRs; S = structural, golden (a) identical)
1. **S0 docs:**
   - `plans/aether-services.md` (this plan) plus ADRs;
   - mark `plans/scenario-refactor.md` Superseded, saying what it left behind;
   - in the roadmap, mark R&D #44 superseded, open a new item, and track F1–F10.
1b. **S0b Development → Project Model page:** a dashboard tab fed by `/api/dev/project-model`.
   The source of truth is `plans/aether-services-progress.json` (steps, fixes and services, each with its status and
   PR), updated in every PR. The page also shows live code metrics: root-file lines, `game.X` calls, magic `row[N]`
   indexes, and `aether/scenario` lines.
1c. **F3 holiday-aware run gate, early:** a small independent PR that lands before 2026-11-26 (Thanksgiving).
   Today `is_market_open()` checks only weekends and 9:30-16:00 ET, so the 07:00 run passes its first gate on a
   weekday holiday. It doesn't wait for S5.
2. **S1 golden split** plus the audit test. **S1b** CI job for the unit, contract and
   architecture suites.
   **Gate (measured):** with the game calling the runner, the golden-only mutant sweep (each
   `mutate_scenario_steps.py` mutant against the golden test alone) catches 56 of 155 with the log stream
   and 48 without it (#241 review). S1 must show that the behaviour golden plus the audit test still catch
   all 56. The 8 caught only through log lines need explicit checks: cash buffer off, the s10 floor's
   cash-percent scale, the R:R reject, the queued-order dedup type, the stop ratchet never lowering, the
   abort log, the cash-deployment gate upgrade, and the heal scope. Several of these are behaviour, not wording.
3. **S2 delete dead code:** `scenario/prices|schema|helpers` and their tests,
   `deduct_operational_costs`, `--report`.
4. **S3 shared kernel:** `aether/core` (ids, Clock, event bus, errors) plus the architecture test
   and an empty `aether/bootstrap.py`.
5. **S4 `portfolio` service:**
   - the domain model and JSON mapper, with round-trip tests on the golden states and a sandbox
     copy of a real state;
   - `JsonPortfolioRepository`, a verbatim move of load/save;
   - the unit of work;
   - `PortfolioQueries`; the ~10 direct readers move to them.

   Then **F1 + F2**.
6. **S5 `market_data`:** clock/session (computed once per run), `OhlcvStore` (freshness and heal,
   per-run caches), `ChainedPriceFeed`. The game's functions delegate, so external importers keep
   working. (F3 already landed early, as step 1c.)
7. **S6 `research`:** `ResearchSource` plus the column map, read once per run. Then **F6**.
8. **S7 `ledger`:** DNA, decision log and failure rules as event subscribers.
9. **S8 `trading`:** `StrategyProfile`, the policies and the spec sets, then `DailyRunService`
   replacing the scenario stages one group per PR (settle/queued, exits, entries, deploy). The
   `Portfolio` methods replace the dict mutation. Golden (a) on each PR. Then **F5**.
10. **S9 delete `aether/scenario/`,** `_pkg`, the validator exemptions and `test_scenario_*`.
    **Gate (measured):** before deleting the stage tests and `mutate_scenario_steps.py`, the new policy and
    service unit tests must catch all 155 mutants, or a mapped equivalent of each, listed in the PR.
11. **S10 `reporting`:** summaries, email and the excel log. Reports no longer write state.
12. **S11 composition root + thin CLI:** `bootstrap`; external importers (data_api, server,
    real_copilot, aether_oracle, intraday_monitor, validate_scarcity_cap) move to the service
    APIs; re-exports are dropped; the import cycle is gone. **F4** can land any time after S1.
13. **S12 multi-portfolio:** the registry; `portfolio_id` end-to-end; per-portfolio guard and
    stamp; `--portfolio` flag. `ai_game` is the default, and the golden is unchanged.
14. **S13 HTTP routers:** `http.py` per service, mounted in `server.py`, plus API contract tests
    and OpenAPI. This is what other projects use.
15. **S14 PostgreSQL data layer:**
    - SQLAlchemy Core schema plus Alembic;
    - `SqlPortfolioRepository` passing the shared contract suite;
    - the outbox;
    - a backfill tool;
    - a shadow-read diff;
    - the opt-in switch.

    The cutover happens only after the shadow diff is clean, with your go-ahead. Later, the same
    move for `ledger` and `market_data`.
16. **F7, F8 (study first), F10, F9** one by one; **B9** decision.

**PROD risk:** structural PRs keep golden (a) identical and get a smoke check. S14's cutover and
F7–F9 change PROD behaviour or storage: each needs evidence, a backup and a PROD smoke run.

## Out of scope (for now)
- Separate processes, Docker, Kubernetes.
- Refactoring `aether/etrade/` (same patterns later).
- A backtest rewrite.
- New trading rules.
- Moving copilot, oracle or OceanView onto the trading engine (enabled by P8, but a separate
  decision).

## Verification (every PR)
- **Tests:** the full local suite without `PYTHONIOENCODING`, plus the CI unit, contract and
  architecture jobs.
- **Golden:** (a) byte-identical on S PRs; the audit test green; red-then-green on every F PR.
- **Mapper:** round-trip byte-identical on golden states and on a real state copy in a sandbox
  (never the real `Data/`).
- **Lint and compile:** `ruff check .`, the pre-commit validator, `tests/test_executables`.
- **Real data:** for S8, S11 and S14, a real-data dry run of old vs new (sandbox copies, sockets
  blocked), as done for #228.
- **DB (S14):** the contract suite on SQLite and PostgreSQL; the backfill then shadow diff shows
  zero differences before the opt-in.
- **PROD:** back up the state and workbook (and `pg_dump` after S14); watch the next `--run`.
  Roll back by reverting the merge, or by turning the store switch off.

## Critical files
- New: `aether/core/`, `aether/bootstrap.py`, and `aether/services/{market_data,research,
  portfolio,trading,reporting,ledger}/`, each with `api.py`, `domain/`, `ports.py`, `app.py`,
  `adapters/` and `http.py`. Also `aether/db/` (schema, migrations), `config/portfolios.json`
  and `docs/adr/`.
- Changed: `ai_portfolio_game.py`, `server.py`, `data_api.py`, `workbook_read.py`,
  `real_copilot.py`, `aether_oracle.py`, `aether/{circuit_breaker,options,market_calendar,
  sell_rules,risk_utils}.py`, `tests/__init__.py`, `scripts/utils/pre_commit_validator.py`,
  `.github/workflows/ci.yml`, `plans/roadmap.md`, `plans/scenario-refactor.md`.
- Golden: `tests/test_scenario_runner.py` + `tests/fixtures/scenario_runner_golden.json`, which
  become the `trading` run test.
- Removed: `aether/scenario/*`.
