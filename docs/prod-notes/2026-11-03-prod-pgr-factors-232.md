# PGR factors fix (#232): what you need to do

Since 2026-09-01 every cached Chaikin stock snapshot (`Data/Symbol/<SYM>/<SYM>_<date>.json`)
has empty Financials / Earnings / Technicals / Experts lists: the 20 PGR factors were
dropped by the `/api/suggestions` adapter. The overall PGR rating and the checklist were
fine, so live trading used the right ratings. What was lost is the factor history that the
factor studies and `backtest.py` read.

The missed sessions (2026-09-01 to the deploy day) **can't be refilled**: the API only
serves today's values. Copying older values forward would put fake data in the backtests, so
the script marks the gap instead.

**Live trading changes after this deploy.** The same adapter bug dropped the checklist's
`strengthCount`/`timingCount`. The R&D #32 guard reads them (default 1), so since 2026-09-01
its -1.5 score penalty for "strength 0 and timing 0" never fired. After the deploy it fires
again: buy candidates with 0/0 (INTC and NVDA on 2026-11-03) get -1.5, so expect a few
names to drop out of the buy list compared with the last two months. This restores the
pre-September behaviour; it isn't a new rule.

## 1. Before deploying: see the gap (read-only)

```powershell
venv_new\Scripts\python.exe scripts\utils\pgr_factor_repair.py --since 2026-08-25
```

Expect every session from 2026-09-01 on flagged `GAP` (factors for 0 stocks). On dev the
sessions to 2026-08-31 show factors for every stock.

## 2. Deploy

Merge #232, then pull on PROD. No config change. No backup needed for the code itself.

**When:** after that day's evening run. If you deploy mid-day, the next screen can read
today's snapshots that the old code already cached (without factors) and log one
`[Chaikin factors] … (most of the batch)` ERROR, so the watchdog emails once. It clears
the next day.

New folder: `Data\Symbol_raw\` gets the untouched Chaikin response for each stock, gzipped
(~5 KB each, ~3 MB a day, ~750 MB a year). Check the disk has room. If `AETHER_CACHE_DIR`
is set on PROD, it goes there with `Symbol\`.

## 3. After the next evening run: confirm it's fixed

```powershell
venv_new\Scripts\python.exe scripts\utils\pgr_factor_repair.py --check
```

`CHECK OK: <date> N/N` = the new snapshots have factors. If it still says `CHECK FAILED`,
look for `no PGR factors` in the log (`_check_schema` now warns per stock).

If you deploy **after** that day's evening run, today's snapshots were written by the old
code. Re-fetch them after the close (dry run first; it backs up to `Data\Backup\pgr_refetch_<time>\`):

```powershell
venv_new\Scripts\python.exe scripts\utils\pgr_factor_repair.py --refetch-today
venv_new\Scripts\python.exe scripts\utils\pgr_factor_repair.py --refetch-today --apply
```

## 4. Mark the gap for the studies

```powershell
venv_new\Scripts\python.exe scripts\utils\pgr_factor_repair.py --since 2026-08-25 --mark
```

Writes `Data\pgr_factor_gap.json` (the gap sessions). Factor studies should skip those dates.

## 5. How a repeat would be caught

- Each screen run now logs one `[Chaikin factors]` line after the fetch. When most stocks
  come back without factors it's an **ERROR**, and the watchdog emails ERRORs. No new task.
- #235 (data completeness) also flags it in the evening email.
- `--check` exits 1 when the latest session's coverage is under 90%, for a manual look. It's
  not scheduled; ask me before turning it into a task.

## Roll back

Revert #232. The snapshots written after the deploy keep their factors. Old code reads them
the same way.
