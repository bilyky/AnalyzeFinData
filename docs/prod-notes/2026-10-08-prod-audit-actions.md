# PROD direction (updated 2026-10-20)

One ordered plan. Older versions: `2026-10-08-prod-audit-actions.ARCHIVE-2026-10-13.md` and
`…ARCHIVE-2026-10-14.md`.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`. PowerShell 5.1.
"Elevated" = an Administrator PowerShell.

---

## Where things stand (PROD logs 10-13 08:00 to 10-20 07:52)

**Working**
- Morning pipeline completed every day 10-14 to 10-20 (no `ABORT:`).
- Evening `daily_task.py` completed every day 10-13 to 10-19.
- Trade run at 07:00 on every weekday (10-14, 15, 16, 19, 20).
- Looks deployed around 10-15 (dashboard restarted 10-15 06:36; from 10-16 the Data folder is synced
  once a day instead of 2-5 times). Please confirm with `git log -1 --oneline` on PROD.
- Chaikin: the token expired 10-19 evening; your CDP-attach renewal at 21:37 worked (runway 6.6 days on 10-20).

**Broken**
| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | AI calls fail: the OpenRouter fallback model was removed, and the primary now returns 404 | 404 on every AI call on 10-20 (29 evaluations, email reasoning, intel extraction -> 0 ideas); fallback 404s since 10-14 | **Step 1** (config only) |
| 2 | Three AI-agent tasks fail every day: DailyDriver, PostMarketSync, PostMarketReporter (also RD_Scientist on Saturday) | Gemini CLI: HTTP 403 "Request is prohibited by organization's policy" (VPC Service Controls), every day 10-09 to 10-20 | **Step 4**: your decision |
| 3 | Backup still runs at 15:00, not 22:10 | `data_backup_agent.log`: 10-16 to 10-19 at 15:00 | **Step 3** (re-register tasks) |
| 4 | Pyramid add undid the 1% loss cap | COCO 10-14: cap cut the buy from 26 to 14 shares; 10-19 a pyramid add of 12 took it back to 26 with the stop below cost | **#197** (merged `be76576`; needs deploy) |
| 5 | Covered calls on less than 100 shares | GNE 93, MNST 25 and 14, MSCI 1 share (10-15 to 10-20) | **#196** (merged `ca922c6`; needs deploy) |
| 6 | Second morning pipeline at 05:30 | Overlap Guard stops it every day | **Step 5** (task dump) |

**To check**
- E*TRADE "Trusted Session Expiring" email: noise on the TOTP login; PR #205 stops it (step 6).
- Stop monitor (real account): 11 active stop breaches out of 38 monitored positions on 10-20 07:15.

---

## Do next, in this order

### 1. Switch the AI models (any time; config only)
Tested 10-20 with the PROD key: `nvidia/nemotron-3-ultra-550b-a55b:free` and `poolside/laguna-s-2.1:free`
answer (HTTP 200); the current primary returns 404 and the current fallback is no longer listed.
```powershell
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_models_2026_10_20.py            # dry run
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_models_2026_10_20.py --apply    # backs up, then writes
```
It changes only the two model names; the key is kept and never printed.

### 2. Merge, then deploy (quiet window ~18:00-20:30)
**#199** (`da3dc95`), **#196** (`ca922c6`) and **#197** (`be76576`) are MERGED. Deploy:
```powershell
cd C:\Develop\StockTrading\AnalyzeFinData
git log -1 --oneline
git fetch origin; git log --oneline HEAD..origin/main
Get-ChildItem Data\*.lock -ErrorAction SilentlyContinue
$ts = Get-Date -Format yyyyMMdd_HHmmss; $b = "Data\Backup\${ts}_predeploy"; New-Item -ItemType Directory $b | Out-Null
Copy-Item Data\ai_portfolio_game.json, Data\state_of_the_day.xlsx, Data\config.json $b
venv_new\Scripts\python.exe server.py upgrade
```

### 3. Re-register the tasks (elevated, right after the deploy)
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -Force
(Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_Data_Backup").Triggers | Select-Object StartBoundary
```
Expect `AETHER_Data_Backup` at 22:10.

### 4. Decide on the AI-agent tasks
The Gemini CLI is blocked by an organization policy, so these tasks fail every day and add a
"failed" line to every watchdog report. Options:
- Disable them until there is a CLI that works on this machine:
  `Disable-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName AETHER_DailyDriver` (same for
  `AETHER_PostMarketSync`, `AETHER_PostMarketReporter`, `AETHER_RD_Scientist`).
- Or point `run_agent.cmd` at another CLI. Tell me which, and I'll make it a manifest change.

### 5. Find the second 05:30 launcher (read-only)
```powershell
Get-ScheduledTask | ForEach-Object {
  $a = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " | "
  if ($a -match "autonomous_pipeline|AnalyzeFinData|StockTrading|run_agent") {
    [pscustomobject]@{ Path=$_.TaskPath; Name=$_.TaskName; State=$_.State; Action=$a;
      Trigger=(($_.Triggers | ForEach-Object { $_.StartBoundary }) -join ", ") } }
} | Format-List | Out-File docs\prod-notes\tasks_dump.txt -Encoding utf8
```
Send me `docs\prod-notes\tasks_dump.txt`.

### 6. E*TRADE "Trusted Session Expiring" email
Not needed any more. The TOTP login never uses or refreshes `etrade_browser_state.json`, so the email
repeats daily and the command in it can't clear it. **PR #205** (ready to merge, `bb9286a`) stops it when a TOTP secret is set.
Ignore the email until #205 is deployed.

### 7. Check the next day, then send back
```powershell
Select-String Data\logs\aether.log -Pattern "404 Client Error|AI evaluation failed" | Select-Object -Last 3
Select-String Data\logs\aether.log -Pattern "Pipeline completed successfully|ABORT:|Overlap Guard" | Select-Object -Last 3
Get-Content Data\backup_status.json
```
Expect no new 404 lines and one backup at ~22:10 with `"last_result": "ok"`.
Send `Data\logs.zip`, `Data\backup_status.json`, `docs\prod-notes\tasks_dump.txt`.

---

## Open work on my side
- #185 (log noise, AI 429) is far behind main; I'll bring it current when you say.
- PR #204 (MERGED `d829a74`): the 0-ideas warning says why (mailbox error, no emails, or every AI
  extraction failed). Log text only; deploy with the next batch. Do step 1 first, or it will say "every AI extraction failed" each morning.
- Studies before any rule change: pyramid gate, minimum hold before momentum exits, breadth vs the
  "deploy cash" upgrade (plans/behaviour_review_2026-10-14.md).
- Move the backup share path out of `watchdog.py` into a config key.

## Rollback
Per PR: `git revert -m 1 <merge commit>` on a branch, then redeploy. Config: copy back the newest
`Data\Backup\*_config*\config.json` or the step-2 `_predeploy` copy.
