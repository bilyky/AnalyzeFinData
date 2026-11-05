# OceanView tab: what you need to do

A new **OceanView** tab in the dashboard (between Accounts and Chat). It needs the
dashboard login, and shows, for INTC:

- **Today:** position-watch's call (TRIM / HOLD / WAIT) with its reasons and data doubts,
  the share of the total against the 30% floor, INTC vs its chip peers, SPY/RSP entry timing,
  and the lots a TRIM would sell with the estimated tax;
- **RETRO:** every daily call, how it turned out, the hit rate, and the lessons;
- **Tax & calendar:** gains by long/short term, lots turning long-term soon, and the vest /
  ESPP dates with their wash-sale windows;
- **Research:** the latest study results that bear on the plan (R&D #78, gap drift).

The page is read-only: it runs position-watch with `journal=False`, so opening it never
adds a journal entry. The lots' account field is never sent to the page.

## 1. Order

#229 (position-watch) is merged, so this can go any time. #243 (login on the other private
routes) can go before or after; this page sends the login itself. If #243 lands first, this
PR's `web/app.js` needs a small merge (both touch the same area).

## 2. Private files on PROD

The tab reads `Data\position_watch\config.json`, `INTC_lots.json` and `INTC_journal.jsonl`.
If they aren't on PROD yet, copy them privately (the #229 note, step 2). Without them the tab
shows the call and research but no lots or journal.

## 3. Deploy

Merge, pull, restart the dashboard:

```powershell
venv_new\Scripts\python.exe server.py restart
```

## 4. Check

- Logged out: the tab says "🔒 Log in (top right) to see OceanView."
- Logged in: four cards. It takes a few seconds (it loads prices for INTC, its peers, SPY
  and RSP).
- The "account snapshot" in Today should be `ok` or `degraded`. `failed` means the evening
  pipeline's account snapshot is older than 24 h, and any TRIM is held back as WAIT.

## Roll back

Revert the PR and restart the dashboard. No data changes.
