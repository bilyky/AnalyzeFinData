# PROD: scheduled jobs without the Gemini CLI (2026-11-16)

**Why:** every Gemini CLI agent task failed with HTTP 403 ("prohibited by organization's policy") on PROD.
AETHER_RD_Scientist failed every Saturday from at least 09-19 to 11-14, so no pattern-discovery report was written after
the 08-18 replay (`Data\pattern_discovery_2026-08-18.json` is still the newest).

**What changes:** the work runs in Python, and the AI part goes through our own AI layer (`config.json` → `ai`
providers and tiers, the same Groq / Gemini API setup the rest of AETHER uses). When no provider can answer, the AI step is
**skipped and reported** in the job's email; the computed output is kept.

| Old task (Gemini CLI, retired) | New |
|---|---|
| AETHER_RD_Scientist (Sat 10:00) | **AETHER_PatternDiscovery**, Sat 10:00: replays today − 25 days, writes `Data\pattern_discovery_<date>.json` and `..._ai.md`, emails the findings |
| AETHER_PostMarketReporter (14:00) | **AETHER_StatusReport**, 14:00 on NYSE sessions: the `status` skill's checks plus an AI-written report, emailed |
| AETHER_DailyDriver (07:05) | none: it only ran `autonomous_pipeline.py`, which AETHER_Morning already runs at 05:30 |
| AETHER_PostMarketSync (13:30) | none: its skill file doesn't exist |

**New emails:** one R&D email each Saturday, and one status email each trading day at 14:00.

**Failure-DNA rules are only proposed.** `pattern_discovery.py` runs `retrospective_analyzer.py` in Phase 7. Before this PR that run set the live `Data\failure_dna_rules.json` (which buys the game rejects); now it runs `--report-only`: the rules go to `Data\failure_dna_rules_proposed.json`, and the R&D email and `Data\retrospective_report.txt` say how they differ from the live rules. To adopt them, run `venv_new\Scripts\python.exe retrospective_analyzer.py` by hand. The game also matches the analyzer's z-score rule name now (`greater_equal_2.5`); PROD's live rules have no z-score rule today, so nothing changes until one is set.

## Steps
1. **Pull.** No config change is needed if the `strong` and `cheap` tiers in `config.json` have a working provider (they do
   today: Groq first).
2. **Re-register the tasks, dry run first:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -DryRun
   ```
   Check that `AETHER_PatternDiscovery` and `AETHER_StatusReport` are built and the four retired tasks print
   `"action":"disable"`. Then run it without `-DryRun`.
3. **Catch up the missing R&D report once,** outside market hours:
   ```powershell
   venv_new\Scripts\python.exe scripts\backtesting\rd_pattern_discovery.py --date 2026-10-22
   ```
   An email arrives with the findings, or with "AI analysis skipped" and the reason. Exit code 1 means the computation
   itself failed (the email shows the error). The live failure-DNA rules are not changed by this run; check that
   `Data\failure_dna_rules.json` has the same timestamp afterwards.

## After deploy, check
- Saturday 10:00+: `Data\logs\agent_runs\pattern_discovery_task.log` and the R&D email.
- The next trading day after 14:00: `status_report_task.log` and the status email.
- An `AI step ... skipped` line in `aether.log` means no provider answered. Check the `ai` providers in `config.json`.

## Rollback
`git checkout <previous commit>`, then re-register. The old registrar doesn't know the two new tasks, so disable them by hand:
```powershell
foreach ($t in "AETHER_PatternDiscovery","AETHER_StatusReport") { schtasks /Change /TN "\AETHER_Agents\$t" /DISABLE }
```
