# PROD — candlestick contrarian sign in one place (#146) — 2026-10-07

**What it changes:** `patterns.candlestick_score()` now returns the raw bull/bear tally
(+ = bullish). The contrarian sign lives only in `scoring.short_score/long_score`
(`* -0.30` / `* -0.15`). Before, `candlestick_score` was multiplied by the study's
`aggregate_sign` and scoring added it with `+0.30` / `+0.15`.

**Effect on PROD scores: none, if PROD's study file says `aggregate_sign: -1`.**
Checked 2026-10-07 against current main (with #155): 40 random symbols, 1,200 observations
(753 with a non-zero candlestick score), **0 short/long score mismatches** vs main.

Paths below are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`.
`python` = `venv_new\Scripts\python.exe`. It fits the same quiet window as
`2026-10-06-prod-finalize.md` and `2026-10-06-prod-ohlcv-rapidapi.md`, and can ride the same
`python server.py upgrade` once #146 is merged.

## 1. Pre-check (read-only, cmd)

```
python -c "import json,os;p=r'Data\candlestick_pattern_study.json';print('study present:',os.path.exists(p));d=json.load(open(p)) if os.path.exists(p) else {};print('aggregate_sign:',d.get('aggregate_sign'),'as_of:',d.get('as_of'))"
```

- `study present: True` and `aggregate_sign: -1`: **no score change**. Deploy.
- `study present: False`: scores **change**. Today PROD falls back to sign +1
  (non-contrarian); after #146 it is contrarian, as the study says it should be. That's the
  intended behavior, but it shifts S10/L60 for every symbol with candlestick patterns. Either
  copy the study file from the dev machine first (`Data\candlestick_pattern_study.json`, dev
  copy `as_of 2026-06-29`, sign -1), or accept the shift knowingly.
- `aggregate_sign: 1`: the study now says *momentum*, not contrarian. Don't deploy until the
  coefficient is reviewed (the new guard test would fail on PROD, see step 3).

## 2. Deploy

Back up (step 2 of `2026-10-06-prod-finalize.md`), then `python server.py upgrade` and
`python server.py status`. No config or data changes.

## 3. Verify (right after the pull)

```
python -m unittest tests.test_scoring.TestCandlestickSignGuard -v
```

On PROD the study file is present, so the parity test **runs** (in CI it is skipped). Both
tests must say `ok`: the live coefficient sign must equal the study's `aggregate_sign`.

## 4. Things that look different (expected)

- Raw `candlestick_score` values in `pattern_discovery` / `backtest_ratings` output are now
  **+ = bullish**, the opposite sign from earlier PROD runs. Keep that in mind when comparing
  against older reports. The S10/L60 scores themselves are unchanged (step 1 = -1).
- The workbook's Patterns column and the UI: `CS↑` / `CS↓` flip meaning for new rows (now `CS↑` =
  bullish candles; before it showed for a *bearish* raw tally). No scorer reads it.
- A future `candlestick_pattern_study.py` run that flips the verdict now logs a loud
  `SIGN MISMATCH` warning instead of silently changing the factor's direction.

## 5. Rollback

`git revert <squash commit of #146>`, then `python server.py restart`. No data to restore.
