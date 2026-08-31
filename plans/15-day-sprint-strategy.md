# The AETHER "15-Day Sprint" Strategy: The Barbell & Catalyst Protocol

## Objective
To mathematically bridge the gap to our $20,000 portfolio goal in the remaining 15 days without recklessly "gaming" the risk sliders. We must transition from a symmetric, index-hugging portfolio to an asymmetric, catalyst-driven strike force while preserving the initial $10,000 capital base.

## Core Philosophy: The Taleb Barbell (Modular Architecture)
We will abandon the linear "7 equal slots" approach. Instead, we divide the portfolio into two extremes (The Barbell) controlled by a **Modular Strategy Profile** (e.g., `profiles/sprint_barbell.json`), ensuring we do not pollute the core Python engine with hardcoded 15-day rules.
1. **The Shield (80% of Capital):** Ultra-safe, capital-preservation trades. These adhere to all current strict rules (2.5-Sigma guard, 2.0:1 R:R, strict ATR stops). 
2. **The Spear (20% of Capital + Generated Profit):** Hyper-concentrated, asymmetric risk designed for exponential upside. This bucket is allowed to break standard technical rules *only if* it passes the Qualitative Homework Gate.

## The New Dynamic Rules (Enterprise-Grade & Red-Team Hardened)

### 1. The Pre-Execution "Homework" Tribunal (RVOL-Confirmed Catalyst Gate)
Technical indicators (S10/L60) are lagging, but raw LLM analysis is prone to hallucination. To put a stock in the "Spear" bucket, it must pass a dual-factor gate *before* execution.
* **The Rule:** The LLM scans external intel/news for a specific, **unresolved** asymmetric event. 
* **The Verification:** If the LLM proposes a catalyst, the core engine must mathematically verify institutional participation via **Relative Volume (RVOL)**. If RVOL > 2.0 (volume is 200% above average), institutions agree with the LLM, and the trade is authorized. If RVOL is average, the LLM is hallucinating, and the trade is vetoed.

### 2. Time-Velocity Stops (The 7-Day Rule)
Capital trapped in a sideways stock is dead money.
* **The Rule:** If a stock does not achieve a **+5% gain within 7 trading days** of acquisition, it is automatically sold at market.
* **The Rationale:** Seven days allows the stock to break out, mathematically retest its breakout support level, and continue higher without triggering our time stop, while still enforcing extreme capital velocity.

### 3. The "House Money" Asymmetric Leverage (3x LETFs)
To achieve exponential returns without the engineering nightmare and IV-crush risk of an Options API, we will use existing equity infrastructure.
* **The Rule:** We authorize the system to buy **3x Leveraged ETFs (LETFs)** (e.g., TQQQ, UPRO, SOXL) on our top Catalyst-approved "Spear" setups using *only* house money.
* **The Rationale:** We capture 300% asymmetric leverage using our 100% battle-tested stock execution engine. Zero new API code required, zero expiration risk, and maximum upside, all while capping our maximum possible loss at our profit margin.

### 4. Dynamic Pullback Pyramiding (Scaling In)
We stop buying full allocations on Day 1, and we do not scale-in on gap-ups (which ruins our cost basis).
* **The Rule:** Buy 33% of the target position initially. We execute a secondary "scale-in" buy order (the remaining 66%) **only on a pullback retest** to the nearest moving average (e.g., the 10-SMA or 20-SMA), simultaneously moving the initial stop-loss to breakeven.

### 5. Active Cash Sweeps (No Dead Money)
If no catalysts are found, the 20% Spear bucket cannot sit idle.
* **The Rule:** Any unallocated Spear cash is automatically swept into liquid yield-bearing ETFs (e.g., SGOV) or high-momentum proxies (e.g., IBIT) to earn yield every single day. The moment a valid catalyst appears, the proxy is instantly liquidated to fund the Spear.

### 6. The Iterative 7-Day Retrospective (Daily Calibration)
No static rule set survives a shifting market regime. To ensure the Barbell strategy remains adaptive, we must institute a mandatory, daily calibration loop.
* **The Rule:** Every single day, before the market opens, we will conduct an interactive AI-guided review of the past 7 days of execution data.
* **The Rationale:** This allows us to instantly adjust our pullback levels, volume thresholds, or momentum floors for the very next session based on empirical feedback.

## Implementation Phases
1. **Phase 1:** Build the Modular Strategy Profile framework (`profiles/barbell.json`) to decouple sprint logic from the core engine.
2. **Phase 2:** Inject the 7-Day Time-Velocity Stop and Pullback Pyramiding into `ai_portfolio_game.py` (triggered only by the Barbell profile).
3. **Phase 3:** Build the dual-factor `pre_execution_homework()` function (LLM Event + RVOL > 2.0).
4. **Phase 4:** Authorize the 3x LETF whitelist and build the Active Cash Sweep logic.