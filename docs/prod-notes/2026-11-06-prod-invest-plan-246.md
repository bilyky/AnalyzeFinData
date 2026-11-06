# Investment plan check (#246): what you need to do

PR #246 adds `scripts/monitoring/invest_plan.py`, a daily check of a short, dated plan (steps,
fills, stops, sell window). It is recommend-only: it never places an order and writes nothing.
The `/position-watch` skill runs it as Step 4.

## 1. Pick the machine

Run it on the same machine as position-watch (the #229 note, step 1). It reads only the price
cache and the plan file. It needs no E*TRADE or Chaikin login.

## 2. One-time setup (private file, never in git)

Copy the plan from dev the same way as the other position-watch files (the #229 note, step 2):

```
Data\position_watch\invest_plan.json   steps, rules, fills
```

Keep one copy only. Fills are added by hand, so two copies drift apart.

## 3. Deploy

Merge, then pull. No restart: it's a script, not part of the dashboard.

## 4. Check

```powershell
venv_new\Scripts\python.exe scripts\monitoring\invest_plan.py
```

- The header line shows the last bar date. If it says the price cache is behind, every
  BUY/SELL turns into `WAIT (not sure)`; refresh prices first.
- A step you bought shows `done`. A step is covered when your fills are within $1 or 1% of
  it, so fractional shares that leave a few dollars still count.
- Each holding shows its stop. Index funds use the plan's `index_stop` from cost.

## 5. Daily

Run it after the close, with position-watch. Record each buy in `fills` (date, symbol,
shares, price; `stop` for non-index names). It doesn't install a schedule.

## Roll back

Revert the PR. Delete `Data\position_watch\invest_plan.json` if you no longer want it.
