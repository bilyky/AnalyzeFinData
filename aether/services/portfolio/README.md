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

**Saving is safe (F1):** a save backs the file up first and stops if the backup fails. The
file is written to a temp file and swapped in, so it is never half-written; the swap is retried
for about 5 s while another process has the file open (Windows refuses it then). On any
failure the file keeps its last good state, the state goes to `<file>.unsaved-<time>`, an
ERROR is logged and `StorageError` is raised.

**When a run logs `NOT saved` (recovery runbook).** The save is the run's last step, so the
run ends with an error. The state file still holds the state from before the run, and the
Excel log (`Data/ai_portfolio_performance.xlsx`) has no rows for it: the Summary row and the
Transaction_Log rows are written only after a good save. Before the next scheduled run:
1. Read the ERROR line for the cause (the backup failed, the file stayed locked, or the write
   failed) and fix it, for example free disk space or close what holds the file open.
2. Back up the current `Data/ai_portfolio_game.json` (for example into `Data/Backup/`).
3. Copy the `Data/ai_portfolio_game.json.unsaved-<time>` named in the ERROR line over
   `Data/ai_portfolio_game.json`. It is the run's full state, positions and history included.
4. The Excel log is not rebuilt. That run's transactions are in the state's `history`; add the
   missing rows by hand if the sheet must be complete.

If the line says `nowhere (the quarantine write failed …)`, there is no unsaved copy. The run's
changes are then only in the log text, and the next run starts from the last good state.

**When the state cannot be loaded (F2 runbook).** A fresh $10k portfolio starts only on a true
first run: no state file and no backup. If the file is missing, empty or not valid JSON and
none of the backups can be read, the load stops with `StorageError` and an ERROR line
(`[PORTFOLIO] state for ai_game cannot be loaded: …`). The run ends before it trades or saves,
and nothing is written. Before F2 the game silently started again with $10k cash.
1. Keep the evidence: the unreadable file stays where it is, and a copy is kept as
   `Data/ai_portfolio_game.json.corrupt-<time>`.
2. Find a good copy: `Data/Backup/` (the `*_pre_*` folders), `Data/.trash/`, or a PROD copy.
   Check it opens as JSON and holds the positions you expect.
3. Copy it to `Data/ai_portfolio_game.json`, then rerun.
4. To start the game again from $10k on purpose, move both the state file and the
   `ai_portfolio_game_*.json` files in `Data/Backup/Game/` out of `Data/`. Only then is it a
   true first run.

Only a *missing* file is a first run. An empty file (for example a placeholder made when setting
up a new machine) is treated as broken and stops the load. Delete it to start fresh.

**Data owned:** `Data/ai_portfolio_game.json` (portfolio `ai_game`) and its backups in
`Data/Backup/Game/` (the newest 15 are kept).

**Events:** `StopChanged`.

**Next:**
- F2: start a fresh portfolio only on a true first run;
- `PortfolioQueries` for the modules that read the file directly today, and the one other
  writer, `aether/data_completeness.accept_fields()` (#288), moved onto the service;
- the open, close and scale methods move onto `Portfolio` with their callers in S8.
