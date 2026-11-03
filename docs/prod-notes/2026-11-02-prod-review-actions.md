# PROD review 2026-11-02 — actions

Source: PROD logs 10-20..11-02 (`Data/logs/aether.jsonl*`, `Data/logs/agent_runs/*`), `ai_portfolio_game.json`,
`decision_log.jsonl`. Code fixes are in PR #226 (branch `fix/prod-review-2026-11-02`).

Back up first (CLAUDE.md backup rule):

```powershell
$ts = Get-Date -Format yyyyMMdd_HHmmss; $b = "Data\Backup\${ts}_prod_review_1102"; New-Item -ItemType Directory $b | Out-Null
Copy-Item Data\config.json, Data\ai_portfolio_game.json, Data\decision_log.jsonl $b
```

## 0. URGENT: every scheduled task runs an hour early since DST ended (11-01)

Task Scheduler holds the trigger start times in UTC (`New-ScheduledTaskTrigger` wrote "...Z"), so after
the clocks went back the 07:00 trade run fires at 06:00 PT = 09:00 ET and aborts ("Market is Closed").
The 05:30 morning pipeline ran at 04:30, the 17:00 evening report at 16:00. Until this is fixed, run the
trade step by hand after 06:30 PT, as you did today.

Fix: merge #226, deploy, then re-register the tasks from an **elevated** PowerShell in the repo root:

```powershell
Get-ScheduledTask -TaskPath '\AETHER_Agents\' | ForEach-Object { "{0,-28} {1}" -f $_.TaskName, $_.Triggers[0].StartBoundary }   # before: ends in Z
.\scripts\utils\register_agent_tasks.ps1 -DryRun     # every "start" is local time, e.g. 2026-11-02T07:00:00
.\scripts\utils\register_agent_tasks.ps1 -Force
Get-ScheduledTask -TaskPath '\AETHER_Agents\' | ForEach-Object { "{0,-28} {1}" -f $_.TaskName, $_.Triggers[0].StartBoundary }   # after: no Z
```

`-Force` re-registers the agent tasks with the default `-Engine gemini`, as they are today (see step 2).

## 1. AI is down: every OpenRouter call 404s (since at least 10-20)

Both free OpenRouter models are gone. Zero AI exit verdicts since 10-20; the email-intel extraction fails
every morning. The game falls back to the rules, so trading is unaffected.

`docs/prod-notes/patch_ai_models_2026_10_20.py` is superseded: #192 (AI tiers) is merged, and its decision was
to drop OpenRouter's free models. Follow `docs/prod-notes/2026-10-12-ai-tiers-192-enable.md`, steps 1-6:
deploy, get a Groq key and a Gemini key (personal Google account), put both in `.env`, then

```powershell
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg>                       # dry run
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg> --apply
venv_new\Scripts\python.exe docs\prod-notes\patch_ai_tiers_192.py --config <cfg> --enable groq --enable gemini_lite --enable gemini_oai --disable openrouter --disable openrouter_fb --apply
```

Check the Groq model id (`openai/gpt-oss-120b`) on console.groq.com first; it was not checked today.
Next day: `aether.ai_client` warnings should stop, and `decision_log.jsonl` should get verdicts again.

## 2. Three Gemini-run agent tasks fail every day (your decision)

`AETHER_DailyDriver`, `AETHER_PostMarketSync`, `AETHER_PostMarketReporter` (40 of the 54 failed-task
alerts). The Gemini CLI gets HTTP 403 "Request is prohibited by organization's policy" (VPC Service
Controls) and 429s. They are qualitative AI-agent runs; the data refresh itself is done by the
`AETHER_AftermarketReport` (daily_task.py) Python task. Either disable them:

```powershell
foreach ($t in "AETHER_DailyDriver","AETHER_PostMarketSync","AETHER_PostMarketReporter") { schtasks /Change /TN "\AETHER_Agents\$t" /DISABLE }
```

or point `run_agent.cmd` at a CLI agent the account may use. Until then the watchdog keeps alerting.

## 3. Deploy the PR, then purge the test rows from the decision log

```powershell
venv_new\Scripts\python.exe scripts\utils\purge_decision_log_fixtures.py            # dry run
venv_new\Scripts\python.exe scripts\utils\purge_decision_log_fixtures.py --apply    # backs up to Data\Backup first
```

On the 11-02 copy the dry run finds 1,279 ULTA fixture rows and nothing else.

## 4. After the deploy, check the logs

- No more "Circuit Breaker active" floods: at most one warning per 15-minute window.
- `[PRICING] Game position ...` warnings: once per symbol, kind and day (was ~6 a minute).
- `[RapidAPI]` repairs the held positions first. Check that a held name's flat bar is gone after one
  evening pass; if it isn't, the fetch itself failed (look for its error in the run summary).
- The evening `run_history.py` / `main.py` no longer abort on one symbol's network error (10-22) and stop
  at once when Chaikin has no session (10-27 timeouts).

## 5. PR #185 is folded into #226

Its commit was cherry-picked onto today's main (it was 246 commits behind and conflicting). Close #185
after #226 merges.

## Not actioned (info)

- StopMonitor exit 267014 (0x41306 = terminated) once on 10-21.
- Data backup timeout once on 10-22.
- Covered calls on 1-2 share positions on 10-26 came from pre-#196 code; #196 is live (11-02 skips them).
