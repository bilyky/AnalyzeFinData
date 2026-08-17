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

## 🛡️ 5. Autonomous Context & Logical-Reporting Safety Standards

### 🚀 The Transparent Factual Reporting & Context Safety Mandate (The Truth Standard)
To completely prevent silent logical bypasses, "lying" or misleading system-health emails, and context-based privilege failures (such as Task Scheduler runs failing silently over Windows credential/SYSTEM boundaries):

*   **The Mandate:** All autonomous reporting, backup synchronization, and background pipeline execution layers **MUST** guarantee absolute, unspeculated, and privilege-aware transparency.
*   **The Rules:**
    1.  **Strict Three-State Logical Tracking:** Bypasses, safe-skips, and warnings (such as market-hours skips, offline drives, or incomplete API loads) **MUST NEVER** be collapsed or reported as boolean `True` (SUCCESS). They must be returned and modeled as explicit, descriptive string statuses or Enums. High-level email and dashboard reporters must render yellow/orange warning badges for skips and red badges for failures, reserving green badges **only** for actual, successful execution.
    2.  **Explicit Context Privilege Verification:** Before assuming or logging that an infrastructure resource (like a NAS, server, or UNC share) is offline/disconnected because a path-existence check failed, the script **MUST** execute a direct, low-level network diagnostic check (e.g., pinging the IP address directly). If the IP is pingable, the script is strictly prohibited from claiming a "drive outage" and must explicitly report a **Local Windows User Permission/SYSTEM Context Block**.
    3.  **Headless User Context Locking:** All registered Task Scheduler tasks or cron daemons **MUST** be explicitly configured to run under your credentialed active user account context (the interactive user) rather than the local machine `SYSTEM` account to ensure they possess the necessary network share and SMB access privileges.
    4.  **Notification as the Absolute Final Step:** In any pipeline or task execution script, drafting and dispatching success notifications (emails/SMS/slack) **MUST** take place as the absolute final step of the program, after all database updates, network syncs, and backups have fully completed. Any late-stage sync or writing failure must be dynamically appended to the report and reflected loudly in the email's subject line.

