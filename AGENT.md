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
