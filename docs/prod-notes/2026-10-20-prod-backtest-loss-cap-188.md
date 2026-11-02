# PROD — portfolio backtest models the 1% loss cap (#188) — 2026-10-20

**What it changes**
- `scripts/backtesting/portfolio_backtest.py`: each `aether_rules` buy is trimmed so a
  stop-out loses at most `CFG.system_max_trade_risk_pct` of equity (1% by default). It uses
  the same helper as the live buy path (`risk_utils.risk_capped_cash`, from #155).
- New `aether_no_cap` row: the old behavior, for comparison. New `Trims` column: buys the
  cap trimmed.
- The docstring lists where the simulator's cap differs from live (no ATR → no stop → no cap;
  a trimmed buy can land under the $100 minimum ticket; fractional shares).
- Tests only otherwise (`tests/test_portfolio_backtest.py`, 19 tests).

**Runtime effect on PROD: none.** Nothing in the trading path, the web server, or any
scheduled task imports or runs this script (checked with `git grep` on 2026-10-20). No config,
no new task, nothing to restart for.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`;
`python` = `venv_new\Scripts\python.exe`.

## 0. Branch state before merge (dev, 2026-10-20)

- Main merged in at `c691574` (0 behind, clean merge). Of the files I checked
  (`scripts/backtesting/`, `aether/{risk_utils,config,sell_rules,patterns,scoring}.py`,
  `instruments.py`), main only added an unrelated study
  (`scripts/backtesting/rubber_band_stop_study.py`) and changed `aether/sell_rules.py`
  (#184, AI-override helpers);
  `exit_decision` behaves the same (same reason text, now held in a constant). Their
  transitive imports were not checked; the full suite passing is the cover for those.
- Full suite on the merged branch: `Ran 1446 tests … OK (skipped=6)`. `ruff check .` clean.
- #190 and #191 are stacked on this branch. After #188 merges, retarget #190 to `main`
  (and #191 after #190).

## 1. Deploy

Rides the normal pull: back up, then `python server.py upgrade`. No restart is needed for this
PR itself.

## 2. Verify (optional, hermetic)

```
python -m unittest tests.test_portfolio_backtest -v
```

Expect `Ran 19 tests … OK`. These tests never touch the real `Data\`: `run()` is tested on a
temp fake cache.

## 3. Optional: run the backtest on PROD data

Only if you want PROD's numbers (the dev results are in `Data\portfolio_backtest_2026-10-12.json`).

- **When:** outside the 17:00–~20:00 evening task and the 07:00 game run. Building the score
  panel took ~8 min with 14 workers on the dev box. By default it uses all CPUs but one.
- **Reads:** `Data\Symbol`, `Data\Symbol_full` and the study JSONs (read-only).
- **Writes:** only `Data\portfolio_backtest_panel.json` (score cache) and
  `Data\portfolio_backtest.json` (results). Neither is a core state file, and nothing reads
  them.
- **Cap level:** it uses PROD's `config.json` `system.max_trade_risk_pct` if set, else 1%.

```
findstr /C:"max_trade_risk_pct" config.json
python scripts\backtesting\portfolio_backtest.py --workers 4
```

The table must show an `aether_rules` row with `Trims` > 0 and an `aether_no_cap` row with
`Trims` 0.

## 4. Rollback

`git revert <squash commit of #188>`. If the optional run was done, you can also delete
`Data\portfolio_backtest_panel.json` and `Data\portfolio_backtest.json` (harmless if left).

## Results so far (dev data, not PROD)

| variant | CAGR% | MaxDD% | Sharpe |
|---|---|---|---|
| aether_rules (1% cap) | 9.62 | −24.79 | 0.74 |
| aether_no_cap | 14.86 | −29.62 | 0.90 |
| spy_buy_hold | 20.83 | −19.00 | 1.36 |

576 symbols, 2023-01-03 → 2026-10-05. The cap-level sweep (`Data\portfolio_backtest_cap_sweep_2026-10-13.json`)
found no level that is reliably better than 1%, so **no config change is recommended**.
