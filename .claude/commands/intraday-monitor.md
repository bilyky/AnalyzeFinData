# Intraday Stop Monitor — Real-time Risk Guard

Monitors current E*TRADE positions against their Stop prices defined in the Research sheet.

## What it does

1.  **Load Positions:** Reads `Data/state_of_the_day.xlsx` (Short_Long sheet) to identify current holdings and their `Stop` levels.
2.  **Live Check:** Queries E*TRADE for the `lastPrice` of each position.
3.  **Alerting:** If `lastPrice <= Stop`, it immediately sends an alert via `notify.py`.

## Command

Run once:
```
python intraday_monitor.py
```

## Scheduled Task

To monitor every 30 minutes during market hours:

1.  **Task Scheduler:** Create `AnalyzeFinData_Monitor`.
2.  **Trigger:** Daily, repeat every 30 minutes for 7 hours (starting at 6:30 AM PST).
3.  **Action:** `python intraday_monitor.py`
