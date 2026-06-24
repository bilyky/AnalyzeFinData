# AnalyzeFinData — Claude Instructions

## General Principles

*   **Rule of Zero Trust:** NEVER trust an initial data point or assumption. ALWAYS verify by checking file timestamps, reading raw JSON cache, or cross-referencing E*TRADE quotes before raising doubts or making moves.
*   **Rule of Temporal Zero-Trust:** NEVER assume or guess the current date or time. Before ANY time-dependent operation (checking market hours, verifying file freshness, or generating reports), the system MUST execute an empirical clock check via a shell tool first. Never trust memory for temporal state.
*   **Mandatory Backup Policy:** NEVER run a script or modify a file without a verified backup. All core state files (`ai_portfolio_game.json`, `state_of_the_day.xlsx`, `ops_and_rd_tracker.xlsx`) must be cloned to a timestamped backup directory (`Data/Backup/`) before any write is executed.

## Workflow Conventions

When the user asks to "send", "push", "create", or "save" something (e.g., Gmail draft, commit, file), execute the full action — do not just preview or show content for review unless explicitly asked.

## Excel / openpyxl

For Excel/openpyxl work: when a fix is claimed to be working, verify by actually reopening the file (or simulating the reopen path) and checking the warning/state is gone before reporting success.

Before telling the user a fix works, re-run the exact reproduction steps the user would take (reopen the file, re-execute the script, re-query) and paste the output proving the issue is resolved. If you cannot verify end-to-end, say so explicitly.

## Project Conventions

Standard ranking/query conventions: Setup field uses `'OK'`/`''` strings (not 1/0), and Win% is stored as a decimal (multiply by 100 for display).
