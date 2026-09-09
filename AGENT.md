# 🛡️ AETHER Workspace Agent Instructions & Cognitive Standards

This file documents the foundational, workspace-wide cognitive, temporal, and software engineering standards that **any** AI agent, copilot, or model (including Gemini CLI, Claude Dev, or future routines) must strictly and deterministically follow when operating in this repository.

---

## 🧠 1. Cognitive & Factual Auditing Standards

### 🛡️ The Zero-Trust Factual Auditing Standard (Factual Verification Hook)
To completely eliminate "AI hallucinations," speculative explanations, and logical rationalizations when discussing system performance, portfolio metrics, file-content timelines, or active state files:

*   **The Mandate:** Whenever explaining WHY a transaction occurred, why a buy/sell was skipped, what is written in any JSON or log file on disk, or summarizing a chronological sequence of events:
    *   **Action 1 (Mandatory Check):** You **MUST** execute a direct Python or shell tool command in that exact turn to print and inspect the raw file content, terminal output, or database record *before* formulating your answer.
    *   **No Speculating:** You are strictly forbidden from guessing, assuming, or constructing "reconciling narratives" or speculative chronologies to explain discrepancies. If you do not possess direct, unmocked, and newly printed log lines on screen in your current turn to prove a fact, you MUST explicitly state: *"I do not have the hard data for that. Let us run a check to find out,"* and then immediately execute the audit.

### 🕒 Strict Temporal Zero-Trust (The Clock-Check First Rule)
To completely eliminate calendar, date-stamping, or virtual machine clock-lag discrepancies when analyzing portfolio performance or daily activity:

*   **The Mandate:** Whenever the user asks ANY question regarding account balances, portfolio equity, active holdings, performance progress, or daily trading status:
    *   **Action 1 (Mandatory First Step):** You **MUST** execute an empirical system clock check (e.g., running `Get-Date` via a shell tool) as the very first action in that turn.
    *   **No Exceptions:** Never guess, assume, or trust your memory or the loaded context for the current date, time, or weekday. Always verify the active system clock first before compiling any data, generating any reports, or answering any inquiries.

### The "Why" Verification & Silent-State Auditing Rule
To completely eliminate "silent logical failures" (where a script exits with success code `0` or reports `"all systems nominal"`, but a hidden edge case, hard-coded slice, or math discrepancy silently blocked executions like buying or selling):

*   **Rule of Silent Outcomes:** Whenever an automated run results in a **silent or empty action** (such as *"0 trades executed"*, *"no stocks bought"*, or *"no errors detected"*), **never accept the success status blindly.**
*   **The "Why" Audit Pass:** You must programmatically or analytically execute a deep retrospective by asking:
    *   **WHY** did this run result in zero actions?
    *   **WHY** did our highest-scoring buy candidates get skipped?
    *   **Trace the Logical Path:** Print out the raw watchlists, trace the exact row numbers, and display the final evaluated prices/scores step-by-step to mathematically prove that the "zero action" was a deliberate, correct risk-management decision, and **not** a silent code bug (like our row-50 slice or list-slicing priority blocks).
*   **Continuous Vigilance:** Trust, but verify. Treat every "silent success" with high-signal skepticism. Ensure that we logging-trace why items are rejected (e.g., logging `🛑 AI BUY REJECTED` with specific check values) so that we have an active, transparent audit trail.

---

## 💻 2. Software Engineering & Code-Style Standards

### 🚫 The No-Inline-Imports Standard
To ensure instant, static dependency-compilation checks and completely eliminate runtime path or import-hierarchy crashes (such as the `ModuleNotFoundError` on symbol detail modals):

*   **The Mandate:** All import statements (including `import` and `from ... import`) **MUST** be declared globally at the very top of the file.
*   **No Exceptions:** Function-level or conditional inline imports are **strictly forbidden** across the entire repository. This guarantees that any missing packages or broken path-routing are immediately flagged at compilation/import time during test runs, rather than lying dormant as runtime landmines.

### 🧱 Clean Root Directory Mandate
To maintain a pristine, highly portable, and professional-grade repository architecture:

*   **The Mandate:** The root directory must remain clean of auxiliary diagnostic, sync, or discovery scripts.
*   **The Architecture:** All auxiliary scripts, backtesters, debuggers, or sync tools must reside in categorized subdirectories under **`scripts/`**:
    *   `scripts/diagnostics/` (API and session diagnostics)
    *   `scripts/backtesting/` (historical backtesters and level audits)
    *   `scripts/discovery/` (stock screeners and workbook analyzers)
    *   `scripts/sync/` (data sync commands)
    *   `scripts/utils/` (general utilities)
*   **Portability Headers:** Any script under the `scripts/` directory must include standard sys-path portability headers at the top to ensure they can be executed seamlessly from any directory.

---

## 🧪 3. Quality Assurance & Testing Standards

### 🚫 No-Mocks QA Mandate
To ensure absolute reliability, data-contract integrity, and prevent "green tests, broken production" mirages:

*   **The Mandate:** Mocking frameworks, stubs, or virtual request interceptors are **strictly forbidden** inside active live-connection contract tests (specifically `tests/test_live_api_contract.py`).
*   **The Rule:** All API contract tests must make real, unmocked, and un-intercepted network requests to the production endpoints of E*TRADE and Chaikin Analytics. Testing is incomplete unless verified against the actual, live broker and database servers.

### 🚨 Strict Ban on Performative "Empty" Testing (The Value-Test Mandate)
To completely eliminate "vacuum tests" or performative mocks that pass in sterile test environments but let silent corruptions or data gaps persist on disk:

*   **The Mandate:** You are strictly forbidden from writing "empty tests" or mock-based assertions that merely check if functions were called without validating real-world data structures, dirty historical files, or production files on disk.
*   **The Rules:**
    1.  **Production-State Verifiers:** All data, pipeline, and healing tests must cover realistic edge-case states, corrupted/partial files, rate limits, and realistic production database conditions on disk.
    2.  **No "Happy Path Only" Coverage:** Mocking must never be used to mask complex, multi-day historical data gaps or timeline drifts.
    3.  **Empirical Failure First (Strict Red-Green):** Before applying any bug fix, you MUST write a reproducing test case that fails (RED) on the actual dirty state. If the test cannot fail on the broken code, the test has NO value and must be rewritten. The fix is complete only when the test successfully passes (GREEN) with zero regressions.

---

## 🧹 4. Resource Cleanup & Sanitation Standards

### 🛡️ The Auto-Clean Resource Mandate (AI-Agnostic Resource Cleanup Rule)
To completely prevent un-cleaned programmatic debris, lingering scheduled tasks, temporary active files, or database locks from leaking on your production system:

*   **The Mandate:** Any AI agent, copilot, or developer script that programmatically registers a scheduled task (using `schtasks` or PowerShell), creates temporary locking files (such as `.lock` or `.tmp`), or spawns transient test-runner environments **MUST** guarantee absolute, 100% cleanup of these physical resources upon completion or failure of their session.
*   **The Rules:**
    1.  **Atomic Teardowns:** Temporary system-level resources (like test scheduled tasks) must never be registered with calendar triggers that persist past the session. They must be constructed with immediate expiration or wrapped in atomic `try/finally` command blocks that ensure their deletion.
    2.  **No Extraneous Files:** All temporary diagnostic logs, test-state JSONs, or Excel worksheets generated during an agent's run must be cleaned up and deleted before staging/committing any files.

---

## 📁 5. Autonomous Git Branch & Merge Safety Standards

### 🚫 Strict Banning of Unprompted Merges and Swaps
To permanently prevent unprompted branch switches, branch merges, and remote force-pushes, and to guarantee that the user remains the absolute sovereign authority over git repository states:

*   **The Mandate:** The agent is **strictly forbidden** from switching branches to merge code, fast-forwarding local `main`, merging any pull requests, or executing `git push` commands to stable production branches (such as `main`) unprompted or automatically. All final merging, branch lifetime decisions, and push execution must be left exclusively to the user.
*   **The Rules:**
    1.  **Prepare and Rebase Only:** The agent's scope is strictly limited to the preparation, rebasing, linting, and testing of feature branches.
    2.  **Verify and Wait:** When a feature branch is ready and tested 100% green, the agent must stop, present the results, and wait for the user's explicit merge directive before executing any merge or push commands. Never assume approval or make fast-forward modifications on production branches automatically.

---

## 📈 6. E*TRADE Platform Capabilities & Sizing Heuristics

### Fractional Share Order Entry
E*TRADE supports fractional share order entry, allowing precise capital allocation without rounding down to the nearest whole share.
*   **Available Assets:** Most S&P 100® stocks, plus major ETFs: **DIA**, **SPY**, **QQQ**, and **IWM**.
*   **Precision:** Up to **three decimal places** (e.g., `1.458` shares).
*   **Sizing Precision:** For these optionable symbols, bypass the whole-share rounding integer constraint and allow up to three decimal places for share quantities in position-sizing modules (`risk_utils.py`, `ai_portfolio_game.py`). This allows exact target allocations (e.g., exactly 10.0% or 15.0% allocation) without holding unnecessary cash drag from whole-share rounding.

### State-Aware Persistent Profile Modes (MANUAL vs. ADAPTIVE)
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

### 🌐 Price Fetching & Fallback Hierarchy (Zero-Trust Data Rule)
To maximize data accuracy while eliminating API rate-limits, suspended sessions, and sequential network latency:
*   **The Rule:** If local workbook data (`state_of_the_day.xlsx`) is available and we are outside of active market hours (after-hours and weekends), **always** read prices directly from this local file first (near-instant 0.1-second lookup).
*   **Active Trading Hours:** During active market hours (weekdays 6:30 AM - 1:15 PM PST), bypass the static local workbook and execute the **regular live process**:
    1.  **Primary:** Query the live E*TRADE Production API for real-time streaming quotes.
    2.  **Agnostic Fallback:** If E*TRADE fails or is missing specific ticker quotes, immediately fallback to scrape Google Finance.

---

## 📊 7. Antifragile Feedback Analyzer & Failure DNA Loop

To maintain an adaptive, self-correcting quantitative trading desk, Project AETHER operates a closed-loop retrospective feedback system:
*   **Phase 1: Real-Time Buy DNA Freezing:** When the autopilot executes any BUY action (live or queued), it must capture and freeze the candidate's exact buy-state metrics (PGR rating, S10/L60 trend score, combined score, Z-score, and buy date) inside the position's record in `ai_portfolio_game.json`.
*   **Phase 2: Closed-Trade DNA Logging:** On position exits (sells), the system automatically calls `log_closed_trade_dna()` to calculate holding days, final realized P&L %, and append the completed trade details to `Data/trade_history_dna.json`.
*   **Phase 3: The Weekly Retrospective Analyzer:** Every Saturday, `retrospective_analyzer.py` must be run (manually or via task scheduler) to scan the raw trade ledger, separate successes from failures, automatically filter out market-panic days (e.g. SPY down > 2%), and run statistical clustering on true failures to isolate bad habits (e.g., buying weak, sub-5.0 combined scores).
*   **Phase 4: Dynamic Rejection Rules:** The retrospective analyzer automatically writes these toxic patterns to `Data/failure_dna_rules.json` and outputs a rich, human-readable summary in `Data/retrospective_report.txt`.
*   **Phase 5: The Autopilot Rejection Guard:** During the daily buy cycle (`_execute_buys` in `ai_portfolio_game.py`), the buy-loop must run `check_failure_rules()` on all prospective candidates, immediately rejecting any stock matching our dynamically generated toxic rules on autopilot!

---

## 🔒 8. Uncompromising Safety Locks & Direct Push Blocks

### 🚨 Strict Ban on Direct Commits/Pushes to Production Branches (The Branch-Safety Lock)
To prevent accidental direct commits, merges, or pushes to the stable `main` production branch:
*   **The Mandate:** You are **strictly and absolutely forbidden** from executing any staging (`git add`), committing (`git commit`), or pushing (`git push`) commands on the `main` or `master` branch in any repository **unless the user has explicitly written the exact phrase "main/master direct push" in that exact current turn**.
*   **The Verification Pass:** Before executing any git write or push operation, you **MUST** run a direct, unmocked shell command (such as `git branch --show-current`) in that exact turn to print the active branch on screen. If the current branch is `main` or `master`, and the user has not written the exact words "main/master direct push", you **MUST** immediately halt, print a branch-safety block, and ask the user which feature or Pull Request branch they would like you to switch to. There are absolutely no exceptions to this safety lock.

### 🛑 Strict Ban on Silent Failures, Masked Errors, & Greenwashing (Uncompromising Truth)
To guarantee 100% accurate, zero-trust system diagnostics and eliminate any lying or false safety reports:
*   **The Mandate:** All errors, exceptions, expired sessions, or bypassed locks **MUST** be programmatically captured, logged, and surfaced to the operator with complete, uncompromised truthfulness.
*   **No Greenwashing:** You are **strictly and absolutely forbidden** from masking any failure, timeout, or expired credential under a generic success badge or reporting 'PASS' when an API check was actually bypassed, failed, or waived.
*   **Explicit Labeling:** Any waived check (such as E*TRADE session validation on weekends) must be explicitly reported as **`[WAIVED]`** or **`[EXPIRED]`** on both console screens and HTML emails, never as `[PASS]`. All structural exceptions must fail loudly, instantly, and print complete traceback logs so the operator has immediate, uncompromised visibility.
