# Intraday Stop Monitor — Real-time Risk Guard

Checks all open positions against their stop-loss levels in real time and
sends email alerts for any breaches. Reads positions from the workbook
(`Data/state_of_the_day.xlsx`, Short_Long sheet), fetches live prices from
E*TRADE (or Google Finance fallback), and alerts if `lastPrice <= stop`.

## Run once

```bash
python scripts/utils/intraday_monitor.py
```

## Check for breach (programmatic)

```python
import data_api

accounts = data_api.read_accounts()["accounts"]
for acct in accounts:
    for h in acct.get("holdings", []):
        if h.get("stop") and h.get("current") and h["current"] <= h["stop"]:
            print(f"STOP BREACH: {h['symbol']}  current={h['current']:.2f}  stop={h['stop']:.2f}")
```

## Scheduling (Windows Task Scheduler)

To monitor every 30 minutes during market hours (6:30 AM – 1:30 PM PST):
- Program: `venv\Scripts\python.exe`
- Arguments: `scripts/utils/intraday_monitor.py`
- Trigger: Daily, repeat every 30 min for 7 hours starting 06:30
- Task name: `AnalyzeFinData_Monitor`
