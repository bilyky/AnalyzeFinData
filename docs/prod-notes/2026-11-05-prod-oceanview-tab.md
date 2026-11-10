# OceanView tab: what you need to do

A new **OceanView** tab in the dashboard (between Accounts and Chat). It needs the
dashboard login, and shows, for INTC:

- **Today:** position-watch's call (TRIM / HOLD / WAIT) with its reasons and data doubts,
  the share of the total against the 30% floor, INTC vs its chip peers, SPY/RSP entry timing,
  and the lots a TRIM would sell with the estimated tax;
- **Plan:** the dated investment plan's daily check (`invest_plan.py`): the steps, what to do
  today, each holding against its stop. Shown only when the private plan file exists;
- **RETRO:** every daily call, how it turned out, the hit rate, and the lessons;
- **Tax & calendar:** gains by long/short term, lots turning long-term soon, and the vest /
  ESPP dates with their wash-sale windows;
- **Research:** the latest study results that bear on the plan (R&D #80 with its monthly
  mean, the basis of its verdict; gap drift with its per-event mean).

The page is read-only: it runs position-watch with `journal=False`, so opening it never
adds a journal entry. The lots' and the plan fills' account field is never sent to the page.

## 1. Order

Everything this needs is merged: #229 (position-watch), #240 (insider study), #246 (plan
check), #243 (login on the private routes) and #252 (plan-check button). This PR already
includes them, so it can go any time.

## 2. Private files on PROD

The tab reads `Data\position_watch\config.json`, `INTC_lots.json`, `INTC_journal.jsonl` and
`invest_plan.json`. If they aren't on PROD yet, copy them privately (the #229 note, step 2; the
#246 note for the plan). Without them the tab shows the call and research but no lots, journal
or plan. For the Research card, copy the latest `Data\insider_buy_study_*.json` (the #240 note).

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
