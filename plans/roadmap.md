# 🔬 Project AETHER R&D Roadmap & Lab Agenda

The active experimental backlog, scoring studies, and structural risk-mitigation agenda for the AETHER quantitative desk:

## Active Agenda Items:

1.  **Autonomic Self-Tuning Optimizer (`calibrate_model.py`):** Backtests past watchlist signals to dynamically calibrate and optimize indicator weights (detailed architecture mapped in [plans/calibrate-model.md](plans/calibrate-model.md)).
2.  **Systemic Crash "Circuit Breaker" (Systemic Risk Gate):** Automated market panic detector (SPY down > 2% / VIX > 30) to freeze buying and tighten stops (detailed architecture mapped in [plans/circuit-breaker.md](plans/circuit-breaker.md)).
3.  **Global Sector Breadth "Heatmap":** Maps S&P 500 sectors above 50-SMA to block buys in decaying sector roll-overs.
4.  **Benner Cycle Macro-Temporal Filter:** Integrates Samuel Benner's 1875 long-term cycle time-anchors into autopilot risk engines.
5.  **Moonshot AI (Kimi) Failover:** Integrates OpenAI-compatible, high-capacity deep reasoning API failover alternatives.
6.  **Peter Lynch Categorization Engine:** Classifies watchlist stocks into Lynch's 6 categories to dynamically adjust exits, stops, and targets.
7.  **Fibonacci Wave-3 Forecaster:** Measures Wave 1 breakouts to project exact 161.8% and 261.8% extensions as target prices.
8.  **Micro-Swing Day-Trading Sentinel:** 15-minute closing-candle soft stop checks with exchange-level disaster orders.
9.  **Production PostgreSQL Migration:** 5-table schema containerization with Docker and silent daily SQL email backups (detailed architecture mapped in [plans/database-migration.md](plans/database-migration.md)).
10. **Dynamic Trailing Stop-Loss Adjuster (AETHER Profit-Lock):** Unidirectional trailing stop ratchet that updates stop floors based on highest closing prices achieved since acquisition (detailed architecture mapped in [plans/trailing-stop-adjuster.md](plans/trailing-stop-adjuster.md)).
11. **Dividend Yield Capture Factors:** Integrates dividend capture and yield variables to add premium scoring weight.
12. **Cash Dividend Reinvestment Engine:** Reinvests real-money cash distributions back into fractional shares.
13. **Saturday Pattern Retrospective (July 25):** 
    *   *BottomSnipePGRWaiver:* Bypasses slow Chaikin PGR on confirmed Trader Vic 1-2-3 bottom setups.
    *   *HighScorePGRBypass:* Bypasses Neutral PGR on extremely high combined scores (>=15.0) backed by Money Flow.
    *   *SectorMomentumConfirmation:* Waives energy/oil industry penalties during active, volume-confirmed sector breakouts.
    *   *Overbought Breakout Guard:* Apply a soft -1.5 penalty on breakout chasing inside weak sectors.
14. **AI Second-Opinion Exit Override:** Integrates an exit-routing gate to override momentum sells if the active AI Shadow Heuristic returns FLAG-FOR-REVIEW or HOLD.
15. **Short10 Momentum Floor Qualification Tuning:** Additional qualification parameters (such as Long60 trend strength &ge; 5.0, high Money Flow, or stable PGR) to dynamically bypass the strict s10 momentum floor.
16. **Market Summarization Skill Development:** Custom Gemini CLI Agent Skill headlessly ingests live index ticks and daily newsletters to generate cohesive, data-grounded macro market summaries.
17. **Model-Agnostic Token Optimizer (`prompt_optimizer.py`):** Dynamic token and prompt optimizer that prunes, compresses, or stashes verbose instructions based on the active LLM provider limits.
18. **Unified Agent Skills Registry (`skills_registry.py`):** Skills Registry that indexes and loads only the specific agent skills relevant to the active user inquiry.
