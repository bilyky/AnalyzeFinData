# AETHER Production Trailing Stop-Loss Adjuster (AETHER Profit-Lock)

## 1. Objective
To design and deploy a highly disciplined, automated **Dynamic Peak-Trailing Stop-Loss Adjuster (Profit-Lock)**. This system dynamically ratchets your stop-loss floor upwards as a stock rallies, permanently locking in open profits and ensuring that a profitable "winner" is never allowed to evaporate back into a losing position.

---

## 2. Mathematical & Execution Model

### 1. The Core Formulas
Whenever a stock in your portfolio achieves a **new highest closing price (peak close)** since the day you acquired it, the stop-loss is dynamically recalculated:

$$\text{New Stop Floor} = \text{Highest Close Since Acquisition} - (\text{Multiplier} \times \text{ATR})$$

*   **The Unidirectional Constraint:** The stop-loss can **only move upwards** (it can never be lowered!).
    $$\text{stop\_loss}_{t} = \max(\text{stop\_loss}_{t-1}, \ \text{peak\_close} - (\text{Multiplier} \times \text{ATR}))$$
*   **The Sizing Multiplier:**
    *   *Standard Satellites:* Uses a **`2.5 * ATR`** trailing cushion.
    *   *High-Volatility / Biotech Satellites:* Uses a **`3.0 * ATR`** trailing cushion.
    *   *Scarcity Core:* Uses a **`1.5 * ATR`** tighter trailing cushion to capture quick spikes in commodity assets.

---

## 3. Step-by-Step Architectural Flow

### Phase 1: Peak Price Tracking
To calculate the peak price without needing to store infinite historical intraday state, we add a single metadata field to each position inside your portfolio database:
*   `highest_close_since_acq` ➡️ Stores the maximum close price achieved by the stock since the date acquired.
*   *The Update:* On every daily run, the autopilot compares today's live close price with `highest_close_since_acq`:
    $$\text{highest\_close\_since\_acq} = \max(\text{highest\_close\_since\_acq}, \ \text{current\_price})$$

### Phase 2: Dynamic Stop Recalculation
Once the peak close is updated, the script calculates the new stop floor using the active stock's ATR:
1.  If the newly calculated stop floor is **greater** than the existing stop-loss, **overwrite the stop-loss with the higher floor!**
2.  If it is lower (due to a pullback), **strictly preserve the existing stop-loss.**
3.  Write the updated stop-loss directly back to your database (`ai_portfolio_game.json` or PostgreSQL)!

### Phase 3: The "Breakeven" Trigger (Profit Protection)
*   **The Rule:** The split-second the stock rallies by **`> 1.5x ATR`** above your cost basis, **the trailing stop-loss is automatically, instantly bumped to your exact purchase Cost Basis (Breakeven)!**
*   **The Impact:** This completely guarantees a **"Risk-Free Trade"** early in the trend, completely eliminating any possibility of a loss once the stock begins its breakout!

---

## 4. Red-Teaming & Stress-Testing (How to "Cheat" or "Break" It?)

1.  **The "Gap-Down on Earnings" Hole:**
    *   *The Threat:* A stock rallies to `$150.00` (Stop ratcheted to `$140.00`). Overnight, a bad earnings report is released, and the stock gaps down and opens at `$132.00`.
    *   *The Failure:* Even though our stop was at `$140.00`, the stock bypassed it overnight and opened lower. We are stopped out at `$132.00` instead of `$140.00`!
    *   *The Patch:* We must combine this adjuster with our newly deployed **Idiosyncratic Gap-Down Guard**! If the stock gaps down $> -8\%$ below our stop floor, we hold the exit wide during the first 30 minutes to let the opening panic settle before executing!
2.  **The "Too-Tight Whip" Hole:**
    *   *The Threat:* If we tighten the stop-loss too rapidly early in the trade (e.g. using `1.0x ATR`), a minor intraday pullback will instantly stop us out, causing us to miss the massive, multi-week `Wave 3` trend!
    *   *The Patch:* We must enforce the **Lynch Flower Protection**! Trailing stop tightening is **strictly disabled** for any positions that are trading below their cost basis or underperforming their initial trend, allowing them room to breathe, and **only activates once the trade is safely in profit by $> 1.0x$ ATR!**

---

## 5. Verification & Testing
- Write unmocked mathematical tests in `tests/test_custom_sprints.py` verifying:
  - Trailing stop-loss correctly rachets upwards as the stock price rises from `$100` to `$110` to `$120`.
  - Trailing stop-loss strictly remains locked at `$110` when the stock pulls back to `$115`.
  - Breakeven trigger successfully locks in cost-basis when the price rallies $> 1.5x$ ATR.
- Ensure the 281-test suite remains fully green.
