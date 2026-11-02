# PROD direction (updated 2026-10-14)

One ordered plan. The full history (steps 1-13 from 10-08 to 10-12) is in
`2026-10-08-prod-audit-actions.ARCHIVE-2026-10-13.md`.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`. PowerShell 5.1.
"Elevated" = an Administrator PowerShell.

---

## Where things stand (from the 10-13 PROD logs)

**Working now**
- Watchdog: finishes every hourly cycle; "All systems nominal" every hour from 10-12 21:07 to 10-13 07:00.
  Recovery emails say why they were sent.
- Tasks: re-registered with the cmd.exe launcher (agent logs are plain text again); `AETHER_AI_Summary`
  registered; the five old root tasks (`\AnalyzeFinData_*`) are gone (last seen 10-12 20:08).
- Backups: succeeded 10-12 21:10, 21:12 and 10-13 06:25; `backup_status.json` = `"last_result": "ok"`.
- One trade run at 07:00 (no duplicate). The workbook is intact (checked after the 10-12 crash).

**Still broken (fixes are in open PRs)**
| Problem | Fix |
|---|---|
| 10-13 morning pipeline aborted: `main.py` stopped at its 600 s cap (478/549 symbols, last phase) | **#194** + **#193** (merged; needs deploy) |
| `rapidapi.py` crashes (`KeyError: 'deferred'`) when another recovery pass holds the lock | **#194** (merged; needs deploy) |
| Data is synced by seven scripts (twice every morning, up to four times every evening) | **#193** (merged; needs deploy + step 3): one backup a day at 22:10 |
| The AI second opinion can cancel a stop-loss exit | **#184** (merged; needs deploy) |
| Repeated log warnings; AI 429 bursts from the 05:30 email batch | **#185** (I'll bring it up to date with main first) |
| Two morning pipelines still start at 05:30 (the second stops on the Overlap Guard) | Unknown launcher, outside the AETHER/AnalyzeFinData names: step 4 |

---

## Do next, in this order

### 1. Merge
**#193 (`20467b4`), #194 (`fb4fabd`) and #184 (`f2e7884`) are MERGED.** #195 (other session) goes
next once it is up to date with main. Hold **#185** until I post that it is up to date with main.

### 2. Deploy (quiet window ~18:00-20:30; check no pipeline is running)
```powershell
cd C:\Develop\StockTrading\AnalyzeFinData
git log -1 --oneline                                     # what PROD runs now
git fetch origin; git log --oneline HEAD..origin/main    # what will ship
Get-ChildItem Data\*.lock -ErrorAction SilentlyContinue  # expect none (or only an old, dead one)
$ts = Get-Date -Format yyyyMMdd_HHmmss; $b = "Data\Backup\${ts}_predeploy"; New-Item -ItemType Directory $b | Out-Null
Copy-Item Data\ai_portfolio_game.json, Data\state_of_the_day.xlsx, Data\config.json $b
venv_new\Scripts\python.exe server.py upgrade
```

### 3. Re-register the tasks (elevated, right after the deploy)
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -Force
Get-ScheduledTask -TaskPath "\AETHER_Agents\" | Select-Object TaskName, State
(Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_Data_Backup").Triggers | Select-Object StartBoundary
```
Expect 14 tasks and `AETHER_Data_Backup` at 22:10. The task list lives in
`scripts\utils\scheduled_tasks.json` (also shown on the dashboard's System page).

### 4. Find the second 05:30 launcher (read-only)
Lists every scheduled task, of any name, whose action mentions the project:
```powershell
Get-ScheduledTask | ForEach-Object {
  $a = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " | "
  if ($a -match "autonomous_pipeline|AnalyzeFinData|StockTrading|run_agent") {
    [pscustomobject]@{ Path=$_.TaskPath; Name=$_.TaskName; State=$_.State; Action=$a;
      Trigger=(($_.Triggers | ForEach-Object { $_.StartBoundary }) -join ", ") } }
} | Format-List | Out-File docs\prod-notes\tasks_dump.txt -Encoding utf8
```
Send me `docs\prod-notes\tasks_dump.txt`. Do not delete anything from it yet.

### 5. Check the next day
```powershell
Select-String Data\logs\aether.log -Pattern "Pipeline completed successfully|ABORT:|Overlap Guard" | Select-Object -Last 5
Select-String Data\logs\agent_runs\data_backup_agent.log -Pattern "successfully synchronized|waiting|backing up anyway|did not complete" | Select-Object -Last 5
Get-Content Data\backup_status.json
Select-String Data\logs\aether.log -Pattern "STEP FAILURE|Automation finished with failures|Backup Stale|Recovery Report emailed" | Select-Object -Last 5
```
Expect: the morning pipeline completes (no `ABORT:`), one backup at ~22:10 with
`"last_result": "ok"`, no STEP FAILURE. The Overlap Guard line goes away once step 4 is resolved.

### 6. Send back
`Data\logs.zip`, `Data\backup_status.json`, `docs\prod-notes\tasks_dump.txt`.

---

## Please confirm (not visible in the logs)
- The AI config from #175: in `Data\config.json`, `ai.providers.openrouter.model` is
  `nvidia/nemotron-3-super-120b-a12b:free` with `extra_body.reasoning.enabled=false`
  (`docs\prod-notes\patch_ai_config_175.py` sets it; dry run first).
- `web.secret` is set and the web admin password was rotated.
- `system.max_trade_risk_pct` is `0.01` (or absent, which defaults to 0.01).

## Open work on my side (no PROD action yet)
- Bring #185 up to date with main (40 commits behind on 10-14).
- Move the backup share path out of `watchdog.py` into a config key.
- Free AI tier limits (about 50 requests/day reported): batch or cap the stop-monitor calls.
- OHLCV: 524 provisional bars a day vs a 400-per-run repair budget; needs your choice of a daily bar source.
- Studies before any rule change: profile flip-flop, momentum exits, Bearish-PGR waiver, churn.

## Rollback
Per PR: `git revert -m 1 <merge commit>` on a branch, then redeploy. Config: copy back the newest
`Data\Backup\*_config*\config.json` or the step-2 `_predeploy` copy.
