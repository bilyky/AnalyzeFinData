# 0003 — Portfolio storage: JSON by default, PostgreSQL by explicit opt-in

**Status:** Accepted, 2026-11-05.

## Context
- **`Data/ai_portfolio_game.json` is a shared contract.** About 10 modules read it directly.
- **`save_game` can write without a backup, and not atomically.** If the backup fails it only
  warns, then overwrites the file.
- **A corrupt state file becomes a fresh $10,000 portfolio, silently.** If no backup can be
  restored, `load_game` returns a new portfolio without an error.
- **The repo already points at PostgreSQL.** It has `database.url` / `DATABASE_URL`, SQLAlchemy,
  and an E*TRADE DB-store stub.
- **Lesson from #144:** PROD sets `database.url`. A store factory switched to an unimplemented DB
  backend because of that setting alone, and caused an outage.

## Decision
- **Repository + Data Mapper + Unit of Work.** `PortfolioRepository.load(id)` and `save(portfolio)`.
  A `UnitOfWork` loads once, lets the service change the aggregate, then commits once. Domain
  events go to an outbox in the same commit.
- **`JsonPortfolioRepository`, the default.** It owns the file, the backups (keep 15), the atomic
  write (temp file + `os.replace`) and restoring from a backup.
  - Its mapper round-trips today's JSON byte for byte: it keeps unknown keys, the key order, and
    each transaction type's key set.
  - Fixes F1 and F2 live here: abort if the backup fails, and raise on a corrupt file that can't
    be restored.
- **`SqlPortfolioRepository`.** It uses SQLAlchemy Core tables (`portfolios`, `positions`,
  `transactions`, `queued_orders`, `outbox`) with its own mapper, and Alembic migrations.
- **Both adapters pass the same repository contract tests.** Tests use SQLite in memory;
  PostgreSQL runs only behind `AETHER_LIVE_TESTS`.
- **The switch is explicit.** It takes `AETHER_PORTFOLIO_STORE=db` (or `portfolio.store: db` in
  config). A `DATABASE_URL` alone never switches the store.
- **Cutover:**
  1. backfill the JSON into the DB;
  2. a shadow phase that reads both and diffs them, until the diff is clean;
  3. the opt-in, with the user's go-ahead;
  4. a `pg_dump` before migrations.

  The backup policy for the database is confirmed with the user at cutover.
- **Read side.** `PortfolioQueries` replaces the direct readers of the JSON file.

## Why
- The domain never sees storage. Moving to PostgreSQL then means adding an adapter and flipping a
  switch, not rewriting.
- The Mandatory Backup Policy and the "no silent failures" rule get a single owner.

## Rejected
- **Switching to the database when `DATABASE_URL` is set:** that caused #144.
- **Changing the JSON schema now:** about 10 readers depend on it.
- **ORM entities as the domain:** see ADR 0002.
