# Daily Run Pipeline — Automated Workflow

Executes the full end-to-end trading pipeline.

## Steps Performed

1.  **Workbook Refresh:** Runs `python main.py` to fetch latest Chaikin data.
2.  **Data Freshness Check:** Verifies that `Data/state_of_the_day.xlsx` was updated today.
3.  **Sheet Validation:** Ensures `Research`, `Picks`, and `Replacements` sheets rendered and contain data.
4.  **Top-5 Scoring:** Computes the top 5 symbols where `Setup == 'OK'`, using decimal `Win%` and `S10+L60` total scores.
5.  **Notification:** Sends a formatted HTML summary directly to Gmail.

## Command

Run immediately:
```
python autonomous_pipeline.py
```

## Scheduled Task (Automation)

To run this autonomously at 5:30 AM PST on Windows:

1.  Open **Task Scheduler**.
2.  Create a new task named `AnalyzeFinData_Daily`.
3.  **Trigger:** Daily at 5:30 AM.
4.  **Action:** Start a Program.
    - Program/script: `C:\Develop\StockTrading\AnalyzeFinData\venv_new\Scripts\python.exe` (use absolute path)
    - Add arguments: `autonomous_pipeline.py`
    - Start in: `C:\Develop\StockTrading\AnalyzeFinData` (use absolute path)
