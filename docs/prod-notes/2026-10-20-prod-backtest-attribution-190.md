# PROD — portfolio backtest rule attribution (#190) — 2026-10-20

**What it changes**
- `scripts/backtesting/portfolio_backtest.py`:
  - `simulate(disable=...)` switches off one rule at a time: `stop`, `soft_exit`,
    `winner_protection`, `regime`, `cash_buffer`, `slot_expansion`, `loss_cap`;
  - `--attribution` adds one `aether_rules` row per rule switched off (`no_<rule>`);
  - the exit step now runs `exit_decision`'s stop floor and the soft exit separately, so
    switching `stop` off really removes every stop exit, including the 8%-of-cost fallback.
    For the default run and every other variant this is behavior-identical (checked
    trade-for-trade, old vs new simulator, same inputs, 2026-10-15).
- Tests only otherwise (`tests/test_portfolio_backtest.py`, 28 tests on this branch).

**Runtime effect on PROD: none.** Nothing in the trading path, the web server or any
scheduled task imports or runs this script (`git grep`, 2026-10-20). The default run (no
`--attribution`) gives the same output as after #188. No config, no new task, no restart.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`;
`python` = `venv_new\Scripts\python.exe`.

## 0. Branch state before merge (dev, 2026-10-20)

- #188 merged (`561ad8b`). #190 retargeted to `main`, main merged in at `56fae19`
  (0 behind, clean merge); the PR diff is the 2 files above.
- Full suite on the merged branch: `Ran 1455 tests … OK (skipped=6)`. `ruff check .` clean.
  CI: lint, quality-gate, open-pr all success.
- #191 is stacked on this branch. After #190 merges, retarget #191 to `main`.

## 1. Deploy

Rides the normal pull: back up, then `python server.py upgrade`.

## 2. Verify (optional, hermetic)

```
python -m unittest tests.test_portfolio_backtest -v
```

Expect `Ran 28 tests … OK`. The `run()` tests use a temp fake cache, never the real `Data\`.

## 3. Optional: run the attribution on PROD data

Same timing, reads and writes as #188's note step 3 (outside the evening task and the
07:00 game run; writes only `Data\portfolio_backtest_panel.json` and
`Data\portfolio_backtest.json`). The extra rows take well under a second each once the
bars are loaded (~20 s on dev).

```
python scripts\backtesting\portfolio_backtest.py --attribution --workers 4
```

The table gains 6 `no_*` rows. Every one should show `Trims` > 0 (the 1% cap stays on in
all of them).

## 4. Rollback

`git revert <merge commit of #190>`. Results files from an optional run can be deleted or left.

## Results so far (dev data, not PROD; 2023-01-03 → 2026-10-09; from the 2026-10-15 old-vs-new simulator check)

| rule switched off | CAGR% | MaxDD% | Sharpe |
|---|---|---|---|
| none (aether_rules) | 9.65 | −24.79 | 0.74 |
| loss_cap (aether_no_cap) | 15.03 | −29.62 | 0.91 |
| stop (fixed) | 8.41 | −18.00 | 0.66 |
| soft_exit | 12.40 | −18.29 | 0.69 |
| winner_protection | 14.31 | −17.80 | 0.92 |
| regime | 17.43 | −13.16 | 1.26 |
| cash_buffer | 0.79 | −34.01 | 0.13 |
| slot_expansion | 15.42 | −10.59 | 1.30 |

**No rule change is recommended.** This is one in-sample path. The out-of-sample split (#191)
showed the in-sample leads (`regime`, `slot_expansion`) don't hold on 2025–2026. Dropping the
regime gate or the stop would also go against the Rule of Loss Minimization.
