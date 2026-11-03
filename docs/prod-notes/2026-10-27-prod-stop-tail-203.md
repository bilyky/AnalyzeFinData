# PROD — stop-out tail in the portfolio backtest, R&D #54a (#203) — 2026-10-27

**Merged 2026-10-27 as `628cf44`.**

**What it changes**
- `scripts/backtesting/portfolio_backtest.py`: each trade records its `stop` and
  `pnl_pct_equity` (P&L as % of the equity the buy was sized on, costs included). Every run
  prints a stop-out table per variant: stop-outs, gaps below the stop, and loss per stop-out
  (median / P95 / worst). It then prints the R&D #54a gate line:
  `gate (aether_rules): P95 …% vs 1.5%, worst …% vs 3.0% -> PASS/FAIL`.
- `plans/roadmap.md`: R&D #54 status updated (a = done, gate PASS, no change).
- Tests only otherwise.

**Runtime effect on PROD: none.** Nothing in the trading path, the web server or any scheduled
task imports or runs this script (`git grep`, 2026-10-27). No config, no new task, no restart.
**No rule or config change is recommended:** the 1% loss cap holds.

Paths are relative to the PROD checkout `C:\Develop\StockTrading\AnalyzeFinData`;
`python` = `venv_new\Scripts\python.exe`.

## 0. Branch state before merge (dev, 2026-10-27)

- 0 behind `main`. Head `9747465`; its only change from the reviewed and approved `8d6ced3` is
  roadmap wording.
- Full suite on `8d6ced3`: `Ran 1505 tests … OK (skipped=6)`. `ruff check .` clean. CI green.
  The review approved it as ready for PROD.

## 1. Deploy

Rides the normal pull: back up, then `python server.py upgrade`.

## 2. Verify (optional, hermetic)

```
python -m unittest tests.test_portfolio_backtest -v
```

Expect `Ran 42 tests … OK`. The `run()` tests use a temp fake cache, never the real `Data\`.

## 3. Optional: re-check the cap on PROD data

Same timing, reads and writes as the #188 note step 3: run it outside the evening task and the
07:00 game run. It writes only `Data\portfolio_backtest_panel.json` and
`Data\portfolio_backtest.json`.

```
python scripts\backtesting\portfolio_backtest.py --workers 4
```

Look for the `Stop-out loss, % of equity (R&D #54a)` table and the `gate (aether_rules)` line.
`PASS` = the cap holds on PROD data too. If it says `FAIL`, don't change the cap from this
alone: send me `Data\portfolio_backtest.json` and I'll check which trades broke it. A worst-case
fail from a single gap is much weaker evidence than a P95 fail.

## 4. Rollback

`git revert -m 1 628cf44`. Output files from an optional run can be deleted or left.

## Result so far (dev data, not PROD; 604 symbols, 2023-01-03 → 2026-10-23)

`aether_rules` (1% cap): 112 stop-outs, 21 gapped. Loss per stop-out: median 0.55%, **P95 1.20%**
(gate 1.5%), **worst 2.57%** (gate 3%; ATSG, Nov 2023, opened 13.7% below its stop). Without the cap:
P95 1.80%, worst 3.05%. The P95 holds under a date bootstrap (90% interval 1.03–1.27%); the
worst case rests on one trade. Results: `Data\portfolio_backtest_stop_tail_2026-10-27.json`
(dev box).
