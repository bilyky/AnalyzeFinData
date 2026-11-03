# PROD — OHLCV placeholder fix (#155) + RapidAPI repair budget (#160) — 2026-10-06

Companion to `2026-10-06-prod-finalize.md` (same pull, same quiet window ~18:00–20:30 PROD time).
That note was written before #155, #160 and #163 merged; the pull now also brings:

| PR | What | Effect on PROD |
|---|---|---|
| #155 `aeddd12` | Placeholder bars no longer feed ATR / swing levels / patterns; weekend placeholders no longer written; repair settles every placeholder it covers; **1% of equity per-trade loss cap** on the main AI buy path | ATR stops **widen** (1.4–3.8× on the dev data); big buys in volatile names get trimmed; stop age now measured from the last *real* bar |
| #160 `a6b43e5` | Repair pass: 400-fetch budget per run, most-starved first, stops after 3×429; timeout sized to the budget (was killed at 600 s on 13 of 14 evenings); runs **last** in `daily_task` | Evening report keeps its time; repair runs ~17:30 → ~19:00–20:00 and finally reaches every symbol (rotation, ≤ every other night) |
| #163 `6426831` | Cache paths via `aether.paths`; watchdog data sentinel + backup-health alerts | New alert emails if a cache folder shrinks > 20% or no backup succeeded for 3 days |

All paths below are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`.
Use the project venv (`venv_new\Scripts\python.exe`, written as `python` below).

---

## A. Before the pull (read-only)

### A1. Which task runs `daily_task.py`, and its time limit

PROD's log points at `\AETHER_Agents\AETHER_AftermarketReport` (output in
`Data\logs\agent_runs\aftermarket_report_agent.log`). PowerShell:

```powershell
Get-ScheduledTask -TaskPath '\AETHER_Agents\' -TaskName 'AETHER_AftermarketReport' -ErrorAction SilentlyContinue | Select-Object TaskName, State, @{n='Limit';e={$_.Settings.ExecutionTimeLimit}}
Get-ScheduledTask -TaskName 'AnalyzeFinData_Evening' -ErrorAction SilentlyContinue | Select-Object TaskName, State, @{n='Limit';e={$_.Settings.ExecutionTimeLimit}}
```

- **Both enabled?** Both run `daily_task.py` at 17:00 → two reports. Disable one.
- **Limit under `PT7H`** (e.g. `PT15M`)? Raise it, or Windows stops `daily_task` mid-repair
  (worst case of the whole task is ~388 min since #176 and #177; #176 registers 420 min = `PT7H`).
  *(Updated 2026-10-12: this note first said PT6H, which no longer covers the worst case.)* #160 only changes how tasks get *registered*;
  existing tasks keep their settings. Elevated PowerShell:

```powershell
$t = Get-ScheduledTask -TaskPath '\AETHER_Agents\' -TaskName 'AETHER_AftermarketReport'
$t.Settings.ExecutionTimeLimit = 'PT7H'
Set-ScheduledTask -InputObject $t
```
(For `AnalyzeFinData_Evening` drop `-TaskPath`.)

### A2. How many stops move to the 8% fallback after #155

#155 measures stop age from the last *real* bar; anything older than 10 days falls back to
8% off price. Run main's audit **before** pulling (cmd):

```
git fetch origin
git show origin/main:scripts/diagnostics/ohlcv_placeholder_audit.py > scripts\diagnostics\ohlcv_audit_main.py
python scripts\diagnostics\ohlcv_audit_main.py --json Data\ohlcv_audit_pre_deploy.json
del scripts\diagnostics\ohlcv_audit_main.py
```

Read `stops on the 8% fallback: before … after … newly stale …`.
If **after ≫ before**, those symbols' last real bar is old because the repair never reached
them. That's expected, and #160 fixes it over the next 1–2 nights. It means some stops sit at 8%
until then. If that's not acceptable, deploy now but don't act on stops for those symbols
until the next-morning audit (C2) shows them fixed.

## B. Deploy

1. **Back up**: step 2 of `2026-10-06-prod-finalize.md` (core state files + `config.json`).
   Write down `git log -1 --oneline` as the rollback point.
2. **Pull + restart**: `python server.py upgrade` (git pull + restart), then
   `python server.py status`. `Data\server_commit.txt` should equal `git rev-parse --short HEAD`.
3. **(Optional) tests**: `python -m unittest discover tests`. Hermetic since #161 (expect
   `OK (skipped=6)`, ~1197 tests).
4. **Prune weekend placeholders** (stranded forever, since the API never returns those dates):

```
rem dry run: counts only, writes nothing
python scripts\sync\prune_weekend_placeholders.py
rem apply: backs up each touched file first
python scripts\sync\prune_weekend_placeholders.py --apply
```

   PROD's 10-05 audit counted 4,590 weekend-dated placeholders since 2023; the dry run counts all
   years, so expect that or a bit more. `--apply` copies every file it changes to
   `Data\Backup\<timestamp>_weekend_placeholders\Symbol_full\` before rewriting it. Weekday
   placeholders are left for the evening repair (their close is real).

5. **Config (only to change defaults)**, in `config.json`:
   - `"system": {"max_trade_risk_pct": 0.01}`: per-trade loss cap (1% of equity; `1.0` ≈ off)
   - `"rapidapi": {"max_fetches_per_run": 400}`: repair budget. Keep ≤ ~400: the plan is almost
     certainly RapidAPI **Basic, 500 requests/day hard limit**, and daytime single-symbol heals
     share it. **No second scheduled repair run is needed.**

## C. Verify

### C1. First evening (17:00 → ~20:00)

- Report email at the usual time (~17:30). The repair now runs **after** it.
- `Data\logs\aether.log` gets the run summary (it used to be console-only):

```
findstr /L /C:"[RapidAPI]" Data\logs\aether.log | findstr /L /C:"need repair" /C:"Done:"
rem must show no rapidapi.py line dated after the deploy
findstr /L /C:"Command timed out" Data\logs\aether.log
```

  Expect `N need repair, budget 400 this run, M deferred …` then `Done: X fetched, …`.
  `deferred` > 0 every night is normal (~512 active symbols, 400/night, rotating).

> Note: `2026-10-06-prod-finalize.md` step 7 greps `Data\aether.log`. The logger writes
> `Data\logs\aether.log`, so use that path there too.

### C2. Next morning

```
python scripts\diagnostics\ohlcv_placeholder_audit.py --json Data\ohlcv_audit_post_deploy.json
```

Compare with `Data\ohlcv_audit_pre_deploy.json` (A2):
- weekend-dated placeholders ≈ 0
- trailing placeholders per symbol ≤ today (+ weekend)
- `stale_stops.after` falling night over night
- ATR ratio (raw series / real bars) moving toward 1.00. Worst names on 10-05:
  ZVIA AZIO KTOS ENPH EHTH SEDG SOFI LVS ADBE BLDR.

### C3. After the next 07:00 game run

```
rem #155's loss cap trimmed a buy (appears only when the cap binds)
findstr /L /C:"Risk-capping" Data\logs\aether.log
```

New positions' stops in `ai_portfolio_game.json` sit wider than before (the honest ATR).
Queued buys and pyramiding are **not** capped yet (they live in `run_daily_ai_management`,
owned by the scenario refactor).

## D. Rollback

- Code: as in `2026-10-06-prod-finalize.md` step 8 (`git reset --hard <rollback point>`,
  restore `config.json`, `python server.py restart`).
- Pruned bars: copy `Data\Backup\<timestamp>_weekend_placeholders\Symbol_full\*` back over
  `Data\Symbol_full\`.
- Only the loss cap: `"system": {"max_trade_risk_pct": 1.0}`.
- A raised task time limit can stay. It's harmless with the old code.

## E. Known follow-ups (not PROD actions)

- `daily_task`'s other steps still use the 600 s default: `run_history.py 5` was killed on 09-30.
- Per-trade loss cap for queued buys + pyramiding (refactor session).

## F. Dev machine (not PROD): caches lost 2026-10-06

The dev box's `Data\Symbol` and `Data\Symbol_full` are **empty**. A worktree removal followed
junctions into them (551,974 files, see #163). PROD is the only known source for the Chaikin
`Symbol` history. To restore, copy PROD's `Data\Symbol` and `Data\Symbol_full` to the dev
machine's `D:\Develop\AnalyzeFinData\Data\` (USB, or the backup share once it's reachable
again). **Copy, never link.** Doing it after C2 gives the dev box the repaired bars.
