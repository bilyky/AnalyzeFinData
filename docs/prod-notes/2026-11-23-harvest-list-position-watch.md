# Harvest list (O2a) and the position-watch wait wording (2026-11-23)

## Harvest list: `python -m aether.harvest`
- Reads every losing position's tax lots from E*TRADE through the client (read-only, no login, no orders, writes nothing).
- Per symbol, across the listed accounts: shares, value, short- and long-term loss on today's date, the tax value at the
  `st_rate` / `lt_rate` in `Data\position_watch\config.json`, and the first clean buy-back date (today + 31 days).
- **WASH RISK** marks a buy of the same symbol within the last 30 days in any listed account. Selling only the older
  lots at a loss would then be partly a wash sale: sell every lot, or wait.
- **Scheduled buys (O2b):** the watched symbol's vest / purchase dates (`acquisition_dates` in position-watch's
  config) are checked too. A loss sale within 30 days of one is flagged `VEST/ESPP <date>`, with the next clean day.
- The watched symbol's loss lots are listed even when the whole position is up (`loss lots only`: sell them by
  specific lot).
- Not checked: accounts the API doesn't list (stock plan, 401(k), other brokers).
- `--json` prints the full list. It holds account digits and amounts, so keep it private.

## position-watch: the wait names the session
- The crash-day wait (the owner's rule: after a big drop, wait a day before selling) now says which session fell:
  "last session (2026-11-20) fell -8.0%: wait a day after a big drop before selling".
- With `--live`, the report adds today's move against that close (`today +13.8% (live vs 2026-11-20)`).
- The call itself doesn't change.

## Margin pay-down for a buffer (plan O3)
- The Oracle's margin card now adds one line per margined account: "To survive a 20% fall: deposit $X or sell ~$Y;
  a 30% fall: ..." (or "covered"). The sales are at the account's average requirement; the pay-down table below
  it still lists the sales that free the most requirement first.

## PROD check
After the pull:
1. `python -m aether.harvest` should list the losing positions; a name bought within the last 30 days shows a WASH RISK flag.
2. `python scripts/monitoring/position_watch.py --live --no-journal` should show both the "today" and the
   "last session" figures.

Rollback: revert the PR.
