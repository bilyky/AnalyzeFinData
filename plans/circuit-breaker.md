# Project AETHER R&D Lab: Systemic Crash "Circuit Breaker" (Systemic Risk Gate)

## 1. Objective
To design and deploy a highly disciplined, automated **Systemic Crash Circuit Breaker** to protect the AETHER portfolio from devastating "Black Swan" market-wide capitulations, while completely immunizing it against opening whipsaws and execution-slippage vacuums.

## 2. Architectural Design & Flow

### Phase 1: Two-Factor Systemic Confirmation
To completely avoid false alarms from single-variable volatility spikes (e.g. VIX briefly exceeding 30 on a flat day), the circuit breaker requires **Two-Factor Confirmation** to activate:
1.  **Price Factor:** The S&P 500 (SPY) must be down **`> -2.0%`** on the day (or on its 15-minute closing bar).
2.  **Volatility/Breadth Factor:** The CBOE Volatility Index (VIX) must exceed **`30`** **AND/OR** S&P 500 breadth must show a massive capitulation (more than 80% of stocks declining).
*   *The Rule:* The Circuit Breaker **only** activates if both Price and Volatility/Breadth factors confirm a systemic panic!

### Phase 2: The 30-Minute Stabilization Window (Whipsaw Protection)
To prevent the portfolio from panic-selling or stop-tightening at the worst possible prices during a chaotic morning gap-down:
1.  **The Delay:** The Circuit Breaker is **strictly forbidden** from executing any stop-tightening or selling actions within the **first 30 minutes of the market open** (6:30 AM to 7:00 AM PST / 9:30 AM to 10:00 AM EST).
2.  **The Logic:** Market opening gaps are highly volatile and frequently reverse. Waiting 30 minutes allows bid-ask spreads to narrow, market-makers to clear imbalances, and prices to find a stable equilibrium.
3.  **The Action:** The system will only enforce the breaker if the systemic breach remains fully active and confirmed **after 7:00 AM PST**.

### Phase 3: Stop-Limit & TWAP Scale-Out (Slippage Protection)
To prevent devastating execution slippage during a liquidity vacuum (where order books are empty):
1.  **Stop-Limit Orders:** The Circuit Breaker will **never** submit uncapped market orders during a panic. Instead, it will enforce **Stop-Limit orders** with a strict execution cap (e.g. Limit placed at `Stop - 1%`).
2.  **TWAP Scale-Out:** For large positions, the system will execute a Time-Weighted Average Price (TWAP) routine, scaling out of the position slowly (e.g., selling 25% of the position every 15 minutes over an hour) rather than dumping 100% of the shares in a single millisecond panic block.

### Phase 4: Dynamic Reversion (The Recovery Gate)
1.  Once the market stabilizes (SPY closes above its 5-day EMA, or VIX drops back below 28), the Circuit Breaker autonomously **de-activates**, restores the standard trailing stops, and unlocks the buy routine to deploy cash safely back into fresh bottoming breakouts.

## 3. Red-Teaming & Stress-Testing (How to "Cheat" or "Break" It?)
1.  **The "Slow-Bleed" Blindspot:** What if the market doesn't crash 2.0% in a single day, but slowly bleeds down **-1.0% every day for 10 consecutive days** (a total -10% drop)?
    *   *The Threat:* Since the daily drop never breaches -2.0%, the Circuit Breaker will **never fire**, and the portfolio will suffer a slow-bleed drawdown!
    *   *The Patch:* We must add a **Rolling 10-day Drawdown Breaker**. If the SPY's rolling 10-day drawdown exceeds **`-5.0%`**, the breaker triggers automatically, regardless of the single-day move!
2.  **The "Flash Crash" Timeout:** What if a massive, systemic crash occurs in under 2 minutes (like the 2010 Flash Crash)?
    *   *The Threat:* If the crash occurs and recovers within our 30-minute opening stabilization window, we might miss the entire crash, but if it doesn't recover, our 30-minute delay might prevent us from exiting before the stock hits rock-bottom.
    *   *The Patch:* The 30-minute delay **only applies to the market open** (6:30 AM - 7:00 AM PST). If a systemic crash occurs *intraday* (anytime after 7:00 AM PST), **the Circuit Breaker triggers instantly with zero delay!**

## 4. Verification & Testing
- Write offline mathematical unit tests in `tests/test_custom_sprints.py` simulating:
  - Intraday -3.0% crash triggering the breaker instantly.
  - Morning -2.5% gap-down successfully waiting for the 30-minute stabilization window before acting.
  - Rolling 10-day -6% bleed successfully triggering the rolling drawdown breaker.
- Ensure the 266-test suite remains fully green.