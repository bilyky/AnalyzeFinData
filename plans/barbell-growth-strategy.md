# The AETHER Generalizable Barbell Strategy: Risk-Budgeted Growth

## Objective
To deploy an asymmetric growth strategy that operates identically on a $10,000 account or a $10,000,000 account over any time horizon. This protocol completely abandons arbitrary calendar deadlines and fixed dollar targets, replacing them with strict, mathematically generalizable risk budgets and expectancy targets.

## Core Philosophy: The Horizon-Agnostic Barbell
We implement a dual-bucket portfolio architecture via a **Modular Strategy Profile** (`profiles/barbell_growth.json`). This ensures the logic is reusable and does not hardcode constraints into the execution engine.
1. **The Shield (80% Risk Budget):** Core capital-preservation trades. These adhere to all baseline AETHER rules (2.5-Sigma Bubble Guard, SPY-RSP Breadth gating, ATR-sized position scaling, and strict downside stops).
2. **The Spear (20% Risk Budget):** Asymmetric, catalyst-driven strike plays. The Spear is **hard-capped at 20% of total equity** (rebalanced) to prevent martingale creep. It must survive rigorous, dual-factor structural validation before entry. 

## The Generalizable Rules

### 1. The Pre-Execution Catalyst Gate (Dual-Factor Confirmation)
The Spear relies on external intelligence, but it must mathematically confirm that intelligence before risking capital.
* **The Rule:** The LLM proposes an unresolved event (e.g., upcoming PDUFA date, earnings shock). The Python engine then executes a strict dual-factor validation:
  1. **Volume (Attention):** RVOL > 2.0 (Institutional attention).
  2. **Price Structure (Direction):** The stock must simultaneously print a confirmed higher-low reversal bar or hold above VWAP/prior close. 
* **The Rationale:** RVOL without structure is a knife-catch. Volume AND Price must both mathematically agree with the LLM's bullish thesis.

### 2. Event-Relative Exits & Vol-Scaled Stops
Fixed-day time stops (e.g., 7 days) are arbitrary calendar artifacts that ignore market structure.
* **The Rule:** Exits are tied to the instrument and the event. 
  1. **Time-Stop:** The trade is exited if the specific catalyst date passes without the expected structural move.
  2. **Downside Stop:** The position is governed by a hard ATR/vol-scaled downside stop from Day 1 to bound the left tail.

### 3. Pyramiding on Confirmed Retests (Not Touches)
* **The Rule:** Enter with an initial ATR-scaled scout position. The system will *only* execute an add-on order (Scale-In) when the asset pulls back to a moving average AND **prints a confirmed close back above the MA** (a successful hold).
* **The Rationale:** Scaling in on an unconfirmed "touch" of a moving average is catching a falling knife. We only average up when the market structurally confirms the recovery.

### 4. Correlation Caps
* **The Rule:** The Spear bucket enforces a strict correlation cap (e.g., maximum of one broad-beta tech play). 
* **The Rationale:** Holding three highly correlated assets is a disguised, uncapped bet on market direction, which violates the Barbell's core premise of many uncorrelated, small bets.

### 5. Discovery Engine Cadence (No Daily Overfitting)
* **The Rule:** The daily execution loop evaluates trades based on frozen parameters. When the market surfaces a new phenomenon, the loop nominates it as a *candidate signal* for the Saturday R&D queue. 
* **The Rationale:** We do not tune momentum floors on a rolling 7-day P&L (which guarantees whipsawing and curve-fitting). Parameters are derived once monthly against the full 500-symbol universe. The daily loop generates hypotheses; the monthly pipeline validates them.

### 6. Anti-Toxic DNA & Gap Defense (The URA Lesson)
Technical stop-losses are illusions against overnight fundamental gap-downs. To protect the Spear bucket from catastrophic slippage:
* **The Rule:** The system must strictly enforce the `failure_dna_rules.json` feedback loop. Any asset carrying a Bearish (`Be` or `Be-`) PGR rating is mathematically toxic and is instantly vetoed, regardless of momentum scores or breakout patterns. 
* **The Rationale:** Buying momentum on fundamentally broken assets guarantees exposure to "dead cat bounces" and violent gap-downs. Furthermore, the true mathematical defense against an overnight gap is **Defined Risk** (e.g., Options or Leveraged ETFs). By using these instruments, the absolute maximum loss is bounded at the premium paid, completely neutralizing pre-market gap slippage.

## Implementation Roadmap
* **Phase 1:** Build the `profiles/barbell_growth.json` structure to separate risk parameters from `ai_portfolio_game.py`.
* **Phase 2:** Implement the Dual-Factor Catalyst Gate (RVOL + Structure) into the Spear entry logic.
* **Phase 3:** Integrate Event-Relative exits and Confirmed-Retest Pyramiding.
* **Phase 4:** Expand the Saturday Discovery Engine to accept candidate signals from daily anomalous market behavior.