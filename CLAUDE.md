# AnalyzeFinData — Claude Instructions

## General Principles

*   **Rule of Zero Trust:** NEVER trust an initial data point or assumption. ALWAYS verify by checking file timestamps, reading raw JSON cache, or cross-referencing E*TRADE quotes before raising doubts or making moves.
*   **Rule of Temporal Zero-Trust:** NEVER assume or guess the current date or time. Before ANY time-dependent operation (checking market hours, verifying file freshness, or generating reports), the system MUST execute an empirical clock check via a shell tool first. Never trust memory for temporal state.
*   **Mandatory Backup Policy:** NEVER run a script or modify a file without a verified backup. All core state files (`ai_portfolio_game.json`, `state_of_the_day.xlsx`, `ops_and_rd_tracker.xlsx`) must be cloned to a timestamped backup directory (`Data/Backup/`) before any write is executed.
*   **Empirical Verification:** Before recommending an action (EXIT/BUY), the system must backtrack: check the last 3 times a similar technical setup occurred for that symbol in the local cache to verify its historical hit rate.
*   **Self-Correction:** If the system identifies a discrepancy (e.g., live price vs. OHLCV close), it must automatically log the correction and re-calculate dependent scores.
*   **Rule of Loss Minimization:** NEVER prioritize returns over risk. Capital preservation is the absolute priority. The downside on any single trade must be strictly capped using dynamic ATR stop-losses, and the portfolio must maintain a high defensive cash cushion (at least 50%) during broad market corrections (SPY L60 < -2).

## Saturday R&D Roadmap (Next Sessions)

*   **Session: Saturday, July 4, 2026 (10:00 AM PST) — Jeremy Grantham & E*TRADE Alerts API:**
    1.  *The 2.5-Sigma Bubble Detector:* Build a technical filter that calculates how far SPY or sector ETFs are trading relative to their 500-day mean, blacklisting any asset class in the "Super-Bubble" zone (> 2.5 standard deviations above mean).
    2.  *Structural Scarcity Core:* Create a dedicated, long-term 20% portfolio allocation cap reserved strictly for metals, agriculture, and grid utility assets (e.g., FCX, RS, EIX) to ride Grantham's secular "Hard Asset Supercycle."
    3.  *E*TRADE Alerts API Integration:* Research and prototype a headless client for E*TRADE's native Alerts API (`GET /v1/user/alerts`) to dynamically capture "Power Inflow" and broker order-flow events (like today's INTC alert) directly from the broker in real-time.
    4.  *The Intraday Sentinel Trigger:* Code the real-time event-driven loop to parse these broker alerts every 15 minutes, run technical bottom checks, and instantly execute buy orders inside the active session.
    5.  *The Real-Account Shadow Copilot:* Design an automated, isolated daily risk-auditor (`real_copilot.py`). It will headlessly scan the user's 44 actual E*TRADE holdings every evening, cross-reference our fresh technical sheets, and automatically email precise "Actionable Trade Tickets" (BUY/SELL recommendations with ATR stop levels) to their inbox, keeping real-money risk perfectly optimized without giving the AI raw trade execution access.

*   **Session: Saturday, July 11, 2026 (10:00 AM PST) — Peter Lynch & Portfolio Gardening:**
    1.  *The "Flower Protection" Trailing Stop:* Research dynamic trailing stops to let our winning "flowers" run. Instead of selling immediately on a short-term score drop, allow high-conviction positions (with Bullish/Very Bullish PGR) to be held as long as they remain above their 50-day moving average.
    2.  *The "Weed Cutter" (Breaking the Disposition Effect):* Code strict, non-negotiable risk rules that force the immediate liquidation of any losing "weed" position that breaches support, completely eliminating the human emotional trap of "waiting to get back to even."
    3.  *The "Lynch Local Edge" Filter:* Integrate fundamental metrics that track consumer-footprint strength, transaction growth, and operational inventory cycles to identify undervalued consumer/retail companies before Wall Street notices them.

## Workflow Conventions

When the user asks to "send", "push", "create", or "save" something (e.g., Gmail draft, commit, file), execute the full action — do not just preview or show content for review unless explicitly asked.

## Excel / openpyxl

For Excel/openpyxl work: when a fix is claimed to be working, verify by actually reopening the file (or simulating the reopen path) and checking the warning/state is gone before reporting success.

Before telling the user a fix works, re-run the exact reproduction steps the user would take (reopen the file, re-execute the script, re-query) and paste the output proving the issue is resolved. If you cannot verify end-to-end, say so explicitly.

## Project Conventions

Standard ranking/query conventions: Setup field uses `'OK'`/`''` strings (not 1/0), and Win% is stored as a decimal (multiply by 100 for display).
