# PROD — portfolio backtest out-of-sample split (#191) — 2026-10-20

**What it changes**
- `scripts/backtesting/portfolio_backtest.py`: `--split DATE` (turns on `--attribution` too)
  reruns every variant from fresh cash before DATE (train) and from DATE on (test). It picks
  the rule variant with the best train Sharpe and reports its test Sharpe and rank. The logic
  lives in `oos_split()`; `DATE` must be `YYYY-MM-DD`.
- Without `--split`, the output is the same as after #190.
- Tests only otherwise (`tests/test_portfolio_backtest.py`, 34 tests on this branch).

**Runtime effect on PROD: none.** Nothing in the trading path, the web server or any
scheduled task imports or runs this script. No config, no new task, no restart.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`;
`python` = `venv_new\Scripts\python.exe`.

## 0. Branch state before merge (dev, 2026-10-20)

- #188 (`561ad8b`) and #190 (`df8eb99`) are merged. #191 is retargeted to `main`, with main
  merged in (0 behind, clean). The PR diff is the 2 files above.
- Full suite on the branch: `Ran 1461 tests … OK (skipped=6)`. `ruff check .` clean. CI green.
- Review 🟠 (no test of the split) and all 🟡 items are fixed at `96b7a27`.

## 1. Deploy

Rides the normal pull: back up, then `python server.py upgrade`.

## 2. Verify (optional, hermetic)

```
python -m unittest tests.test_portfolio_backtest -v
```

Expect `Ran 34 tests … OK`. The `run()` tests use a temp fake cache, never the real `Data\`.

## 3. Optional: run on PROD data

Same timing, reads and writes as #188's note step 3: run it outside the evening task and the
07:00 game run. It writes only `Data\portfolio_backtest_panel.json` and
`Data\portfolio_backtest.json`.

```
python scripts\backtesting\portfolio_backtest.py --split 2025-01-01 --workers 4
```

It prints the full-period table, then TRAIN and TEST tables, then
`picked on train: <variant> -> test Sharpe …, rank N/8`.

## 4. Rollback

`git revert <merge commit of #191>`. Output files from an optional run can be deleted or left.

## Results so far (dev data, not PROD; 600 symbols, through 2026-10-16)

`Data\portfolio_backtest_oos_2026-10-20.json`: the train pick `no_slot_expansion` (Sharpe 1.66)
ranks 6th of 8 on test (1.08, below `aether_rules`' 1.38). The full-period leads don't hold out
of sample. **No rule or config change is recommended.** The one consistent lead, removing the
1% cap, weakens a loss-minimization rule, and the cap sweep found no level reliably better than 1%.
