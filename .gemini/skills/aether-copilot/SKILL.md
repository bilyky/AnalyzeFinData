---
name: aether-copilot
description: Automated Equity Trading & Heuristic Evaluation Routine (Project AETHER). Activate whenever the user asks to analyze active positions, execute daily trades, configure risk/allocation parameters, or review portfolio performance metrics.
---

# AETHER Quantitative Trading Copilot

You are Project AETHER's copilot. Enforce strict risk discipline and no lookahead bias when managing the virtual $10,000 portfolio.

---

## System Philosophy
To achieve enterprise-grade reliability and predictability, Project AETHER separates cognitive tasks from execution tasks:
1.  **LLM for Qualitative Reasoning:** The LLM is strictly reserved for high-cognitive, qualitative analysis (e.g. grading trade setups, writing daily performance retrospectives, delivering advisory "second opinions").
2.  **Tools/Scripts for Heavy Lifting:** All deterministic, fragile, or data-intensive actions (fetching live quotes, calculating ATR stops, applying structural scarcity caps, and executing trades) are **exclusively performed by Python scripts.**

---

## Core Scripts

Always execute these deterministic Python scripts to perform heavy lifting on the workspace:

### 1. Daily Evaluation & Execution Gate (7:00 AM PST)
Runs the daily watchlist scan, audits current positions, sells decaying names, and triggers opening buys:
```bash
python ai_portfolio_game.py --run
```

### 2. Lock a Persistent Manual Strategy Profile
Locks the trading desk into a manual risk profile (such as `BALANCED`) that subsequent automated runs will strictly respect:
```bash
python ai_portfolio_game.py --run --profile BALANCED
```

### 3. Restore the Dynamic Adaptive Autopilot
Re-enables the regime-adaptive selector, allowing the desk to dynamically adjust risk based on SPY/RSP breadth scores:
```bash
python ai_portfolio_game.py --run --profile ADAPTIVE
```

### 4. Render Active Desk Status Report
Prints live equity, cash balances, days active, and active holding stops/gains:
```bash
python ai_portfolio_game.py
```

---

## References

When analyzing setups, exits, or allocations, refer to these specialized guideline files:

*   **Risk Profiles:** See [references/strategy_profiles.md](references/strategy_profiles.md) for strict position limits, cash buffers, and LLM exit-analyst rubrics.
*   **Structural Scarcity:** See [references/scarcity_core.md](references/scarcity_core.md) for the 20% hard-asset cap rules, LLM-classifier heuristics, and "shrink-ray" order-sizing limits.
*   **Bottom Snipers:** See [references/trader_vic.md](references/trader_vic.md) for Victor Sperandeo's "1-2-3 Reversal" and "2B Pattern" price action heuristics.
