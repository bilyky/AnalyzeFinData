# Insider-buy study (#240, R&D #80): what you need to do

PR #240 adds a backtest, `scripts/backtesting/insider_buy_study.py`, and its R&D #80 entry in
`plans/roadmap.md`. It asks whether an officer or director buying shares on the open market
should pause position-watch's trims.

**Result: INCONCLUSIVE. No rule was added.** Nothing in the daily pipeline, the dashboard or
position-watch calls the study, so PROD behaves exactly as before.

## 1. Deploy

Merge, then pull. No restart, no config change, no scheduled task.

## 2. Check

Nothing to check on PROD. The roadmap page (`/api/roadmap`) shows the new R&D #80 entry.

When #244 (OceanView tab) is deployed, its Research card reads the newest
`Data\insider_buy_study_*.json`. To show the result there, copy the latest one from dev
(`insider_buy_study_2026-11-05_132039.json`, about 0.8 MB) into PROD's `Data\`. Without it the
card leaves R&D #80 out.

## 3. Running the study again (optional, not on PROD)

Run it on dev, not PROD: the first run downloads about 600 MB of SEC data into
`Data\insider_cache\` (59 quarterly files) and takes a while.

```powershell
$env:SEC_USER_AGENT = "<your name> <a contact email>"   # the SEC blocks requests without one
venv_new\Scripts\python.exe scripts\backtesting\insider_buy_study.py
```

Each run writes a new file, `Data\insider_buy_study_<date>_<time>.json`, so earlier results
are kept. The roadmap entry says what would re-open the question.

## Roll back

Revert the PR. Nothing else changes.
