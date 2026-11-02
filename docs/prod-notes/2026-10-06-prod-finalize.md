# PROD finalize — session 2026-09-29 → 2026-10-07 (updated 2026-10-07)

Everything below runs **on the PROD machine**, in `<prod checkout>`.
**When:** only while nothing is running. Busy per PROD's log: 05:00–07:59 (morning pipeline + 07:00
game), 13:00–17:59 (post-market). Since #160 the 17:00 report task ends with the OHLCV recovery pass,
which can run until ~19:00–22:00 (worst case ~5 h). So don't pick a clock window — check first:

```
dir Data\pipeline_run.lock Data\rapidapi.lock Data\watchdog_run.lock   # each must be absent ("File Not Found")
tasklist /fi "imagename eq python.exe"                                 # no daily_task / rapidapi / pipeline run
```

Steps 1–6 are the deploy; 7–8 are verification; 9 is optional data cleanup.

**Companion notes — same pull, read together** (written by other sessions, in this folder):

| Note | Adds | Do it |
|---|---|---|
| `2026-10-06-prod-ohlcv-rapidapi.md` | #155 / #160 detail: pre-deploy stop audit (A2), **duplicate 17:00 task check** (A1), weekend-placeholder prune (B4), repair checks (C1–C2) | A1–A2 before step 4 here; B4 after step 5; C after step 7 |
| `2026-10-07-prod-candlestick-146.md` | #146 candlestick sign | **only once #146 is merged** (open on 2026-10-07) |
| `2026-10-07-prod-theme-watch-156.md` | #156 theme watchlists: **secret rotation (section 0)**, SEC pre-check, first scans, adding the 25 symbols | section 0 before step 3 here; the rest after step 5 |
| `2026-10-08-prod-171-172.md` | #171 skill docs (no runtime effect) + #172 stale-SEC labels on the theme watch | **only once #171/#172 are merged** (open on 2026-10-08); verify after step 5 |

Where they overlap, step 4b here = its A1 (its A1 also checks for a duplicate
`AnalyzeFinData_Evening` task — do that check). Its "quiet window ~18:00–20:30" predates #160's
evening repair pass (~17:30 → ~19:00–20:00): use the lock check above instead.

---

## 1. Look before you pull

```
git status --short                      # must be clean, or the pull may fail / clobber
git log -1 --oneline                    # WRITE THIS DOWN — it is your rollback point
git fetch origin
git log --oneline HEAD..origin/main     # exactly what will land
```

PROD's web-server stamp `Data/server_commit.txt` was `4c87d8f` (#128, written 09-27), so
the running server predates everything below. As of 2026-10-07 13:00 the pull brings **32 merged PRs**
since 09-29 (#122 #123 #125 #130–#132 #134–#136 #138–#145 #147–#153 #155 #156 #160 #161 #163–#166 — #156
also carries #157 and #159; re-check with the `git log` above, main may have moved):

| PR | What | Effect on PROD |
|---|---|---|
| **#155** | stop stranded placeholder bars shrinking ATR stops; **new per-trade loss cap** `system.max_trade_risk_pct` (default **0.01**) | **trading change** — every buy is trimmed so a stop-out loses ≤ 1% of equity → smaller positions |
| **#160** | RapidAPI recovery: 400-fetch budget, most-starved-first, stop on quota; runs **last** in the 17:00 `daily_task`, with a timeout covering its budget | data repair finally completes — **needs step 4b** (scheduler limit) |
| #163 | caches reachable via `AETHER_CACHE_DIR`, never via links into `Data/`; prune tool scans for links | dev/tooling safety; no PROD env change needed |
| #136 | exit reason + stop recorded on every live SELL | already live on 10-01 (history shows `Exit: …`) |
| #141 | `aether/ledgers.py` — one path per learning ledger | no behavior change |
| #145 | logs `AI BUY REJECTED (Profile Threshold)` | new log line only |
| #147 | Setup column computed by `risk_utils.setup_ok` (moved verbatim from powergauge) | same values; parity test pins it |
| #144 | E*TRADE DB store is opt-in (DATABASE_URL broke every port call on PROD) | **PROD fix** |
| #125 / #140 | headless scheduled re-auth; probe the renewed prev-day token | re-auth path |
| #132 / #139 | E*TRADE reauth-state port wiring; retire `token_renewer.single_flight` | re-auth plumbing |
| #148 / #161 | test suite stops writing prod logs, deleting real watchdog/pipeline locks + trash, and reading the real config | tests only — makes step 6 safer |
| #122 #123 #130 #134 #138 #142 #150 | scenario-stage refactor + guards / test trims | not wired into the live game |
| #131 #143 | review skill docs; prune tool merged-PR detection | tooling only |
| #135 #147 #149 #151 #153 | studies / docs / diagnostics | no runtime effect |
| #164 #165 #166 | agent docs: skills update, R&D roadmap out of `CLAUDE.md`, `GEMINI.md` → pointer at `AGENT.md` | no runtime effect; changes what agents read on that machine |
| #156 (+#157 #159) | theme watchlists (AI buildout, robot vision) + signal study (R&D #51: MARGINAL, watch-only); new web tab + `/api/ai_buildout` | the tab is empty until `python scripts\monitoring\ai_buildout_watch.py [--theme robot_vision]` is run by hand (it calls SEC EDGAR, cached 20 h; contact = `email_sender.address`); nothing schedules it, no scoring change |
| #152 | OceanView context-pack assembler (`aether/oceanview_context.py`) | new module, not called by the live pipeline |

## 2. Back up (Mandatory Backup Policy)

```
rem Type the real date/time (no spaces) — %date% formats are locale-dependent.
set TS=2026-10-06_1900
mkdir Data\Backup\%TS%
copy Data\ai_portfolio_game.json   Data\Backup\%TS%\
copy Data\state_of_the_day.xlsx    Data\Backup\%TS%\
copy Data\ops_and_rd_tracker.xlsx  Data\Backup\%TS%\
copy config.json                   Data\Backup\%TS%\
copy Data\decision_log.jsonl       Data\Backup\%TS%\
copy Data\trade_history_dna.json   Data\Backup\%TS%\
```

## 3. Install the fixed config

Copy **`Data/config.prod_fixed.json` from the dev machine** to PROD as `<prod checkout>/config.json`
(the project-root file `aether/config.py` loads). It differs from PROD's current config only in:

- `ai.providers.openrouter.endpoint` → `https://openrouter.ai/api/v1/chat/completions`
  (was the misspelled host `openrpouter.ai`; 468 "AI evaluation failed" warnings in the log since 09-24)
- `ai.primary` → `openrouter` (was the retired `github_gpt`), and `ai.providers.github_gpt.enabled` → `false`
- `oracle` block added: `account`, `start_equity: 22978.72`, `target_date: 2026-12-31`
  (absent → the Oracle ran with start equity 0 and no target date)
- `web.secret` set to a new random 64-hex value (empty → sessions died on every restart)
- `etrade.login_startegy` renamed `login_strategy` (no code reads it); stray top-level `primary` removed

**Then change `web.admins[0].pass` yourself** — the old value was printed to a console during the
review and is weak; treat it as exposed.

Left untouched on purpose (machine-specific, or I could not judge): `etrade.proxy` (empty),
`database.url`, Gemini model names, sandbox keys, the OpenRouter key.

> **Added 2026-10-07:** the OpenRouter key in `config.prod_fixed.json`, and the E*TRADE production
> consumer key/secret, were exposed in a 2026-10-05 review. Rotate all three before or right after
> this step. See section 0 of `2026-10-07-prod-theme-watch-156.md`.

## 4. Deploy

```
python server.py upgrade                # git pull + restart (stop/start re-reads config)
```

### 4b. Raise the 17:00 report task's time limit (needed for #160)

#160 moved the OHLCV recovery pass to the end of `daily_task.py` and gives it up to ~5 h, but it did
**not** re-register the scheduled task, which still has the 15-minute `ExecutionTimeLimit` and would
kill the pass. Change only that task (elevated PowerShell; leaves its command and engine untouched):

```
$t = Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_AftermarketReport"
$t.Settings.ExecutionTimeLimit = "PT6H"
Set-ScheduledTask -InputObject $t
(Get-ScheduledTask -TaskPath "\AETHER_Agents\" -TaskName "AETHER_AftermarketReport").Settings.ExecutionTimeLimit   # PT6H
```

(`scripts/utils/register_agent_tasks.ps1 -Force` would also set it, 360 min, but re-registers every
task with the engine/args you pass — only use it if you know PROD's current registration arguments.)
Before changing it, run the companion note's A1 check: if `AnalyzeFinData_Evening` is also enabled, both
tasks run `daily_task.py` at 17:00 — disable one, and raise the limit on the one you keep.

## 5. Check the restart

```
python server.py status                 # Running pid=…
type Data\server_commit.txt             # must equal:
git rev-parse --short HEAD
```

Web logins need one fresh sign-in (new `web.secret`).

## 6. (Optional) run the tests on PROD

Before #148 / #161 the suite wrote prod logs and could delete real watchdog/pipeline locks — only
run it after the pull. On main at `2339677` (10-06, before #161) it wrote **no files** into `Data/`
(it still creates the empty folders `Data/etrade_chrome_profile/` and `Data/logs/`). Only `Data/` was
checked, not every side effect — skip it if in doubt.

```
python -m unittest discover tests       # expect Ran ~1117 … OK (skipped=6)
```

## 7. After the next 07:00 game run

```
findstr /C:"Profile Threshold" Data\logs\aether.log      # new #145 line (only if a candidate hit the score floor)
findstr /C:"openrpouter" Data\logs\aether.log            # must find nothing dated after the deploy
findstr /C:"AI evaluation failed" Data\logs\aether.log   # should drop sharply after the deploy
```

After the next 17:00 report task (#160): its log shows the recovery-pass queue/budget line and a run
summary, and **no** `Command timed out after 600s`.

New buys after #155 are smaller: each one's `qty × (price − stop)` is at most ~1% of equity.

Also open the newest SELL rows in `Data/ai_portfolio_game.json` history: each should carry
`details: "Exit: …"` and a `stop_loss` (live and queued sells).

## 8. Rollback (if anything is wrong)

```
git reset --hard <rollback point from step 1>
copy Data\Backup\<TS>\config.json config.json
python server.py restart
```

---

## 9. (Optional) purge test fixtures from the learning ledgers

Test runs wrote fixture records into PROD's learning files before #136/#141 fixed the harness.
They skew the decision scorecard and the retrospective analyzer. Measured on the copies
taken 2026-10-01:

- `Data/decision_log.jsonl`: **3,355 of 5,000** lines are fixture symbols (`P1`–`P6`, `TSCO`, …),
  i.e. symbols the game never held. (The local copy was dated 09-25 — re-measure on PROD.)
- `Data/trade_history_dna.json`: **45 of 52** `CIRCUIT_BREAKER_TRIGGER` records carry the test
  position `TSCO cost 100.0 / stop 80.0`. The other 7 were not proven to be fixtures — leave them.

Do step 2's backup first, copy `docs/prod-notes/purge_test_fixtures.py` from the dev machine into PROD's
`docs\prod-notes\`, then:

```
python docs\prod-notes\purge_test_fixtures.py            # DRY RUN — prints counts + dropped symbols, writes nothing
python docs\prod-notes\purge_test_fixtures.py --apply    # only after the dry run lists just P1–P6 / TSCO
```

Dry run against the 10-01 PROD copies (verified 10-06, files unchanged afterwards):
`decision_log.jsonl: 5000 lines -> keep 1645, drop 3355 (symbols never held: P1, P2, P3, P4, P5, P6, TSCO)`
and `trade_history_dna.json: 102 records -> keep 57, drop 45 fixture breaker records`.

Caveat: "never held" is the fixture test for the decision log because it only records exit
decisions for held positions. A symbol the game held only in a history the PROD file no longer
has would be dropped too — the dry run prints every dropped symbol; apply only if it is just the fixtures.
Then re-run the analyzer so its rules come from real trades: `python retrospective_analyzer.py`
(rules file last written 09-14).

---

## Open items carried forward (not PROD actions)

- **R&D #46 next step:** a stop-out / drawdown reliability study of the Setup gate before relaxing
  it. The returns study says TOO_STRICT under the fixed HAC rule; the live gate is unchanged.
- **Dead rule:** the analyzer writes `z_score greater_equal_2.5`; `check_failure_rules` only matches
  `greater_than_2.5`. Low impact (the bubble guard covers it); one-line code fix owed.
- **Dev-machine cleanup:** empty leftover folders `_wt_dep_check`, `_wt_hermetic_check`, and test
  artifacts under `_wt_exit_reason/Data/` (delete by hand).
- Re-attribute losing sells (stop vs momentum) once a few weeks of #136 exit reasons accrue.
