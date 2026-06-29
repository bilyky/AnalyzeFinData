# AnalyzeFinData — Claude Instructions

## General Principles

*   **Rule of Zero Trust:** NEVER trust an initial data point or assumption. ALWAYS verify by checking file timestamps, reading raw JSON cache, or cross-referencing E*TRADE quotes before raising doubts or making moves.
*   **Rule of Temporal Zero-Trust:** NEVER assume or guess the current date or time. Before ANY time-dependent operation (checking market hours, verifying file freshness, or generating reports), the system MUST execute an empirical clock check via a shell tool first. Never trust memory for temporal state.
*   **Mandatory Backup Policy:** NEVER run a script or modify a file without a verified backup. All core state files (`ai_portfolio_game.json`, `state_of_the_day.xlsx`, `ops_and_rd_tracker.xlsx`) must be cloned to a timestamped backup directory (`Data/Backup/`) before any write is executed.
*   **Empirical Verification:** Before recommending an action (EXIT/BUY), the system must backtrack: check the last 3 times a similar technical setup occurred for that symbol in the local cache to verify its historical hit rate.
*   **Self-Correction:** If the system identifies a discrepancy (e.g., live price vs. OHLCV close), it must automatically log the correction and re-calculate dependent scores.
*   **Rule of Loss Minimization:** NEVER prioritize returns over risk. Capital preservation is the absolute priority. The downside on any single trade must be strictly capped using dynamic ATR stop-losses, and the portfolio must maintain a high defensive cash cushion (at least 50%) during broad market corrections (SPY L60 < -2).

## Saturday R&D Roadmap (Next Sessions)

*   **Session: Saturday, July 4, 2026 (10:00 AM PST) — Jeremy Grantham & Mean-Reversion:**
    1.  *The 2.5-Sigma Bubble Detector:* Build a technical filter that calculates how far SPY or sector ETFs are trading relative to their 500-day mean, blacklisting any asset class in the "Super-Bubble" zone (> 2.5 standard deviations above mean).
    2.  *Structural Scarcity Core:* Formalize Grantham's thesis on resource depletion by creating a dedicated, long-term 20% portfolio allocation cap reserved strictly for metals, agriculture, and grid utility assets (e.g., FCX, RS, EIX) to ride the "Hard Asset Supercycle."
    3.  *Margin Reversion Check:* Integrate a fundamental filter to penalize companies with peak, unsustainable profit margins and reward those with depressed margins forming a technical bottom.

## Workflow Conventions

When the user asks to "send", "push", "create", or "save" something (e.g., Gmail draft, commit, file), execute the full action — do not just preview or show content for review unless explicitly asked.

## Excel / openpyxl

For Excel/openpyxl work: when a fix is claimed to be working, verify by actually reopening the file (or simulating the reopen path) and checking the warning/state is gone before reporting success.

Before telling the user a fix works, re-run the exact reproduction steps the user would take (reopen the file, re-execute the script, re-query) and paste the output proving the issue is resolved. If you cannot verify end-to-end, say so explicitly.

## Project Conventions

Standard ranking/query conventions: Setup field uses `'OK'`/`''` strings (not 1/0), and Win% is stored as a decimal (multiply by 100 for display).
