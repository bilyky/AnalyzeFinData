# PROD — option-chain snapshot task (#198) — 2026-10-20 (updated 2026-10-27)

**Merged 2026-10-27 as `f57f0b3`.** Includes the review fix `ebcaa28` (below).

**What it changes**
- New scheduled task **`AETHER_OptionChainSnapshot`**: daily at **12:35 PT (15:35 ET)**, 15 min
  limit. It runs `scripts/monitoring/option_chain_snapshot.py`, which appends one live E*TRADE
  option chain per symbol to `<Data dir>\option_chains\<SYM>.jsonl`. The Data dir is
  `Data\` unless `AETHER_DATA_DIR` is set, the same folder the game state is read from.
  The symbols are INTC plus every symbol the paper game holds a covered call on.
- It skips ET weekends and NYSE holidays itself (exit 0, nothing written).
- It uses the read-only data client: it never renews a token and never opens a browser. If the
  token is dead it logs an error per symbol and exits 2. The watchdog keeps the token fresh.
- **Fix:** `options_adviser.fetch_chain` always returned spot `None`. `options_adviser.py --live`
  now prices from the live last trade. Before, it fell back to a spot passed on the command line,
  then to the position's stored price.
- If no spot comes back, it now logs `No spot price for <SYM> in the quote response` (a WARNING),
  instead of failing silently.
- **System page:** a ▶ Run button for the task (admin).
- **Registrar:** `register_agent_tasks.ps1 -Only <task>` registers just one task.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`;
`python` = `venv_new\Scripts\python.exe`.

## ⚠️ Order matters

Register the task **right after the pull**. From the pull until it's registered, the hourly
watchdog reports `Missing scheduled tasks … AETHER_OptionChainSnapshot` and sends an email
every run (that path has no once-a-day throttle).

**Do not run the registrar without `-Only`.** A plain run re-registers *every* task, which
replaces them all and re-applies `-Engine` (default `gemini`) to the AI agent tasks.

## 1. Deploy

1. Back up as usual. Write down `git log -1 --oneline`.
2. Pull + restart: `python server.py upgrade`, then `python server.py status`.

## 2. Register the one new task (elevated PowerShell)

```powershell
cd C:\Develop\StockTrading\AnalyzeFinData
.\scripts\utils\register_agent_tasks.ps1 -DryRun -Only AETHER_OptionChainSnapshot
.\scripts\utils\register_agent_tasks.ps1 -Only AETHER_OptionChainSnapshot
Get-ScheduledTask -TaskPath '\AETHER_Agents\' -TaskName AETHER_OptionChainSnapshot | Select-Object TaskName, State, @{n='Next';e={(Get-ScheduledTaskInfo $_).NextRunTime}}
```

- The dry run prints one JSON line. `start` should be today at 12:35 local, with
  `days_interval` 1 and `time_limit` `PT15M`.
- Registering does **not** pass `-Engine`; that only matters for agent tasks, and this is a
  Python task.
- No principal is set (only `interactive` tasks get one), so it gets Task Scheduler's default
  for the account that registers it, the same as the other Python tasks. Register it from the
  same account as those.
- `Get-ScheduledTask` should show `Ready` and a next run at 12:35.

## 3. Verify

Right after registering (optional, makes one live E*TRADE chain request per symbol):

```
python scripts\monitoring\option_chain_snapshot.py --dry-run
python scripts\monitoring\option_chain_snapshot.py
```

- The dry run writes a `source=dry-run-fixture` line to `Data\option_chains\INTC.jsonl`.
  Delete that line or the file afterwards if you want only live data in it.
- The live run prints `Snapshot OK: N live quotes for <SYM> (spot <price>, expiry …)` for INTC
  and each covered-call symbol. **The spot must be a number, not `None`** (that's the fix).
  After the close the quotes are after-hours, which is fine as a plumbing check.

After the first scheduled run (12:35 PT on a trading day):

```
type Data\logs\agent_runs\option_chain_snapshot.log
```

- One `Snapshot OK` line per symbol, no `ERROR`, and no `No spot price` warning.
- `Get-ScheduledTaskInfo` shows `LastTaskResult` 0. The result is 2 if any symbol failed;
  usually that means the token is dead, so check the watchdog/preflight reauth.
- The next watchdog email no longer lists it as missing.

Hermetic tests (optional):

```
python -m unittest tests.test_option_chain_snapshot tests.test_options_adviser tests.test_scheduled_tasks_manifest -v
```

## 4. Rollback

```powershell
Unregister-ScheduledTask -TaskPath '\AETHER_Agents\' -TaskName AETHER_OptionChainSnapshot -Confirm:$false
```

Then `git revert -m 1 f57f0b3` and `python server.py restart`. Unregistering without
the revert makes the watchdog report the task as missing again. `Data\option_chains\` can stay:
nothing reads it yet.

## Dev box (not PROD)

There's no schedule here. Run it by hand, or with the System page's ▶ Run:

```
python scripts\monitoring\option_chain_snapshot.py            # INTC + covered calls
python scripts\monitoring\option_chain_snapshot.py --symbol AMD
python scripts\monitoring\option_chain_snapshot.py --force    # also on a closed day
```

Verified on dev 2026-10-20 14:13 PT (after the close): 24 quotes each for INTC, MNST, MSCI;
spot 91.69 / 47.04 / 591.69.
