# portfolio service

**Purpose:** owns portfolio state: the domain model and how it is stored
(R&D #78, `plans/aether-services.md`, ADR 0002/0003/0004).

**Public API (`api.py`, `API_VERSION` 0.1.0).** Every call names a portfolio by `PortfolioId`;
no caller sees a path or a storage format.
- `PortfolioService.load(portfolio_id)` returns a `Portfolio`.
- `PortfolioService.unit_of_work()`: `get(id)`, change the portfolio, then `commit()`. That
  saves once and publishes the domain events.
- Domain types: `Portfolio` (aggregate root), `Position` (entity), `BuyDNA`, `Transaction`,
  `QueuedOrder` (value objects), the `StopChanged` event, and `UnknownPortfolio`.

**Layout:**
- `domain/model.py`: pure model, with no I/O.
- `ports.py`: `PortfolioRepository` (`load(id)`, `save(portfolio)`).
- `app.py`: `PortfolioService`, `UnitOfWork`.
- `adapters/json_repository.py`: `JsonPortfolioRepository`, the default store.
- `adapters/json_mapper.py`: the data mapper; the stored dict round-trips exactly.
- `adapters/memory.py`: an in-memory store for tests.
- `adapters/legacy_state.py`: `LegacyStateGateway`, which gives the raw state dict to the code
  that still changes it in place (the game, `aether.scenario`). Every call goes through the model
  and the repository. A state the mapper refuses is written to
  `ai_portfolio_game.json.unsaved-<time>`, an ERROR is logged, and the save raises. Built by
  `bootstrap` as `App.portfolio_state`. Deleted in S9.

**Wiring:** `aether.bootstrap` builds the repository and the gateway, and it is the one place
that names the game's file. Only `bootstrap` and the service's own adapters may import
`adapters/` (rule 5 in `tests/test_architecture.py`). JSON is the default, and only
`AETHER_PORTFOLIO_STORE=db` asks for the database. Until S14 that fails loudly, and a
`DATABASE_URL` alone never switches the store.

**Data owned:** `Data/ai_portfolio_game.json` (portfolio `ai_game`) and its backups in
`Data/Backup/Game/` (the newest 15 are kept).

**Events:** `StopChanged`.

**Next:**
- F1: stop if the backup fails, and write atomically;
- F2: start a fresh portfolio only on a true first run;
- `PortfolioQueries` for the modules that read the file directly today, and the one other
  writer, `aether/data_completeness.accept_fields()` (#288), moved onto the service;
- the open, close and scale methods move onto `Portfolio` with their callers in S8.
