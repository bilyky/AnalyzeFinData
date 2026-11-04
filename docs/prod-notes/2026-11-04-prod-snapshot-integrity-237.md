# Snapshot integrity (#237): what you need to do

`run_history.py` (step 1 of the evening run) used to fill missing past days by fetching
Chaikin live and saving the result under the old date. The API only serves current values,
so those files hold the wrong date's data. On the dev cache about 68,000 snapshots are such
backfills (written in bursts on 2026-04-26/27, 05-07..09 and 07-24), and 15,290 sit on
weekday market holidays. Studies that read `Data\Symbol` used them (see R&D #67 in
`plans/roadmap.md`).

This PR stops new ones and adds a script that moves the bad files out of the cache.
Nothing is deleted: files go to `Data\Backup\snapshot_quarantine_<time>\` with a manifest.

## 1. Deploy

Merge #237, pull on PROD. From the next evening run, `run_history` fetches only the most
recent session; an older missing day is logged as a WARNING
(`<date>: N symbols have no snapshot. Not fetched ...`) instead of being filled. It also
treats 2026-11-27 (day after Thanksgiving) as a trading day now, as NYSE does.

## 2. First run the chart backfill (#234)

The integrity check compares each late file's PGR with Chaikin's daily PGR history in
`Data\Symbol_chart\`. Without it, only the price check runs, and batches whose prices were
repaired later (the 07-24 batch on dev, ~39,000 files) pass. So run #234's backfill first
(its PROD note, ~25 minutes).

## 3. Dry run (read-only, ~1 minute)

```powershell
venv_new\Scripts\python.exe scripts\utils\snapshot_integrity.py
```

On dev (chart history only for INTC and AAPL) it printed:

```
569857 snapshot files; 113997 written more than 3 days after their date
wrong-date (late batches failing price/PGR checks): 68047
weekday-holiday snapshots: 15290
late but unverifiable (no RapidAPI close and no chart row), left in place: 879
late batches that passed the checks (or too few dates), left in place: 39112
```

PROD's numbers will differ (its file times are its own). With the chart backfill done, most
of the "passed" line should move to "wrong-date". Send me the output before step 4 if the
wrong-date count is far above ~110,000 or the "passed" line stays large.

## 4. Quarantine

Back up first, then move. Not while the morning or evening pipeline runs:

```powershell
venv_new\Scripts\python.exe scripts\utils\snapshot_integrity.py --apply
```

It prints where the files went. `manifest.json` in that folder lists every file.

## 5. After

- Tell me, and I'll re-run the R&D #54-#66 verdicts and the factor study on the cleaned
  cache (R&D #67).
- Studies will see those dates as missing. That's the intent: missing, not wrong.

## Roll back

- Code: revert #237.
- Files: move the folders under `Data\Backup\snapshot_quarantine_<time>\wrong_date\` and
  `...\holiday\` back into `Data\Symbol\` (same relative paths).
