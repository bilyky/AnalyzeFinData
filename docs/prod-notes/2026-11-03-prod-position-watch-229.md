# Position watch (#229): what you need to do

PR #229 adds the `/position-watch` skill and `scripts/monitoring/position_watch.py`. It is
recommend-only: it never places an order and never opens a browser. No scheduled task and no
change to `config.json`. Nothing runs on its own, so nothing breaks if you skip these steps.

## 1. Pick the machine you run it on

It needs a fresh OHLCV cache (`Data/Symbol_full`) for the symbol, its peers and the replacements.

- **PROD** refreshes the cache every evening, so the numbers are current. Use this one.
- **Dev** only refreshes when the evening pipeline runs there. On 11-03 it was a day behind and had
  to be refreshed by hand. A stale cache is now a "doubt", so the call can't be TRIM.

## 2. One-time setup on that machine (private files, never in git)

Copy the private folder from dev. Use a USB stick or a private share, never the repo or email:

```text
Data\position_watch\config.json        floor, tranche, peers, replacements, stock-plan shares, 401(k), vest/ESPP dates, tax rates
Data\position_watch\INTC_lots.json     your lots: [{"acquired": "YYYY-MM-DD", "qty": N, "price": X}, ...]
Data\position_watch\INTC_journal.jsonl the daily calls + lessons (keep one copy only: run on one machine)
```

The config fields (any left out take the defaults shown):

```json
{"symbol": "INTC", "floor_pct": 0.30, "tranche_pct": 0.05,
 "replacements": ["SPY", "RSP"], "peers": ["AMD", "NVDA", "QCOM", "MU", "AVGO", "TSM", "MRVL", "ON"],
 "stock_plan_shares": 0, "outside_assets_usd": 0, "outside_symbol_shares": 0,
 "acquisition_dates": ["YYYY-MM-DD"], "lt_rate": 0.281, "st_rate": 0.371}
```

- `stock_plan_shares`: owned (not unvested) shares in the stock-plan account. The API doesn't
  see that account.
- `outside_assets_usd`: everything outside E*TRADE that counts in the total (e.g. the 401(k)),
  **including** the value of any `outside_symbol_shares`. Those shares are added to the
  symbol's value but not again to the total.
- `outside_symbol_shares`: shares of the symbol held outside E*TRADE (e.g. INTC in the 401(k)).
- `acquisition_dates`: past and upcoming vest and ESPP dates. They drive the wash-sale check.
- `lt_rate` / `st_rate`: your marginal rates.

If the account snapshot is missing (a fresh machine with no OceanView pack yet), the script
still runs: the share of the total shows `-` and the call is HOLD, never TRIM.

Check it reads them (no journal write):

```powershell
venv_new\Scripts\python.exe scripts\monitoring\position_watch.py --symbol INTC --no-journal
```

The header line should say `(v2)` and today's or the last session's bar date.

## 3. Daily (by hand, until you want a schedule)

In Claude Code: `/position-watch`. Or:

```powershell
venv_new\Scripts\python.exe scripts\monitoring\position_watch.py --symbol INTC --live
venv_new\Scripts\python.exe scripts\monitoring\position_watch.py --symbol INTC --lesson "<one sentence>"
```

`--live` needs today's E*TRADE token. Run it once a day; a second run the same day replaces that
day's entry.

## 4. Keep the private config current

- **After each RSU vest or ESPP purchase:** update `stock_plan_shares`, add the lot to
  `INTC_lots.json`, and keep `acquisition_dates` complete. The wash-sale check reads those dates.
- **Once a quarter:** update `outside_assets_usd` (the 401(k)) and `outside_symbol_shares` if
  the 401(k) ever holds INTC.
- **Tax rates (`lt_rate`, `st_rate`):** confirm them with your CPA.

## 5. Open item: E*TRADE tokens dying during the day

On dev on 11-03 two fresh tokens (09:31 and 10:30) were rejected with 401 within about 40-45
minutes. Nothing on dev rewrote the token file. The cause is unknown. One guess is a login
somewhere else with the same app key. To check on PROD:

```powershell
Select-String -Path Data\logs\*.log -Pattern "token saved|re-auth" | Where-Object { $_.Line -like "2026-11-03*" }
```

If PROD minted or renewed a token around 10:00-11:10 PT that day, logins on the two machines
cancel each other. Then the fix is to read quotes on one machine only.

## Roll back

Delete `Data\position_watch\`. Nothing else was changed.
