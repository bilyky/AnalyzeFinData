# 🛡️ Project AETHER Instructions & Conventions

This file documents team-shared conventions, platform capabilities, and repo-wide workflows for Project AETHER.

---

## 📈 E*TRADE Platform Capabilities

### Fractional Share Order Entry
E*TRADE now supports fractional share order entry, allowing precise capital allocation without rounding down to the nearest whole share.

*   **Available Assets:** Most S&P 100® stocks, plus major ETFs: **DIA**, **SPY**, **QQQ**, and **IWM**.
*   **Precision:** Up to **three decimal places** (e.g., `1.458` shares).
*   **Expansion Roadmap:** Additional symbols are planned, and dollar-based fractional orders are scheduled for later this year.

### 🛠️ Future Implementation Guidance
When modifying position-sizing modules (specifically `risk_utils.py` and `ai_portfolio_game.py`):
1.  **Check Capability:** Verify if the target symbol belongs to the S&P 100, DIA, SPY, QQQ, or IWM.
2.  **Sizing Precision:** For these symbols, bypass the whole-share rounding integer constraint and allow up to three decimal places for share quantities.
3.  **Capital Efficiency:** This will allow the portfolio to achieve exact target allocations (e.g., exactly 10.0% or 15.0% allocation) without holding unnecessary cash drag from whole-share rounding.

---

## 🧠 Cognitive & Engineering Standards

### The "Why" Verification & Silent-State Auditing Rule
To completely eliminate "silent logical failures" (where a script exits with success code `0` or reports `"all systems nominal"`, but a hidden edge case, hard-coded slice, or math discrepancy silently blocked executions like buying or selling):

*   **Rule of Silent Outcomes:** Whenever an automated run results in a **silent or empty action** (such as *"0 trades executed"*, *"no stocks bought"*, or *"no errors detected"*), **never accept the success status blindly.**
*   **The "Why" Audit Pass:** You must programmatically or analytically execute a deep retrospective by asking:
    *   **WHY** did this run result in zero actions?
    *   **WHY** did our highest-scoring buy candidates get skipped?
    *   **Trace the Logical Path:** Print out the raw watchlists, trace the exact row numbers, and display the final evaluated prices/scores step-by-step to mathematically prove that the "zero action" was a deliberate, correct risk-management decision, and **not** a silent code bug (like our row-50 slice or list-slicing priority blocks).
*   **Continuous Vigilance:** Trust, but verify. Treat every "silent success" with high-signal skepticism. Ensure that we logging-trace why items are rejected (e.g., logging `🛑 AI BUY REJECTED` with specific check values) so that we have an active, transparent audit trail.

### 🕒 Strict Temporal Zero-Trust (The Clock-Check First Rule)
To completely eliminate calendar, date-stamping, or virtual machine clock-lag discrepancies when analyzing portfolio performance:

*   **The Mandate:** Whenever the user asks ANY question regarding account balances, portfolio equity, active holdings, performance progress, or daily trading status:
    *   **Action 1 (Mandatory First Step):** You **MUST** execute an empirical system clock check (e.g., running `Get-Date` via a shell tool) as the very first action in that turn.
    *   **No Exceptions:** Never guess, assume, or trust your memory or the loaded context for the current date, time, or weekday. Always verify the active system clock first before compiling any data, generating any reports, or answering any inquiries.

### 🌐 Price Fetching & Fallback Hierarchy (Zero-Trust Data Rule)
To maximize data accuracy while eliminating API rate-limits, suspended sessions, and sequential network latency:

*   **The Rule:** If local workbook data (`state_of_the_day.xlsx`) is available and we are outside of active market hours (after-hours and weekends), **always** read prices directly from this local file first (near-instant 0.1-second lookup).
*   **Active Trading Hours:** During active market hours (weekdays 6:30 AM - 1:15 PM PST), bypass the static local workbook and execute the **regular live process**:
    1.  **Primary:** Query the live E*TRADE Production API for real-time streaming quotes.
    2.  **Agnostic Fallback:** If E*TRADE fails or is missing specific ticker quotes, immediately fallback to scrape Google Finance.

### 🛡️ State-Aware Persistent Profile Modes (MANUAL vs. ADAPTIVE)
To guarantee predictability across automated daily executions:
*   **One-Time Tactical Override:** Executing a manual run with an explicit `--profile <PROFILE>` CLI parameter sets the system state to `"profile_mode": "MANUAL"`. This overrides the autopilot for *that specific session only*.
*   **Automatic Autopilot Restoration:** Subsequent automated daily tasks (which run with no CLI parameters) will **automatically detect the manual override state, print an auto-reset warning, and restore the `"profile_mode": "ADAPTIVE"` autopilot**, safely falling back to the dynamic regime selector with zero manual intervention required.
*   **Manual Restoration:** To manually restore the autopilot immediately at any time, execute a run with `--profile ADAPTIVE`. This restores `"profile_mode": "ADAPTIVE"`, enabling the dynamic regime selectors for that session.

### ⚙️ Adaptive Cash-Deployment Upgrade Gate (Capital Efficiency Rule)
To prevent the portfolio from holding excessive, non-productive cash buffer during high-conviction bottoming opportunities:
*   **The Trigger:** When on autopilot (`ADAPTIVE` mode) and the market regime evaluates to `DEFENSIVE`, the system automatically audits your local state:
    1.  **Cash Check:** Is your cash balance greater than **40.0%** of your total portfolio equity?
    2.  **Setup Check:** Do we detect **2 or more strong, safe, and verified bottom setups** (`Setup == 1`, combined momentum score `>= 9.5`, and 500-day Z-Score `< 2.5` to avoid bubble-chasing)?
*   **The Action:** If both conditions are met, the autopilot **automatically upgrades today's strategy profile from DEFENSIVE to BALANCED for this daily session**, opening up 2 additional slots to deploy idle cash safely.

### 🕒 Two-Factor Dynamic Market Hours Check (Stale-Price Prevention)
To completely prevent executing orders on stale weekend or holiday prices:
*   **The Check:** Before proceeding to execute any trades, the system performs a **two-factor live validation**:
    1.  **Official Clock:** Pings E*TRADE's `/v1/market/clock.json` to verify `currentStatus == "REGULAR"`.
    2.  **Empirical SPY Ticker:** Pings a live quote for the `SPY` ETF and converts its `dateTimeUTC` to NY Time. If the last trade did NOT occur today, the market is treated as closed (Holiday/Weekend).
*   **Dynamic Fallback:** If the network or E*TRADE API is offline, the check seamlessly falls back to our local weekend and static NYSE holiday filters, ensuring the system remains indestructible and never blocks.

### 🧠 The Custom `aether-copilot` Workspace Agent Skill
This repository has been augmented with a custom **Gemini CLI Agent Skill** located at `.gemini/skills/aether-copilot`. 
*   **Core Philosophy:** The skill strictly enforces that **the LLM is only used for qualitative reasoning and decision-making** (grading setups, exit second-opinions, and summaries), while **all heavy lifting (fetches, sizing, and trading) is handled deterministically by Python scripts.**
*   **Activation:** To load the skill into your interactive Gemini CLI session, you must manually run `/skills reload`. Verify it is active by running `/skills list`.

### 🧪 The Rigorous No-Fake-Testing & Red-Green Mandate (TDD & Quality Rules)
To maintain the high technical integrity of Project AETHER and prevent the introduction of "fake", useless, or fragile tests:

*   **Absolute Ban on Fake Mocking:** Writing tests that do nothing but assert that a mocked network request, API client, or server endpoint was called is **strictly prohibited**. Mocking must never be used to mask actual API behaviors or create a false sense of test-coverage security.
*   **The Hybrid Testing Standard (All Tests Must Have Value):**
    1.  **Pure Mathematical & Logical Tests:** Core trend calculations (Short10, Long60), Peter Lynch soft exit reviews, ATR stop-loss sizing, and Victor Sperandeo bottom reversals must be tested **deterministically with zero mocks** to guarantee absolute mathematical correctness on real pricing arrays.
    2.  **Live-Network API Contract Tests:** All network, credentials, and API-key integrations must be validated by **real-world unmocked contract tests** (like `tests/test_live_api_contract.py`). They must actively query Chaikin and E*TRADE production endpoints once to guarantee that connections are 100% online, authorized, and unblocked by firewalls.
    3.  **Executable Compilation Smoke Tests:** Standalone entry-point scripts (like `run_history.py` or `daily_task.py`) must be validated by automated smoke tests to guarantee they compile and have perfect type and signature compatibility.
*   **The Strict Red-Green TDD Mandate:** 
    *   **Bug Fixes:** For every reported bug, a reproducing test case must be authored first. This test case **must fail (RED) on the broken codebase** to empirically prove the failure condition, before any code fix is applied. The fix is only complete when the test case successfully passes (**GREEN**) with zero regressions.
    *   **New Features:** For any new features, tests defining the expected input/output contract must be written and fail (**RED**) first, before the feature is implemented and passes (**GREEN**).

### 🧠 The Antifragile Feedback Analyzer & Failure DNA Loop (Self-Learning Rules)
To maintain an adaptive, self-correcting quantitative trading desk, Project AETHER operates a closed-loop retrospective feedback system:

*   **Phase 1: Real-Time Buy DNA Freezing:** When the autopilot executes any BUY action (live or queued), it must capture and freeze the candidate's exact buy-state metrics (PGR rating, S10/L60 trend score, combined score, Z-score, and buy date) inside the position's record in `ai_portfolio_game.json`.
*   **Phase 2: Closed-Trade DNA Logging:** On position exits (sells), the system automatically calls `log_closed_trade_dna()` to calculate holding days, final realized P&L %, and append the completed trade details to `Data/trade_history_dna.json`.
*   **Phase 3: The Weekly Retrospective Analyzer:** Every Saturday, `retrospective_analyzer.py` must be run (manually or via task scheduler) to scan the raw trade ledger, separate successes from failures, automatically filter out market-panic days (e.g. SPY down > 2%), and run statistical clustering on true failures to isolate bad habits (e.g., buying weak, sub-5.0 combined scores).
*   **Phase 4: Dynamic Rejection Rules:** The retrospective analyzer automatically writes these toxic patterns to `Data/failure_dna_rules.json` and outputs a rich, human-readable summary in `Data/retrospective_report.txt`.
*   **Phase 5: The Autopilot Rejection Guard:** During the daily buy cycle (`_execute_buys` in `ai_portfolio_game.py`), the buy-loop must run `check_failure_rules()` on all prospective candidates, immediately rejecting any stock matching our dynamically generated toxic rules on autopilot!

