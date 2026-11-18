# portfolio service

**Purpose:** owns portfolio state and how it is stored (R&D #78, `plans/aether-services.md`).

**Public API (`api.py`):**
- `open_state_file(path, backup_dir, initial_balance=, log=)` returns a `StateStore`, which has
  `load() -> dict` and `save(state)`.

**Data owned:** `Data/ai_portfolio_game.json` and its backups in `Data/Backup/Game/`
(the last 15 are kept).

**Status:** S4a moved `load_game` / `save_game` here as they were. Next:
- F1: stop when the backup fails, and write atomically;
- F2: start a fresh portfolio only on a true first run;
- S4b: the domain model (`Portfolio`, `Position`) and a JSON mapper on top of `StateStore`;
- S4c: read-only `PortfolioQueries`, used by the modules that read the file directly today.

**Events:** none yet.
