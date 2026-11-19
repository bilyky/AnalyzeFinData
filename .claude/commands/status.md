# Portfolio Status — Master Health Check

Comprehensive overview of the system's operational and financial health.
Run all checks below and report findings.

**Scheduled:** AETHER_StatusReport runs this skill's `python -c` blocks unattended at 14:00 on PROD
(`scripts/utils/skill_report.py`). Each block must only read: no broker calls, email, subprocesses,
network calls or file writes. A block that mentions one is refused and reported, not run.

## Step 1 — Data freshness

```bash
python -c "
import os, datetime
from powergauge import XLSX_FILE
mtime = os.path.getmtime(XLSX_FILE)
ts = datetime.datetime.fromtimestamp(mtime)
print(f'Workbook: {ts.strftime(\"%Y-%m-%d %H:%M\")} ({\"FRESH\" if ts.date() == datetime.date.today() else \"STALE\"})')
"
```

## Step 2 — Portfolio financials

```bash
python -c "
import data_api
pf = data_api.read_portfolio()
print(f'Equity:  \${pf[\"equity\"]:,.2f}')
print(f'Cash:    \${pf[\"balance\"]:,.2f}')
print(f'Return:  {pf[\"return_pct\"]:+.2f}%')
print(f'Profile: {pf[\"profile\"]}')
print(f'Positions: {len(pf[\"positions\"])}')
"
```

## Step 3 — Scheduled task health

```bash
python -c "import data_api; [print(f'{t[\"name\"]}: {t[\"status\"]} (last: {t[\"last_run\"]})') for t in data_api.read_scheduled_tasks() if not t.get(\"retired\")]"
```

## Step 4 — Recent errors

```bash
python -c "import watchdog; errors = watchdog.check_logs(); [print(e) for e in errors] or print('No errors in last hour.')"
```

## Step 5 — Market regime

```bash
python -c "
import data_api, workbook_read
label, _ = workbook_read.get_market_regime()   # the regime the emails use (SPY Long60)
print(f'Regime: {label}')
print(f'Data fresh: {data_api.get_system_health().get(\"data_fresh\", False)}')
"
```

## Report format

Summarise findings as:

| Check | Result |
|---|---|
| Workbook freshness | FRESH / STALE (timestamp) |
| Equity / Return | $X,XXX / +X.X% |
| Scheduled tasks | N active / N missed / N never run yet |
| Recent errors | None / list issues |
| Market regime | Bull / Neutral / Bear |

Flag any stale data, missed tasks, or errors prominently. A task whose last run reads `never run` has not run yet (Task Scheduler shows 11/30/1999 for that): it is not missed.
