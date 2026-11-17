# PROD review 2026-11-17: runbook for the next pull

Snapshot: logs up to 2026-11-17 07:15 PT, state files copied 08:50.

## What PROD runs now
PROD pulled at 2026-11-16 19:44:53 (dashboard "Pulling latest code"). That pull brought main up to #268
(merged 19:41:42). #268 is live: from 21:01:24 the log says "E*TRADE production token saved to the shared store".

Merged since that pull: **#264** (reversal-filter study; scripts and docs only, no live change).
Open and meant for this pull: **#274** (scheduled jobs without the Gemini CLI), **#277** (data-completeness CLI
checks the screen list), and this PR.

## What this PR fixes
1. **Stop-monitor alert emails every other run.** One symbol is on the Short_Long sheet twice, with two
   different stops, and its price sits between them. The breach check used the higher stop (breach) and the
   recovery check the lower one (recovered), so the name was dropped and re-sent as a new breach on alternate
   runs. It was in all 8 alert emails on Saturday 11-14, 7 of 8 on Sunday 11-15, and 8 of 12 on Monday 11-16.
   The monitor now keeps one stop per symbol (the highest) and logs the duplicates once per run.
   Replay on the 11-17 workbook and state, 4 runs: old code `[0, 1, 0, 1]` emails, new code `[0, 0, 0, 0]`.
2. **`prod_snapshot.py` crashed** on an emoji in a log line when the console is cp1252. Now a character the
   console can't show prints as `?`.

## Steps
1. **Before the pull:** no job running (lock files, `tasklist` for python). Back up `Data\` as usual.
2. **Pull** main, after #274, #277 and this PR are merged. The dashboard restarts itself when it pulls.
3. **Re-register the tasks** (needed for #274, and finishes #259's retirements):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\utils\register_agent_tasks.ps1 -DryRun
   ```
   Check that AETHER_PatternDiscovery and AETHER_StatusReport are built, and that AETHER_RD_Scientist,
   AETHER_DailyDriver, AETHER_PostMarketSync and AETHER_PostMarketReporter print `"action":"disable"`.
   AETHER_RD_Scientist still ran on Sat 11-14 (HTTP 403), so at least that one is still enabled. Then run it
   without `-DryRun`.
4. **Sheet fix (owner):** the new warning line names the duplicated symbol; remove or merge its second row.
   Several hand-set stops on Short_Long are far above the price (from about 2x to over 10x), so they alert as
   breaches all the time. Update them or clear them.
5. **Dev E*TRADE (owner):** dev logged in with the PROD key at 11-16 09:44:28 and 13:12:40, and 11-17 08:27:13.
   PROD's token was rejected at 09:45:05 and 13:13:25 on 11-16 and PROD logged in again each time. The 11-17
   snapshot ends before 08:27, so its effect is not known yet. Do step 5 of
   [2026-11-13-prod-etrade-shared-store.md](2026-11-13-prod-etrade-shared-store.md) on dev (pull, Tailscale up,
   `AETHER_ETRADE_STORE=db`), or don't use E*TRADE on dev until then.
6. **Chaikin (owner):** the session token expires **Sat 11-21 16:57 UTC (08:57 PT)**. The scheduled headless
   renewal fails at Turnstile (AETHER_Chaikin_Reauth exit 1 on 11-13 and 11-14). Renew by hand before then.
7. **Catch up the R&D report once** (from #274's note), outside market hours:
   `venv_new\Scripts\python.exe scripts\backtesting\rd_pattern_discovery.py --date 2026-10-22`

## After the pull, check
- `aether.log`: one "Short_Long lists these symbols more than once" line per monitor run until the sheet is
  fixed, then none. The stop-monitor emails should drop to new breaches and real new lows.
- `python -m aether.data_completeness --date <last session>` names any missing screen symbols (#277).
- Sat 11-21 10:00+: `pattern_discovery_task.log` and the R&D email (#274).

## Rollback
`git checkout <previous commit>`, then re-register (see #274's note for disabling its two new tasks).

## Seen, no action
- 11-16 20:00:03 to 20:01:54: one watchdog run and one preflight saw no `database.url`, Chaikin login or email
  settings (preflight FAIL at 20:01:03). From 20:01:56 everything passed but the watchdog check, and every
  preflight since 21:30 passed. This was during the deploy; no trade ran in that window.
- The R&D #32 waiver bought PCG (target -2.27% under the price) and TDC (-1.27%) on 11-16, and CLFD (-0.85%) on
  11-12. 13 of the 17 buys since 10-22 came through it. R&D #75 (2026-11-04) studied this: INCONCLUSIVE, keep
  the waiver; better targets (R&D #73) are the path.
- Game: 12 positions after the 11-17 EIX buy, cash 11.9%, open risk to stops about 8.0% of equity. The profile
  was AGGRESSIVE (SPY L60 above 2), whose cash buffer is 0%. PCG and EIX are both electric utilities; the sector
  cap is still an open owner decision.
- 18 symbols get no market data from Chaikin (ROKU since 11-11). Prune the Research sheet when you check them.
- Gemini returned 503 to the exit evaluator; it fell back to Groq each time.
- `[Option Write] Research sheet has no 'Symbol' header`: column D's header cell is blank; the fallback
  (column D) is the right column.
