# Chaikin chart history backfill (#234): what you need to do

`scripts/utils/chaikin_chart_backfill.py` pulls 5 years of daily Chaikin history for every
symbol into `Data\Symbol_chart\<SYM>_chart.json`: daily and weekly PGR, money flow (CMF),
relative strength, Chaikin trend/cycle/bands, and the close for a cross-check. RapidAPI
doesn't have these; the daily snapshots only go back to 2023-08. Read-only on Chaikin's side,
writes only the new folder. No config change, no scheduled task.

It also keeps the full response, unchanged and gzipped, in `Data\Symbol_chart\raw\`.

Cost: one GET per symbol, 2 s apart by default (`--delay N` to change it): about 25 minutes
for ~617 symbols (estimate). The daily screen already makes ~535 Chaikin calls a day. Disk:
~235 KB + ~69 KB (raw) per symbol, ~190 MB in total.

## 1. Pick a quiet time

Not while the morning or evening pipeline runs (they use the same Chaikin session). Midday
or a weekend is fine.

## 2. Try two symbols first

```powershell
venv_new\Scripts\python.exe scripts\utils\chaikin_chart_backfill.py --symbols INTC AAPL
```

Expect `fetched 2` and `content check: 2 symbols with history, 0 with problems`. On dev
(2026-11-03) both had 1,255 days from 2021-11-02, and closes matched RapidAPI exactly over
the last 60 days.

If it says `STOPPED: auth error`, the Chaikin session has expired. Renew it the usual way,
then re-run.

## 3. Run all symbols

```powershell
venv_new\Scripts\python.exe scripts\utils\chaikin_chart_backfill.py
```

If it stops (auth error, or 3 errors in a row), fix the cause and run the same command
again. Symbols already fetched today are skipped.

## 4. Check the result

The last lines list symbols with problems: no rows, PGR outside 1-7, or a close more than
1% off RapidAPI (median of the last 60 days). The full report is
`Data\Symbol_chart\_report.json`. A few close mismatches are expected around splits;
send me the list if it's more than a handful.

## Later

Re-running it any time adds the new days and keeps the old ones. It's not scheduled. Ask
before turning it into a task.

## Roll back

Delete `Data\Symbol_chart\`.
