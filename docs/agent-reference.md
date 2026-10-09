# AGENT.md — reference detail (open on demand)

Full text behind two short rules in [`AGENT.md`](../AGENT.md). The rules there are binding; this file
holds the detail and the history, so it isn't loaded on every session.

## A. Hermetic test harness (AGENT.md §3)

A plain `python -m unittest discover -s tests` run from the main checkout shares its `Data/` with production. It must never change production state:

*   **The Mandate:** No test may write production files (game state and its backups, ledgers, the workbook, auth tokens, or the logs under `Data/logs/`, `Data/autonomous_run.log` and `daily_task.log`), contact a live host, open a real browser, kill a process, or change Task Scheduler.
*   **How it is enforced:** `tests/__init__.py` redirects to temp directories the workbook (`XLSX_FILE`), the learning ledgers (decision log, trade DNA, failure-DNA rules, retrospective report), the E*TRADE token / browser / reauth-state / lock files, the scarcity cache, the game state (`AI_GAME_FILE` **together with** `GAME_BACKUP_DIR`, because `save_game()` prunes that folder to the last 15 backups), the watchdog / pipeline singleton locks (`WATCHDOG_LOCK_FILE`, `PIPELINE_LOCK_FILE`, plus the self-heal lock and prompt), the trash (`trash.TRASH_DIR`, which `run_watchdog()` purges), the run-guard dir, the watchdog's data-sentinel / backup-status files, the market-data caches (`AETHER_CACHE_DIR` is set to temp first and the cache constants are rebound, so `Data/Symbol` and `Data/Symbol_full` are never read or written, junction or not), and the logs above. In hermetic mode it also rebuilds `CFG` from a missing config file, so the real `config.json` (credentials, the E*TRADE TOTP secret) never changes test behavior. Outside live mode it also blocks non-loopback sockets, Playwright launches, and `subprocess` calls that run `taskkill`, `Stop-Process`, `ai_portfolio_game.py`, or mutate Task Scheduler (read-only queries stay allowed). `tests/test_log_hermetic.py` and `tests/test_ledger_hermetic.py` pin it.
*   **The Rules:**
    1.  **Mock at the boundary:** a test that drives code which spawns processes (e.g. `watchdog.run_watchdog()`) must mock `subprocess` itself. A child process never loads the harness, so its writes and side effects are not redirected.
    2.  **New production paths get a redirect:** when code gains a new module-level file path under `Data/` (or a handler opened at import), add its redirect to `tests/__init__.py` in the same change.
    3.  **Prove it after harness changes — in the main checkout too:** hash every file in the repo and in the main checkout's `Data/` before and after a full suite run; the result must be 0 changed / 0 created / 0 deleted. A worktree's `Data/` is nearly empty, so a clean worktree run proves little on its own: plant probe files (fake locks, an old trash file) or run from the main checkout.
    4.  **Live mode is explicit:** only `AETHER_LIVE_TESTS=1` lifts these guards, for the live contract tests.



## B. No links into Data/ (AGENT.md §4)

On 2026-10-06 a worktree whose `Data/Symbol` and `Data/Symbol_full` were directory junctions into the main checkout was removed with `git worktree remove`. `git status` called it clean (`Data/` is gitignored), the removal deleted **through** the junctions, and the real caches (551,974 files) were lost. The junctions had been left by an earlier session four days before.

*   **The Mandate:** Never junction or symlink production `Data/`, or anything inside it, into a worktree or any other folder.
*   **The Rules:**
    1.  **Reach the real caches through the variable, not a link.** Set `AETHER_CACHE_DIR=<main checkout>/Data` for the worktree process. It moves **only** `Data/Symbol` and `Data/Symbol_full` (`aether.paths.cache_dir()`). **Never use `AETHER_DATA_DIR` for this:** it also moves the E*TRADE auth state (token, browser state, reauth lock) and the trash, so the worktree would read and write the production token.
        *   **Honored by:** `aether.risk_utils`, `aether.circuit_breaker`, `aether.scoring`, `aether.decision_eval`, `ai_portfolio_game`, `powergauge`, `rapidapi`, `data_api`, `daily_task`, `run_history`, `aether.ai_buildout` (theme watchlists and their study) and `scripts/backtesting/backtest_ratings.py` (and studies that read through it). **About 40 other scripts under `scripts/` still build their own paths:** check a script before relying on the variable, and route it through `aether.paths` rather than linking.
        *   **It grants writes, not just reads.** Anything run with it (powergauge cache appends, `rapidapi.py` repairs, heals) writes the production caches. For a read-only study, copy the files you need instead.
    2.  **If a link is truly unavoidable**, say so in the PR, and before you finish remove the **link itself** (`cmd /c rmdir <link>` on Windows, `unlink <link>` elsewhere; both remove only the link, never the target), then re-scan.
    3.  **Before removing any worktree, scan it for links** (`scripts/utils/prune_merged_worktrees.py`'s `links_inside()`, or `Get-ChildItem <wt> -Recurse -Force -Attributes ReparsePoint`). Never remove a worktree that still contains one. A link that pointed into `Data/` needs the owner's OK. `prune_merged_worktrees.py` enforces this, and its tripwire stops the run if the main checkout's caches shrink after a removal.
    4.  **Before any batch cleanup, note the cache sizes** (entries in `Data/Symbol`, `Data/Symbol_full`) and compare after. The watchdog's data sentinel alerts on a drop of more than 20% between runs, and on no successful backup for 3 days. It keeps alerting until the data is back; after an **intentional** shrink (a planned prune), delete `Data/data_sentinel.json` so the next run records the new size as the baseline.


