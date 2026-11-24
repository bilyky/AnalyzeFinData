# Digit-sum shadow, stock by stock (R&D #72 follow-up)

Owner decision 2026-11-24: the digit-sum term no longer moves Short10 / Long60, except for stocks a monthly per-stock check confirms. The first check confirmed none, so the term is off for every stock.

## What changes on PROD

- `powergauge._compute_pgr_fields` keeps the raw score in `digit_sum_raw` and sets `digit_sum` to 0 unless the stock is on `Data/digit_sum_allowlist.json`.
- With no list file, every stock gets 0. Expect Short10 to move by up to 1.0 and Long60 by up to 0.5 for the ~120 stocks the old index fired on; picks near the buy line can change.
- New weekly task `AETHER_DigitSum_Monthly` (Saturday 11:00): runs `scripts/backtesting/digit_sum_per_stock.py --write-allowlist --monthly`, which works only when the list is missing or 28+ days old.

## Steps

1. Pull main.
2. Register the new task (admin PowerShell): `powershell -ExecutionPolicy Bypass -File scripts/utils/register_agent_tasks.ps1 -Only AETHER_DigitSum_Monthly`
3. Optional: run the task once by hand to write the list now:
   `python scripts/backtesting/digit_sum_per_stock.py --write-allowlist`
   (about 2 minutes; it writes `Data/digit_sum_allowlist.json` with `"symbols": []` today).

## Evidence to expect in the next drop

- `python -c "from aether import scoring; print(scoring.digit_sum_allowed('AAPL'))"` prints `False` (no stock is listed).
- After the first Saturday: `Data/digit_sum_allowlist.json` with a `run_at` date and `digit_sum_monthly.log`.
- A second Saturday within 28 days logs "monthly check skipped".
