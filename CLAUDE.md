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
    4.  *Trader Vic's Reversal Engines:* Code Victor Sperandeo's legendary "1-2-3 Reversal" (trendline break + higher low + peak breakout) and the "2B Reversal Pattern" (the classic bear-trap liquidity reclaim) as our advanced, real-time price action filters to capture the absolute physical bottoms of our target "flowers."
    5.  *The "Non-Correlated Satellite" Engine:* Develop a dynamic allocation bypass (`satellite_slot.py`). If the market is Defensive, but AETHER detects an elite setup in a low-beta, non-correlated sector (specifically Biotechnology or Defense), the system automatically expands our position limits from 3 to 4 slots to actively capture non-correlated alpha.
    6.  *The SPY-RSP Breadth Divergence Filter:* Build a macro-regime guard that calculates the delta between SPY (Cap-Weighted) and RSP (Equal-Weighted S&P 500). If `SPY_Score - RSP_Score > 4.0` (representing extreme, fragile cap-weight tech concentration), the system will automatically downgrade our strategy profile by one level (e.g. from Balanced to Defensive) to protect our capital from bad market breadth.

*   **Session: Saturday, July 18, 2026 (10:00 AM PST) — The Autonomic Self-Tuning Optimizer & Retrospective:**
    1.  *The Backtest Parser:* Code a headless parser inside `calibrate_model.py` that executes `backtest_ratings.py` programmatically, extracts the 10-day forward return spreads for all technical and pattern factors, and calculates the optimal weights using normalized spread ratio distributions.
    2.  *The Dynamic Code-Writer:* Build a safe, self-contained file-writing routine that opens `scoring.py` and `patterns.py`, locates the designated central weight configurations (marked by developer anchor comments), and programmatically rewrites the active coefficients with the newly optimized values.
    3.  *The Automated QA Gate:* Program the calibration loop to instantly run the full unit testing suite (`python -m unittest discover tests`) after any weight adjustment to ensure 100% code compilation and system stability.
    4.  *The Calibration Audit Email:* Integrate a notification routine that emails a detailed HTML report to the user, displaying the old weights, the newly optimized weights, the statistical alpha improvements, and a green "QA Passed" confirmation badge.
    5.  *The Post-Mortem Retrospective & Antifragile Vulnerability Ledger:* Code a post-mortem diagnostic parser (`retrospective.py`). Whenever a trade is closed at a loss, it will automatically backtrack the historical state of the day of that entry, analyze the exact combination of technical scores, OB/OS, and candlestick patterns, log the "Failure DNA" to a local, ignored `Data/vulnerability_ledger.json`, and instruct our Optimizer to automatically penalize those specific fragile setups on future runs.

*   **Session: Saturday, July 25, 2026 (10:00 AM PST) — "Right Kind of Wrong" (Amy Edmondson): Failing Well by Design:**
    Research the FT/Schroders 2023 Business Book of the Year — Amy Edmondson's *Right Kind of Wrong: The Science of Failing Well* — and apply its failure taxonomy to how AETHER learns from losses. Core idea: **not all losses are equal; prevent the preventable, welcome the productive.** Three archetypes:
    - **Basic failure** — preventable, in familiar territory, a deviation from a known standard.
    - **Complex failure** — a "perfect storm" of several causes aligning in familiar territory.
    - **Intelligent failure** — the "right kind of wrong": a hypothesis-driven bet in *new* territory, adequately prepared, and **as small as possible**, whose loss buys real information.

    Application principles:
    1.  *The Failure-Type Classifier:* Extend the Jul-18 Vulnerability Ledger / `decision_eval` scorecard so every closed loss is tagged **basic / complex / intelligent**, not just "loss." Basic = the preventable ones (sold a winner on stale data à la ANET/RPD, ignored/mis-set an ATR stop, traded on a stale workbook, bypassed the gap guard, any data-integrity violation). Complex = multi-cause market storms (e.g. the Jul-8 Iran + oil-spike + Fed-hawkish day). Intelligent = a well-reasoned new-setup bet, sized small, that simply didn't pan out.
    2.  *Drive Basic Failures to Zero:* Basic failures are the only kind the Optimizer should penalize hard — they represent broken process, and the system must learn to never repeat them (this is exactly what the winner-protection + enforced-stop work in `sell_rules.py` already started).
    3.  *Do NOT Over-Penalize Intelligent Failures:* Critical nuance / guardrail — the Jul-18 optimizer's "penalize fragile setups" reflex must **exempt intelligent failures**, or the system slowly becomes over-conservative and stops discovering new alpha ("fail fast" done blindly is a trap Edmondson explicitly warns against). Treat them as paid-for information, not defects.
    4.  *The Intelligent-Failure Budget:* Reserve a small, explicit slice of capital for hypothesis-driven experimental entries (new patterns/sectors), sized "as small as possible," tracked separately as experiments — expected to fail often but cheaply, generating learning without threatening capital preservation.
    5.  *Blameless, Data-Driven Post-Mortem:* Frame the reflection/retrospective process as blameless and evidence-based (reflect on the *pattern* and its Failure DNA, never a single "bad call") — the decision log + scorecard is the objective record. Mitigate complex failures structurally (cash buffer, diversification, breadth filter) rather than blaming any one signal.
    6.  *Reflect on EVERY Close, Not Just Losses (Outcome ≠ Decision Quality):* The retrospective must run on **every** closed position — gains included — because a profitable exit can be a *bigger* failure than a clean loss. Two cases to flag explicitly: (a) **Opportunity-cost / sold-too-early** — booking RPD at +63% that then runs to +120% is a large failure disguised as a win; the `decision_eval` "missed-upside" metric already measures this and it must count against the process. (b) **Right outcome, wrong process (lucky win)** — a gain produced by a rule that shouldn't have fired is a *process* failure to correct, not a success to repeat. Score decisions by their quality at decision time, never by the P&L sign of the result. This is the flip side of the winner-protection lesson (see the `dont-sell-winners` reflection): a "win" that dumps a flower is still a mistake.

## Workflow Conventions

When the user asks to "send", "push", "create", or "save" something (e.g., Gmail draft, commit, file), execute the full action — do not just preview or show content for review unless explicitly asked.

## Excel / openpyxl

For Excel/openpyxl work: when a fix is claimed to be working, verify by actually reopening the file (or simulating the reopen path) and checking the warning/state is gone before reporting success.

Before telling the user a fix works, re-run the exact reproduction steps the user would take (reopen the file, re-execute the script, re-query) and paste the output proving the issue is resolved. If you cannot verify end-to-end, say so explicitly.

## Project Conventions

Standard ranking/query conventions: Setup field uses `'OK'`/`''` strings (not 1/0), and Win% is stored as a decimal (multiply by 100 for display).
