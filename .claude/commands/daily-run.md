# Daily Run Pipeline — Automated Workflow

Runs the AETHER **screening & intelligence** pipeline: fetches live Chaikin
PowerGauge data for all screener symbols, updates the Excel workbook, computes
the qualitative picks/rotations, and sends an email summary.

> **Scope (post scheduler-split):** this driver does **not** place trades. Deterministic
> buy/sell execution and stop checks run in the separate `AETHER_ExecuteTrades` task
> (`ai_portfolio_game.py --run`) at 07:00; this qualitative `AETHER_DailyDriver` runs
> just after. The two share the `portfolio_state` mutex (`aether/run_guard.py`), so the
> pipeline waits for the executor to finish rather than racing it on the workbook.

## Run

```bash
python autonomous_pipeline.py
```

## Verify success

Check the workbook was updated today:
```bash
python -c "
import os, datetime
from powergauge import XLSX_FILE
mtime = os.path.getmtime(XLSX_FILE)
ts = datetime.datetime.fromtimestamp(mtime)
status = 'FRESH' if ts.date() == datetime.date.today() else f'STALE (last: {ts.strftime(\"%Y-%m-%d %H:%M\")})'
print(f'{XLSX_FILE}: {status}')
"
```

If stale, check for errors:
```bash
python -c "import watchdog; errors = watchdog.check_logs(); [print(e) for e in errors] or print('No errors.')"
```

## Scheduling on Windows (optional)

To run automatically at 5:30 AM daily via Task Scheduler:
- Program: `venv\Scripts\python.exe` (relative to repo root)
- Arguments: `autonomous_pipeline.py`
- Start in: the repo root directory (where this file lives)
- Trigger: Daily at 05:30

The watchdog task (`watchdog.py`) runs hourly and self-heals crashes.
