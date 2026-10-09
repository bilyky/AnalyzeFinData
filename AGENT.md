# 🛡️ AETHER Workspace Agent Instructions & Cognitive Standards

This file documents the foundational, workspace-wide cognitive, temporal, and software engineering standards that **any** AI agent, copilot, or model (including Gemini CLI, Claude Dev, or future routines) must strictly and deterministically follow when operating in this repository.

---

## 🧠 1. Cognitive & Factual Auditing Standards

### 🛡️ The Zero-Trust Factual Auditing Standard (Factual Verification Hook)
*   **The Mandate:** Whenever explaining WHY a transaction occurred, why a buy/sell was skipped, what is written in any JSON or log file on disk, or summarizing a chronological sequence of events:
    *   **Action 1 (Mandatory Check):** You **MUST** execute a direct Python or shell tool command in that exact turn to print and inspect the raw file content, terminal output, or database record *before* formulating your answer.
    *   **No Speculating:** You are strictly forbidden from guessing, assuming, or constructing "reconciling narratives" or speculative chronologies to explain discrepancies. If you do not possess direct, unmocked, and newly printed log lines on screen in your current turn to prove a fact, you MUST explicitly state: *"I do not have the hard data for that. Let us run a check to find out,"* and then immediately execute the audit.
    *   **Strict Ban on Speculative Infrastructure Blame:** You are strictly forbidden from attributing any process hang, timeout, or script failure to external infrastructure (such as "Chaikin server outages", "E*TRADE API downtime", or "network blockages") unless you have executed a direct connection test (e.g. `curl` or `ping`) or printed raw log lines in that exact turn showing that specific network or server error code. If a timeout occurs, you must attribute it strictly to local script/process delays, selectors changes, or automation locks until proven otherwise.

### 🕒 Strict Temporal Zero-Trust (The Clock-Check First Rule)
*   **The Mandate:** Whenever the user asks ANY question regarding account balances, portfolio equity, active holdings, performance progress, or daily trading status:
    *   **Action 1 (Mandatory First Step):** You **MUST** execute an empirical system clock check (e.g., running `Get-Date` via a shell tool) as the very first action in that turn.
    *   **No Exceptions:** Never guess, assume, or trust your memory or the loaded context for the current date, time, or weekday. Always verify the active system clock first before compiling any data, generating any reports, or answering any inquiries.

### The "Why" Verification & Silent-State Auditing Rule
*   **Rule of Silent Outcomes:** Whenever an automated run results in a **silent or empty action** (such as *"0 trades executed"*, *"no stocks bought"*, or *"no errors detected"*), **never accept the success status blindly.**
*   **The "Why" Audit Pass:** You must programmatically or analytically execute a deep retrospective by asking:
    *   **WHY** did this run result in zero actions?
    *   **WHY** did our highest-scoring buy candidates get skipped?
    *   **Trace the Logical Path:** Print out the raw watchlists, trace the exact row numbers, and display the final evaluated prices/scores step-by-step to mathematically prove that the "zero action" was a deliberate, correct risk-management decision, and **not** a silent code bug (like our row-50 slice or list-slicing priority blocks).
*   **Continuous Vigilance:** Trust, but verify. Treat every "silent success" with high-signal skepticism. Ensure that we logging-trace why items are rejected (e.g., logging `🛑 AI BUY REJECTED` with specific check values) so that we have an active, transparent audit trail.

---

## 💻 2. Software Engineering & Code-Style Standards

### 🚫 The No-Inline-Imports Standard
*   **The Mandate:** All import statements (including `import` and `from ... import`) **MUST** be declared globally at the very top of the file.
*   **No Exceptions:** Function-level or conditional inline imports are **strictly forbidden** across the entire repository. This guarantees that any missing packages or broken path-routing are immediately flagged at compilation/import time during test runs, rather than lying dormant as runtime landmines.

### 🧱 Clean Root Directory Mandate
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
*   **The Mandate:** Mocking frameworks, stubs, or virtual request interceptors are **strictly forbidden** inside active live-connection contract tests (specifically `tests/test_live_api_contract.py`).
*   **The Rule:** All API contract tests must make real, unmocked, and un-intercepted network requests to the production endpoints of E*TRADE and Chaikin Analytics. Testing is incomplete unless verified against the actual, live broker and database servers.

### 🚨 Strict Ban on Performative "Empty" Testing (The Value-Test Mandate)
*   **The Mandate:** You are strictly forbidden from writing "empty tests" or mock-based assertions that merely check if functions were called without validating real-world data structures, dirty historical files, or production files on disk.
*   **The Rules:**
    1.  **Production-State Verifiers:** All data, pipeline, and healing tests must cover realistic edge-case states, corrupted/partial files, rate limits, and realistic production database conditions on disk.
    2.  **No "Happy Path Only" Coverage:** Mocking must never be used to mask complex, multi-day historical data gaps or timeline drifts.
    3.  **Empirical Failure First (Strict Red-Green):** Before applying any bug fix, you MUST write a reproducing test case that fails (RED) on the actual dirty state. If the test cannot fail on the broken code, the test has NO value and must be rewritten. The fix is complete only when the test successfully passes (GREEN) with zero regressions. For a **new feature**, the tests defining its input/output contract are written first and fail (RED) before the feature is implemented and passes (GREEN).
*   **The Hybrid Testing Standard (all tests must have value):**
    1.  **Pure mathematical & logical tests:** core trend calculations (Short10, Long60), Peter Lynch soft-exit reviews, ATR stop-loss sizing, and Victor Sperandeo bottom reversals are tested **deterministically with zero mocks** on real pricing arrays.
    2.  **Live-network API contract tests:** network, credential, and API-key integrations are validated by real, unmocked contract tests (`tests/test_live_api_contract.py`) that query the Chaikin and E*TRADE production endpoints (live mode only, see the hermetic harness below).
    3.  **Executable smoke tests:** standalone entry-point scripts (e.g. `run_history.py`, `daily_task.py`) are covered by smoke tests proving they import and have compatible signatures.

### 🧪 Hermetic Test Harness (no production side effects)
*   **The Mandate:** A test run — including `python -m unittest discover -s tests` from the main checkout, whose `Data/` *is* production's — must never write production files (game state, ledgers, workbook, tokens, logs, market-data caches), contact a live host, open a real browser, kill a process, or change Task Scheduler. `tests/__init__.py` enforces it (temp redirects; blocked sockets, browsers and process kills outside live mode), pinned by `tests/test_log_hermetic.py` and `tests/test_ledger_hermetic.py`. Full list: [docs/agent-reference.md §A](docs/agent-reference.md).
*   **The Rules:**
    1.  **Mock `subprocess`** when the code under test spawns processes: a child never loads the harness.
    2.  **A new module-level `Data/` path** (or a handler opened at import) gets its redirect in `tests/__init__.py` in the same change.
    3.  **After a harness change, prove it in the main checkout:** hash the repo and `Data/` before and after a full run (0 changed / 0 created / 0 deleted). A worktree's `Data/` is nearly empty, so plant probe files there.
    4.  **Only `AETHER_LIVE_TESTS=1`** lifts the guards, for the live contract tests.

---

## 🧹 4. Resource Cleanup & Sanitation Standards

### 🛡️ The Auto-Clean Resource Mandate (AI-Agnostic Resource Cleanup Rule)
*   **The Mandate:** Any AI agent, copilot, or developer script that programmatically registers a scheduled task (using `schtasks` or PowerShell), creates temporary locking files (such as `.lock` or `.tmp`), or spawns transient test-runner environments **MUST** guarantee absolute, 100% cleanup of these physical resources upon completion or failure of their session.
*   **The Rules:**
    1.  **Atomic Teardowns:** Temporary system-level resources (like test scheduled tasks) must never be registered with calendar triggers that persist past the session. They must be constructed with immediate expiration or wrapped in atomic `try/finally` command blocks that ensure their deletion.
    2.  **No Extraneous Files:** All temporary diagnostic logs, test-state JSONs, or Excel worksheets generated during an agent's run must be cleaned up and deleted before staging/committing any files.

### 🔗 No Links Into Data/ (Worktree Junction Wipe Rule)
*   **The Mandate:** Never junction or symlink production `Data/`, or anything inside it, into a worktree or any other folder. Removing the worktree deletes *through* the link: on 2026-10-06 that lost 551,974 cache files. Detail: [docs/agent-reference.md §B](docs/agent-reference.md).
*   **The Rules:**
    1.  **Reach the real caches with `AETHER_CACHE_DIR=<main checkout>/Data`** (caches only), **never `AETHER_DATA_DIR`** (it also moves the E*TRADE token and the trash). It grants writes, and not every script honors it (list in §B).
    2.  **An unavoidable link:** say so in the PR, and remove the link itself (`cmd /c rmdir <link>` / `unlink <link>`) before you finish.
    3.  **Scan every worktree for links before removing it;** never remove one that still has a link (`prune_merged_worktrees.py` enforces this).
    4.  **Before a batch cleanup, note the `Data/Symbol*` entry counts** and compare after. After an intentional shrink, delete `Data/data_sentinel.json` so the watchdog takes the new size as its baseline.

---

## 📁 5. Autonomous Git Branch & Merge Safety Standards

### 🚫 Strict Banning of Unprompted Merges and Swaps
To permanently prevent unprompted branch switches, branch merges, and remote force-pushes, and to guarantee that the user remains the absolute sovereign authority over git repository states:

*   **The Mandate:** The agent is **strictly forbidden** from switching branches to merge code, fast-forwarding local `main`, merging any pull requests, or executing `git push` commands to stable production branches (such as `main`) unprompted or automatically. All final merging, branch lifetime decisions, and push execution must be left exclusively to the user.
*   **The Rules:**
    1.  **Prepare and Rebase Only:** The agent's scope is strictly limited to the preparation, rebasing, linting, and testing of feature branches.
    2.  **Verify and Wait:** When a feature branch is ready and tested 100% green, the agent must stop, present the results, and wait for the user's explicit merge directive before executing any merge or push commands. Never assume approval or make fast-forward modifications on production branches automatically.

---

## 📈 6. Runtime Behavior Reference

How the trading system itself behaves (E*TRADE fractional sizing, MANUAL/ADAPTIVE profile modes,
the cash-deployment upgrade gate, the two-factor market-hours check, the price-fetch fallback
hierarchy) lives in `AETHER_REFERENCE.md` §9. Read it before changing that code.

---

## 📊 7. Antifragile Feedback Analyzer & Failure DNA Loop

The closed-loop retrospective (buy-DNA freezing, closed-trade DNA logging, the weekly analyzer,
dynamic rejection rules, the buy-time rejection guard) is documented in `AETHER_REFERENCE.md` §9.

---

## 🔒 8. Uncompromising Safety Locks & Direct Push Blocks

### 🚨 Strict Ban on Direct Commits/Pushes to Production Branches (The Branch-Safety Lock)
*   **The Mandate:** You are **strictly and absolutely forbidden** from executing any staging (`git add`), committing (`git commit`), or pushing (`git push`) commands on the `main` or `master` branch in any repository **unless the user has explicitly written the exact phrase "main/master direct push" in that exact current turn**.
*   **The Verification Pass:** Before executing any git write or push operation, you **MUST** run a direct, unmocked shell command (such as `git branch --show-current`) in that exact turn to print the active branch on screen. If the current branch is `main` or `master`, and the user has not written the exact words "main/master direct push", you **MUST** immediately halt, print a branch-safety block, and ask the user which feature or Pull Request branch they would like you to switch to. There are absolutely no exceptions to this safety lock.

### 🔐 Never Print Secrets (Config Inspection Rule)
*   **The Mandate:** When reading a config, credential or `.env` file, never print a secret value — not in full, not a prefix, not a "form" preview. Name-based masking keeps missing fields (nested dicts, literal keys stored where an `env:VAR` reference was expected); one review leaked an API key and broker consumer credentials that way.
*   **The Rule:** Mask **deny-by-default**: walk the whole structure and replace every string with `set` / `empty`, except an explicit allowlist of known-safe keys (names, flags, hosts, ports, model ids). Compare secrets only as booleans (`a == b`, `startswith('env:')`) or lengths. If a value was printed anyway, say so immediately and name what to rotate.

### 🛑 Strict Ban on Stale-Data Fallbacks & Stale Trading (The Live-Data Only Mandate)
*   **The Mandate:** You are **strictly and absolutely forbidden** from implementing, suggesting, or deploying any automated "historical cache fallbacks" or offline trading loops inside Chaikin or E*TRADE modules. If live fetching of Chaikin ratings or E*TRADE pricing fails, the system **MUST fail loudly, crash immediately, and trigger emergency alerts** to a human operator.
*   **Zero Silent Decay:** Never attempt to "smooth over" a network or authentication failure by silently loading stale cached files or decaying ratings. It is infinitely safer for the desk to do nothing and halt than to execute trades on stale/fake market data.

### 🛑 Strict Ban on Silent Failures, Masked Errors, & Greenwashing (Uncompromising Truth)
*   **The Mandate:** All errors, exceptions, expired sessions, or bypassed locks **MUST** be programmatically captured, logged, and surfaced to the operator with complete, uncompromised truthfulness.
*   **No Greenwashing:** You are **strictly and absolutely forbidden** from masking any failure, timeout, or expired credential under a generic success badge or reporting 'PASS' when an API check was actually bypassed, failed, or waived.
*   **Explicit Labeling:** Any waived check (such as E*TRADE session validation on weekends) must be explicitly reported as **`[WAIVED]`** or **`[EXPIRED]`** on both console screens and HTML emails, never as `[PASS]`. All structural exceptions must fail loudly, instantly, and print complete traceback logs so the operator has immediate, uncompromised visibility.

---

## 🧰 9. Reusable Agent Workflows (Skills)

Step-by-step workflows live as plain markdown in `.claude/commands/<name>.md`. They are agent-agnostic: Claude Code runs them as `/<name>`; any other agent (Gemini, Codex, pi, …) opens the file and follows it. Supporting reference material lives under `docs/skills/` and is read only when a skill points to it.

*   `review-prs` — review open PRs against the fixed rubric and post findings as PR comments; includes the merge-in and lossless-cleanup procedures.
*   `ship-pr` — take a change from branch to pushed PR with CI verified.
*   `run-study` — backtest-first evidence before wiring, loosening or removing a rule: fixed decision rule, independence-aware statistics, verdict tests, result recorded in `plans/roadmap.md`.
*   `status`, `analyze`, `compare-stocks`, `daily-run`, `intraday-monitor`, `watchdog`, `pattern-discover`, `extract-intel`, `oceanview-adviser` — portfolio and operations workflows (see each file's header).

Two skills use the `SKILL.md` frontmatter layout instead (`name` + `description`, then the body); any agent can open them directly:

*   `.gemini/skills/aether-copilot/SKILL.md` (+ `references/`) — portfolio copilot rules: positions, daily trades, risk/allocation parameters.
*   `.skills/aether-documentation-sentry/SKILL.md` — documentation parity and About/wiki drift guard.
