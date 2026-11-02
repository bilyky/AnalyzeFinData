# PROD deploy checklist — OceanView session (#146 #155 #160 #170 #177 #180, + #188 #190 #191 #198 #201) — 2026-10-12, updated 2026-10-27

One page, in run order, for everything this session merged. Details and rollback for each item
are in its own note in this folder. Other sessions' notes (`2026-10-06-prod-finalize.md`,
`…-theme-watch-156`, `…-171-172`, `…-audit-actions`) ride the same pull; follow them too.

PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`; `python` = `venv_new\Scripts\python.exe`.
Quiet window ~18:00–20:30 PT (after the evening task's report).

**I can't see PROD.** Some of this may already be done there. The dev copy of PROD's
Sunday-evening status file suggests #177 is already running (see `…-adviser-180.md`).
Check `git log -1 --oneline` against the merge commits below first:

| PR | merge on main | what |
|---|---|---|
| #155 | `aeddd12` | placeholder bars out of ATR/stops; weekend placeholders; 1% per-trade loss cap |
| #160 | `a6b43e5` | RapidAPI repair: 400/run, most-starved first, real timeout, runs last |
| #146 | `2a3c699` | candlestick contrarian sign only in scoring |
| #170 | `c8fe4b0` | account digits out of source → `config.json`; pack health fixes |
| #177 | `4b05514` | pack built in the evening pipeline; watchdog alert |
| #180 | `f6cf90e` | adviser skill boots from the pack; one status writer; weekend rule in the pack |
| #188 | `561ad8b` | backtest models the 1% loss cap (research script, no runtime effect) |
| #190 | `df8eb99` | backtest rule attribution `--attribution` (research script, no runtime effect) |
| #191 | `7e597ea` | backtest out-of-sample `--split` (research script, no runtime effect) |
| #198 | `f57f0b3` | **new task** `AETHER_OptionChainSnapshot` 12:35 PT; live spot fix; registrar `-Only` |
| #201 | `a16006a` | roadmap R&D #54 (docs only) |

```
git log --oneline -1
git merge-base --is-ancestor f6cf90e HEAD && echo "#146-#180 already pulled"
git merge-base --is-ancestor a16006a HEAD && echo "everything in this table already pulled"
```

## A. Pre-checks (read-only, before the pull)

1. **Evening task + time limit** (`…-ohlcv-rapidapi.md` A1): the task that runs
   `daily_task.py` (likely `\AETHER_Agents\AETHER_AftermarketReport`) must have
   `ExecutionTimeLimit` ≥ `PT7H` (#176 set 420 min; #177's worst case is 388 min). Only one
   of it and `AnalyzeFinData_Evening` may be enabled.
2. **Stops that move to the 8% fallback** (`…-ohlcv-rapidapi.md` A2): run main's audit
   before pulling and note `before / after / newly stale`.
3. **Candlestick study sign** (`…-candlestick-146.md` step 1):
   `Data\candlestick_pattern_study.json` must exist with `aggregate_sign: -1`.

## B. Deploy

4. **Back up** core state + `config.json` (finalize note step 2). Write down
   `git log -1 --oneline`.
5. `python server.py upgrade`, then `python server.py status`.
6. **Prune weekend placeholders** (`…-ohlcv-rapidapi.md` B4): dry run, then `--apply` (it
   backs up first).
6b. **Register the option-chain task right after the pull** (#198, elevated PowerShell; never
   run the registrar without `-Only`):
   ```powershell
   .\scripts\utils\register_agent_tasks.ps1 -DryRun -Only AETHER_OptionChainSnapshot
   .\scripts\utils\register_agent_tasks.ps1 -Only AETHER_OptionChainSnapshot
   ```
   Until it's registered, the hourly watchdog emails about a missing task every run.
   Details: `2026-10-20-prod-option-chain-198.md`.
7. **`config.json`: add the `oceanview.sleeves` block** with the real last-4 labels
   (`…-oceanview-170.md`, a private note). Optional: without it, sleeves are `None`.

## C. Verify

8. **Right after the pull** (hermetic tests; they never touch the real `Data\`):
   ```
   python -m unittest tests.test_scoring.TestCandlestickSignGuard tests.test_oceanview_context tests.test_context_pack_gate tests.test_daily_task_recovery_order -v
   ```
   `TestCandlestickSignGuard` must **run** (not skip) and pass.
9. **Skill read** (`…-adviser-180.md`): the Step 0 command prints the verdict, and
   `Data\oceanview_context_status.json`'s timestamp is unchanged.
10. **First evening (~17:30–20:00 PT)**, in `Data\logs\aether.log`:
    - `OceanView context pack: health=… source=live` **before** the `[RapidAPI] … need repair` and
      `Done: …` lines;
    - no `Command timed out` for `rapidapi.py`;
    - the report email at its usual time; no "OceanView context pack alert" email.
11. **Next morning:** re-run the audit (`…-ohlcv-rapidapi.md` C2). Expect weekend placeholders
    ≈ 0, `stale_stops.after` falling, and the ATR ratio moving toward 1.00.
11b. **First trading day, after 12:35 PT** (#198): `Data\logs\agent_runs\option_chain_snapshot.log`
    has one `Snapshot OK … (spot <number> …)` line per symbol (INTC + covered calls), no `ERROR`,
    no `No spot price` warning; `LastTaskResult` 0.
12. **After the 07:00 game run:** `Risk-capping` lines appear only when the 1% cap binds;
    new stops sit wider.

## D. If something's wrong

Each note's Rollback section. Quickest full rollback: finalize note step 8
(`git reset --hard <rollback point>`, restore `config.json`, `python server.py restart`), plus
copying back `Data\Backup\<ts>_weekend_placeholders\Symbol_full\*` if the prune ran.

## Open decisions (not deploy steps)

- The account digits remain in main's git history (from #152). Removing them needs a history
  rewrite; your call.
- Queued buys and pyramiding are not yet covered by the 1% loss cap (refactor session).
- Backtest PRs #188/#190/#191 need nothing on PROD; their notes (`2026-10-20-prod-backtest-*.md`)
  only describe an optional run on PROD data. None recommends a rule or config change.
