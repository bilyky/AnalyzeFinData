# Data completeness check (#235): what you need to do

From the next evening run after deploy, the daily report email gets a red **DATA
COMPLETENESS** block at the top (and `[DATA]` in the subject) whenever the day's Chaikin
download is less complete than usual. No block = nothing wrong. Read-only; no config change,
no scheduled task. If the check itself fails, the email goes out as before and the log
has `Data completeness check failed`.

What it compares, every evening, with the median of the last 10 sessions:
- every field of every snapshot: a field that is usually filled and drops below half is
  an alarm (this is the check that would have caught the 2026-09-01 factor loss that evening);
- the number of snapshots;
- values: the 12-char signal string, PGR 1-5 (0 / -1 = unrated);
- an OHLCV bar for the day (alarm when more than 10% of stocks lack one);
- a raw copy per snapshot, once `Data\Symbol_raw\` exists (#232);
- Research-sheet symbols that were there in 8 of the last 10 sessions and are missing today
  (named; on dev, 09-01 shows 11 symbols that stopped downloading at the API switch);
- non-trading days: weekend snapshots are only a note (the 17:00 task runs daily, so
  Saturday and Sunday emails stay clean); snapshots on a weekday holiday are an alarm.

## 1. Before deploying: see what it says about recent days (read-only)

```powershell
venv_new\Scripts\python.exe -m aether.data_completeness --date 2026-09-01
venv_new\Scripts\python.exe -m aether.data_completeness
```

On dev, 2026-09-01 gives "44 fields went empty", 2026-10-31 (Saturday) gives a weekend note
and `OK`, and 2026-10-30 gives `OK`. The second line checks the latest day; it should be `OK`
(the factor loss is now the "usual" state, so it isn't flagged again).

## 2. Deploy

Merge #235, pull on PROD. Nothing else.

## 3. After the next evening run

Check the email. If there is a DATA block, run the command it shows and send me the output.

Order with #232: if #232 is deployed first, `Data\Symbol_raw\` starts filling that evening.
On the first day the check may report snapshots without a raw copy if some were fetched
before the deploy; that clears the next day.

## Roll back

Revert #235. The email goes back to how it was.
