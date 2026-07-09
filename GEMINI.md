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
