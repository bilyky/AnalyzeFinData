# PROD actions after the 2026-10-08 log audit (PR #174, PR #175)

Source: `Data/logs.zip` + PROD JSON copies taken 2026-10-08 10:29. Full findings and the fix plan:
`plans/prod_audit_2026-10-08.md` on the dev machine.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`.
PowerShell 5.1 syntax. "Elevated" = an Administrator PowerShell.

**Status 2026-10-08: #174 (a42b2ab), #175 (8b25e04) and #176 (28d2b81) are MERGED to main.** Main at 28d2b81 has the same tree as the tested #176 head (1354 tests OK).

Do steps 1-4 **now** (no deploy needed). Steps 5-8 come **after** #174 and #175 are merged.

---

## 1. End the hung watchdog (now)

The watchdog has not finished a cycle since 2026-09-22 21:00. Each run that took the lock hung on
`schtasks /create` for `AnalyzeFinData_Morning` and held `Data\watchdog_run.lock` for hours to days.
The last lock seen in the logs belonged to PID 37368 (10-07 22:00); it may have changed.

```powershell
cd C:\Develop\StockTrading\AnalyzeFinData
Get-Date
Get-Item Data\watchdog_run.lock | Select-Object LastWriteTime
$wpid = [int](Get-Content Data\watchdog_run.lock)
Get-CimInstance Win32_Process -Filter "ProcessId=$wpid" | Select-Object ProcessId, CreationDate, CommandLine
# The child is what hung. Please copy this output back to me: it tells us WHY schtasks blocked.
Get-CimInstance Win32_Process -Filter "ParentProcessId=$wpid" | Select-Object ProcessId, Name, CommandLine | Format-List
```

If the process exists and started more than 2 hours ago, kill it and its children:

```powershell
taskkill /F /T /PID $wpid
```

Don't delete the lock by hand: the next hourly run sees the PID is dead and takes the lock over
(current code already does that for a dead PID).

Also check what the watchdog keeps trying to heal. It re-registers `AnalyzeFinData_Morning` on every
run, so something says that task is missing or broken. Copy the output back:

```powershell
schtasks /query /tn "\AnalyzeFinData_Morning" /fo LIST /v | Select-String "TaskName|Status|Last Run|Last Result|Task To Run|Run As"
Get-ScheduledTask | Where-Object TaskName -like "*Morning*" | Select-Object TaskPath, TaskName, State
```

## 2. Back up now (now)

The `AETHER_Data_Backup` task has crashed at import on every run (27 of 27), and the NAS share was
unreachable every day from 09-30 to 10-08. Assume there is **no recent off-machine backup**.

Local copy of the core state first:

```powershell
$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$dst = "Data\Backup\${ts}_audit_manual"
New-Item -ItemType Directory -Force $dst | Out-Null
Copy-Item Data\ai_portfolio_game.json, Data\state_of_the_day.xlsx, Data\config.json, `
          Data\trade_history_dna.json, Data\performance_log.json, Data\decision_log.jsonl $dst -ErrorAction Continue
Get-ChildItem Data\ops_and_rd_tracker.xlsx, ops_and_rd_tracker.xlsx -ErrorAction SilentlyContinue | Copy-Item -Destination $dst
Get-ChildItem $dst
```

Then the NAS:

```powershell
Test-Path "\\<backup-share>\<user>\Develop\StockTrading\AnalyzeFinData\Data"
```

- `False`: the share is down, or this account has lost access (check the NAS is on; try
  `net use \\<backup-share>` and log in). Fix that first.
- `True`: run the sync once by hand. Until #174 is deployed, the task's script can't import, so use
  this line from the repo root:

```powershell
venv_new\Scripts\python.exe -c "import sys, watchdog; sys.exit(0 if watchdog.sync_data_folder() else 1)"; $LASTEXITCODE
```

`0` = success. robocopy can take up to 10 minutes.

## 3. Remove the duplicate Morning task (now, elevated)

Every morning two Morning tasks start, and the Overlap Guard stops the second.
`\AETHER_Agents\AETHER_Morning` is flagged as stray by the watchdog and is **not** registered by
`register_agent_tasks.ps1` (checked on main 2026-10-08). Export it first, then compare:

```powershell
Export-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_Morning" | Out-File "Data\Backup\AETHER_Morning_task_$ts.xml" -Encoding utf8
(Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_Morning").Actions | Format-List Execute, Arguments, WorkingDirectory
(Get-ScheduledTask -TaskName "AnalyzeFinData_Morning").Actions | Format-List Execute, Arguments, WorkingDirectory
```

If both run the same morning pipeline, remove the stray one:

```powershell
Unregister-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_Morning" -Confirm:$false
```

If they run different things, stop and send me both outputs.

## 4. Why nothing traded on Wed 10-07 (now, read-only)

There was no execute_trades run on 10-07, and StopMonitor skipped 06:45-08:15. The logs don't say why.

```powershell
Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-TaskScheduler/Operational'; StartTime=[datetime]'2026-10-07 06:00'; EndTime=[datetime]'2026-10-07 08:45'} -ErrorAction SilentlyContinue |
  Select-Object TimeCreated, Id, Message | Format-List | Out-File docs\prod-notes\taskhist_2026-10-07.txt -Encoding utf8
(Get-Item docs\prod-notes\taskhist_2026-10-07.txt).Length
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]'2026-10-07 05:30'; EndTime=[datetime]'2026-10-07 09:00'; Id=1,12,13,41,42,107,1074,6005,6006,6008} -ErrorAction SilentlyContinue |
  Select-Object TimeCreated, Id, ProviderName | Format-Table -AutoSize
```

A length of 0 means the Task Scheduler history log is off (enable it: Task Scheduler →
"Enable All Tasks History"). The second command shows sleep/wake/reboot events in the window.

---

## 5. Deploy #174 and #175 (after both are merged)

Follow the deploy and rollback steps in `2026-10-06-prod-finalize.md` (look before you pull, back up,
check no pipeline locks are present, `python server.py upgrade`). The other pending notes
(`2026-10-06-prod-ohlcv-rapidapi.md`, `2026-10-07-prod-candlestick-146.md`, `2026-10-07-prod-theme-watch-156.md`,
`2026-10-08-prod-oceanview-170.md`) ship in the same pull.

- **#174** (head `505f940`): the watchdog's schtasks/PowerShell/tasklist calls time out after 60s
  instead of hanging. A lock older than 2h whose PID is alive **and is running `watchdog.py`**
  (checked through its command line) gets that process tree killed and taken over. If the PID now
  belongs to another program (PIDs are reused after a reboot), the lock is taken without killing
  anything. Preflight fails on a lock older than 2h. The backup task script can import again.
  So even if you skip step 1, the first hourly watchdog run after the deploy clears a hung one.
- **#175** (head `71cb124`): lets the AI client use free OpenRouter models (step 6). A sell verdict
  is labeled with the provider that actually answered. Before #175 is deployed, step 6's config
  does not work (the models return empty answers).

Both were tested merged together onto main `c8fe4b0`: `Ran 1303 tests ... OK (skipped=7)`. If main moves again before you merge, re-run that check.
Neither changes trading rules, position sizing or stops.

Before the pull, see everything that will ship (PROD's checked-out commit is not in any log):

```powershell
git fetch origin
git log --oneline HEAD..origin/main
```

Rollback for just these two: `git revert -m 1 a42b2ab` (#174) or `git revert -m 1 8b25e04` (#175) on a branch and
redeploy, or follow the finalize note's rollback. Undo step 6 by copying back the
`Data\Backup\<timestamp>_config_ai175\config.json` it made.

Check after the deploy:

```powershell
git log -1 --oneline
git merge-base --is-ancestor aeddd12 HEAD; "155 included: $($LASTEXITCODE -eq 0)"
git merge-base --is-ancestor a6b43e5 HEAD; "160 included: $($LASTEXITCODE -eq 0)"
```

## 6. AI config (after #175 is deployed)

Easiest: the patch script next to this note. It backs up `config.json` first, keeps the existing
key, never prints it, and changes only the `ai` block. Dry run first, then apply:

```powershell
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_config_175.py
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_config_175.py --apply
```

The dry run's last line must read
`enabled providers after the change (the fallback chain): ['openrouter', 'openrouter_fb']`.
If it lists anything paid (claude, gemini...), stop: after #175 every enabled provider is
tried in turn when the first fails. Tested on copies of both PROD configs I have
(`config.prod_fixed.json` from 10-01, and the older one with the `openrpouter` typo).

By hand instead: back up `Data\config.json` (step 2 did). Then in its `ai` block:

- set `"primary": "openrouter"`
- in `providers.openrouter`: keep the existing key; fix the endpoint if it still says `openrpouter`;
  change `model`; add `extra_body`
- add `providers.openrouter_fb` (same key source as `openrouter`)
- set `providers.github_gpt.enabled` to `false` (retired 2026-07-30)

```json
"openrouter": {
  "enabled": true,
  "type": "openai_compatible",
  "model": "nvidia/nemotron-3-super-120b-a12b:free",
  "endpoint": "https://openrouter.ai/api/v1/chat/completions",
  "api_key_source": "<keep what is there now>",
  "extra_body": {"reasoning": {"enabled": false}}
},
"openrouter_fb": {
  "enabled": true,
  "type": "openai_compatible",
  "model": "apodex/apodex-1.1-mini:free",
  "endpoint": "https://openrouter.ai/api/v1/chat/completions",
  "api_key_source": "<same as openrouter>",
  "extra_body": {"reasoning": {"enabled": false}}
}
```

Test it (one call, about 1-3 seconds):

```powershell
venv_new\Scripts\python.exe -c "import json; json.load(open('Data/config.json', encoding='utf-8')); print('config json OK')"
venv_new\Scripts\python.exe -c "from aether import ai_client; print(ai_client.evaluate('Answer in one short sentence.', 'Is a stop-loss useful? ', max_tokens=150))"
```

Expect one sentence back. An error that mentions `finish_reason=length` means `extra_body` is missing
or #175 isn't deployed.

What was tested (2026-10-08, from the dev machine, PROD's key): both models answered valid JSON 5 out
of 5 times with reasoning off. `poolside/laguna-s-2.1:free` (the current PROD model) and the gemma-4
free routes returned 429. **Limits:** the key reported a free-tier cap of **50 requests a day**. The
intraday monitor makes one call per breached symbol (10-26 per burst), so it can use that up by
mid-morning. Options if it does:
- add about $10 of OpenRouter credit (OpenRouter says this raises the free cap; not verified by me)
- add a free Google AI Studio key (`GEMINI_API_KEY` env var) and enable the `gemini_oai` block from
  `config.json.example` as a second provider.

## 7. While you're in config.json

- `web.secret`: set a long random value so admin sessions survive restarts:
  `[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')`
- Rotate the web admin password (pending since 10-01).
- `system.max_trade_risk_pct` should be `0.01` (the default if absent). Check:
  `(Get-Content Data\config.json -Raw | ConvertFrom-Json).system.max_trade_risk_pct`

## 8. Check after the next runs (the day after the deploy)

```powershell
# Watchdog finishing cycles again: the lock should be young or absent, and reports should resume
if (Test-Path Data\watchdog_run.lock) { (Get-Date) - (Get-Item Data\watchdog_run.lock).LastWriteTime }
Select-String -Path Data\logs\aether.log -Pattern "Recovery Report emailed|Healing .* timed out|treating it as hung" | Select-Object -Last 5
# Backup task
Get-ScheduledTaskInfo -TaskPath "\AETHER_Agents\" -TaskName "AETHER_Data_Backup" | Select-Object LastRunTime, LastTaskResult
# AI calls
Select-String -Path Data\logs\aether.log -Pattern "AI evaluation failed|returned 429; retrying" | Measure-Object | Select-Object Count
# OHLCV placeholder backlog
venv_new\Scripts\python.exe scripts\diagnostics\ohlcv_placeholder_audit.py --json Data\ohlcv_placeholder_audit.json
```

`LastTaskResult` 0 = backup OK. If the log shows `Healing AnalyzeFinData_Morning timed out`, the
heal is still failing (now without hanging). Send me step 1's child-process output and step 1's
schtasks query.

## 8b. PR #176 (daily_task failures + stale-workbook gate), MERGED as 28d2b81 (rollback: `git revert -m 1 28d2b81`)

**Before the deploy**, check which tasks run `daily_task.py`. The watchdog still audits the old
root names (`AnalyzeFinData_*`) and tries to re-create them, so PROD may have both the old and new names:

```powershell
Get-ScheduledTask | Where-Object { ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -match "daily_task.py" } |
  Select-Object TaskPath, TaskName, State
```

- Only `\AETHER_Agents\AETHER_AftermarketReport`: fine, continue.
- `\AnalyzeFinData_Evening` too: daily_task runs twice each evening. With #176, every failed
  evening would also repeat a failed-task line in the hourly watchdog email. **Stop and send me the output**
  (the fix is a follow-up PR that makes the watchdog use the AETHER_Agents names).

Deploy as in step 5. Then, **elevated**, raise the 17:00 report task's limit from 6 h to 7 h:

```powershell
$t = Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_AftermarketReport"
$t.Settings.ExecutionTimeLimit          # expect PT6H now (or PT15M if #160's step was never done)
$t.Settings.ExecutionTimeLimit = "PT7H"
Set-ScheduledTask -InputObject $t | Out-Null
(Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_AftermarketReport").Settings.ExecutionTimeLimit
```

What changes on PROD:
- daily_task gives `run_history.py` and `main.py` up to 30 min each (was 600 s), and the task
  exits 1 when any step fails. The report email then starts with **STEP FAILURE** and lists the step.
  So the AETHER_AftermarketReport `LastTaskResult` can now be 1. That is the point: it was 0 while
  steps were being killed.
- The 07:00 trade run **refuses to trade** when `Data\state_of_the_day.xlsx` was last written before
  the previous session's close (13:00 PT). You get an email
  `ALERT: AI trades skipped - stale workbook`. Fix the refresh (`venv_new\Scripts\python.exe main.py`),
  then rerun by hand: `venv_new\Scripts\python.exe ai_portfolio_game.py --run --force`.

Check the day after:

```powershell
Get-ScheduledTaskInfo -TaskPath "\AETHER_Agents\" -TaskName "AETHER_AftermarketReport" | Select-Object LastRunTime, LastTaskResult
Select-String -Path Data\logs\aether.log -Pattern "Automation finished with failures|Automation completed successfully|stale workbook" | Select-Object -Last 5
```

## 9. Send back to me

- a fresh `Data\logs.zip` (Data\logs\ folder) after step 8
- the outputs marked "copy back" in steps 1 and 3, and `docs\prod-notes\taskhist_2026-10-07.txt`
- `Data\ohlcv_placeholder_audit.json`

## Not fixed yet (next PRs, from the audit plan)

- daily_task: per-step timeouts sized to each job (run_history.py and rapidapi.py are killed at 600s);
  report failure when a step fails (today it logs "Automation completed successfully" after a
  timeout); execute_trades refuses a workbook that isn't from today (09-30 traded on stale data).
- Game simulation: stop exits fill at the stop price even on a gap-down; scale-out and scale-in on
  the same symbol in one run (GNE 10-02); covered call assigned at 07:00 on expiry day (KE 09-25).
- Log/alert noise: "Flat placeholder" warning up to 398x per symbol per day; stop-breach emails 6-11x a day.
- Data: 524 placeholder bars a day vs a 400-per-run RapidAPI repair budget: needs a decision on the
  source of real daily bars.
- The Gemini-CLI agents (DailyDriver, PostMarketSync, PostMarketReporter, PatternDiscovery) get 403
  on every run: retire those tasks or move them to a working provider.

## 10. (2026-10-09) Task-name cleanup: send me the task list first

The watchdog still audits and re-creates the old root tasks (`AnalyzeFinData_*`), while
`register_agent_tasks.ps1` registers `\AETHER_Agents\AETHER_*`. Two pairs run the same job:
`AnalyzeFinData_AI_Game` = `AETHER_ExecuteTrades` (07:00 trade run; on 10-09 the second one
stopped on the state lock) and `AnalyzeFinData_Evening` = `AETHER_AftermarketReport` (17:00
daily_task). Three old tasks have no AETHER_Agents twin yet: Morning 05:30, AI_Summary 18:00,
ETrade_Reauth 05:15. Read-only dump, copy the file back to me:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match "AETHER|AnalyzeFinData" } | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo
  [pscustomobject]@{ Path=$_.TaskPath; Name=$_.TaskName; State=$_.State; LastRun=$i.LastRunTime; LastResult=$i.LastTaskResult;
    Action=(($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join " | ");
    Trigger=(($_.Triggers | ForEach-Object { $_.StartBoundary }) -join ", ") }
} | Format-List | Out-File docs\prod-notes\tasks_2026-10-09.txt -Encoding utf8
```

Safe stopgap until the fix (elevated): **disable, don't delete**, the two duplicates. The
watchdog only re-creates tasks that are missing, so a disabled task stays as it is:

```powershell
Disable-ScheduledTask -TaskPath "\" -TaskName "AnalyzeFinData_AI_Game"
Disable-ScheduledTask -TaskPath "\" -TaskName "AnalyzeFinData_Evening"
```

Do that only if `\AETHER_Agents\AETHER_ExecuteTrades` and `\AETHER_Agents\AETHER_AftermarketReport`
exist and are Ready in the dump.

## 11. (2026-10-09) Backup: send me proof of the manual run

`backup_status.json` was not in the copy and logs.zip ends at 07:15, before the manual run.
Next copy, include `Data\backup_status.json` and the current `Data\logs\aether.log`
(look for `Data folder successfully synchronized`).

## 12. (2026-10-12) Weekend watchdog emails + Monday morning abort: PR #186

What happened (verified in the 10-12 logs):
- Until Sun ~17:00 the old `AnalyzeFinData_Evening` (a second daily_task each evening) exited 1 and
  the watchdog flagged it every hour. After the old tasks were deleted (~17:30 Sun) it reported them
  as missing every hour. Both sent an hourly recovery email.
- Tasks launch Python through PowerShell, which reports exit 1 whenever Python writes a warning.
  At 10-12 05:31 the preflight saw `AETHER_Watchdog last run failed (Exit Code: 1)` and aborted the
  morning pipeline. The 07:00 trade run still ran on the workbook rebuilt Sun 17:20 (no buys or sells).
- The backup DID work: `Data folder successfully synchronized` at 10-11 15:03 (Data_Backup task).
  The daily_task sync at 17:20 still said "drive not connected" (that task's context can't see the share).
- On Sun 10-11 two daily_tasks ran main.py AT THE SAME TIME (17:02-17:20 and 17:02-17:21): two writers on
  state_of_the_day.xlsx. Check the workbook opens cleanly.
- Still two runs at 05:30 (Overlap Guard) and 07:00 (state lock) on 10-12, with the old tasks gone.
  The source is unknown. After #186 the watchdog lists any leftover task in `[Stray-Task]`.

After #186 is merged and deployed, in an **elevated** PowerShell:

```powershell
cd C:\Develop\StockTrading\AnalyzeFinData
# Old agent logs are UTF-16; start fresh plain-text ones (keeps the old files)
Get-ChildItem Data\logs\agent_runs\*.log | ForEach-Object { Rename-Item $_.FullName ($_.Name + ".utf16-old") }
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -Force
Get-ScheduledTask -TaskPath "\AETHER_Agents\" | Select-Object TaskName, State
(Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_Watchdog").Actions | Format-List Execute, Arguments
```

Expect `Execute : cmd.exe` and 14 tasks, including the new AETHER_Morning and AETHER_AI_Summary.
If an AETHER_Morning already existed, `-Force` replaces it with the registrar's version.
Next day: `Select-String -Path Data\logs\aether.log -Pattern "Stray-Task|Missing scheduled task|Recovery Report emailed" | Select-Object -Last 10`
and send me the `[Stray-Task]` line: that names whatever still fires the duplicate 05:30 / 07:00 runs.

**Update (PR #187, stacked on #186):** the task list now lives in `scripts\utils\scheduled_tasks.json`
(one source: registrar, watchdog and the System page all read it). Before registering, preview:
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -DryRun`
(prints each task as JSON, registers nothing). The System page then lists all 14 tasks with their
description and parameters; any task shown as "Not registered" is missing in Task Scheduler.

**Deploy window for #186 (verified in the code 2026-10-12):** run `register_agent_tasks.ps1 -Force`
in the SAME session as `server.py upgrade`, before 21:30. Until it runs:
1. the watchdog lists `AETHER_AI_Summary` (and `AETHER_Morning` if it isn't under `\AETHER_Agents\`) as
   missing and sends a recovery email every hour (each email now says why, in "Findings and actions");
2. the tasks still launch through PowerShell, so a warning still makes the watchdog task exit 1, and
   the 21:30 / 05:30 preflight would abort the morning pipeline again.

## 13. (2026-10-12) One backup a day at 22:10 (PR #193)

Only `AETHER_Data_Backup` syncs Data now (no more 05:50 / 18:00 / evening copies from other scripts).
It runs at 22:10 (off the hour, clear of the watchdog), waits while daily_task / main.py / run_history / rapidapi / the pipeline are still
running (up to 2 h), then copies. It exits 1 if the share is offline. After the deploy, the same
registrar step (`-DryRun`, then `-Force`) moves it to 22:10 with a 150-min limit. First night, check:
`Select-String Data\logs\agent_runs\data_backup_agent.log -Pattern "successfully synchronized|waiting|backing up anyway"`
and `Get-Content Data\backup_status.json` (expect `"last_result": "ok"`).
