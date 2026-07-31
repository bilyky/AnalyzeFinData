# AETHER Autonomic Parameter Self-Calibration Engine (AETHER Calibrator)

> **STATUS: NOT YET IMPLEMENTED**
> This design document maps out the mathematical and technical architecture for the `calibrate_model.py` script.
> It outlines how the system dynamically optimizes our quantitative technical weights based on the past 30 days of market feedback, preventing alpha decay.

## 1. Objective
To design and deploy a self-directed, mathematical **Autonomic Parameter Self-Calibration Engine (`calibrate_model.py`)** that automatically backtests the past 30 days of watchlist signals to dynamically tune indicator weights, thresholds, and momentum floors. This brings a Jim Simons-level adaptive edge to Project AETHER, ensuring our scoring model remains optimized as market regimes shift.

---

## 2. Mathematical & Optimization Model

### 1. The Core Objective Function
The calibrator aims to find a vector of weights $W$ that maximizes the **Risk-Adjusted Return (Sharpe-like Performance Ratio)** of the top-5 scoring buys over a 30-day historical window.

$$\text{Maximize } \Phi(W) = \frac{\text{Mean Daily Return of Top-5 Buys}(W)}{\text{Standard Deviation of Daily Returns}(W)} - \lambda \|W - W_{\text{default}}\|_2^2$$

where:
*   $W = [w_{\text{candle}}, w_{\text{chart}}, w_{\text{momentum}}, w_{\text{digit}}, w_{\text{pgr}}, w_{\text{volatility}}]$ represents the vector of active weights.
*   $W_{\text{default}}$ is our baseline starting weight vector.
*   $\lambda \|W - W_{\text{default}}\|_2^2$ is an **L2 Regularization Penalty (Ridge Penalty)** that prevents the model from overfitting or diverging wildly into unstable, extreme values.

### 2. Constraints & Boundaries
To prevent the model from assigning absurd values (such as making a bearish score positive to fit a single black-swan outlier), we enforce strict boundaries:
*   $w_{\text{candle}} \in [-0.15, -0.01]$
*   $w_{\text{chart}} \in [-0.15, -0.01]$
*   $w_{\text{momentum}} \in [-0.15, -0.01]$
*   $w_{\text{digit}} \in [0.1, 1.0]$
*   $w_{\text{pgr}} \in [0.5, 3.0]$

---

## 3. Step-by-Step Architectural Flow

### Phase 1: Matrix Ingestion
1.  **Time Series Construction:** The script scans `Data/Symbol_full/*.json` and pulls daily OHLCV prices and historical indicator values (PGR, Money Flow, LT Trend, OB/OS, Candlestick, Chart, Momentum) over the last 30 trading days.
2.  **Feature Matrix $\mathbf{X}$:** Builds a 3D matrix $\mathbf{X}$ of dimensions $(\text{Symbols} \times \text{Days} \times \text{Indicators})$.
3.  **Returns Matrix $\mathbf{R}$:** Computes the actual forward 1-day, 5-day, and 10-day price returns for all symbols across the 30-day window.

### Phase 2: Signal Generation Simulation
For any trial weight vector $W$:
1.  **Score Calculation:** For each symbol on day $t$, calculate the combined score:
    $$\text{Score}(s, t) = \sum_{k} w_k \cdot X_{s, t, k}$$
2.  **Ranking:** Rank all symbols on day $t$ descending by score.
3.  **Buy Simulation:** Pick the top-5 highest-scoring symbols on day $t$ that pass basic setup filters. Record their actual next-day returns.

### Phase 3: The SciPy Optimization Loop
The calibrator uses a bounded numerical solver to optimize the objective function:
*   **Solver:** `scipy.optimize.minimize` using the **`L-BFGS-B`** (Limited-memory Broyden-Fletcher-Goldfarb-Shanno) algorithm.
*   **Convergence:** Executes a maximum of 500 iterations to find the global optimum weight vector $W^*$ that historically produced the highest risk-adjusted returns.

### Phase 4: Integration & Serialization
1.  **Write Weights:** Saves the optimized vector $W^*$ to **`Data/calibrated_weights.json`**.
2.  **Dynamic Loading (`aether/scoring.py`):**
    *   Modify `aether/scoring.py` (specifically `short_score()` and `long_score()`) to check for the existence of `Data/calibrated_weights.json`.
    *   If the file exists and is less than 7 days old, **load and use these optimized weights dynamically!**
    *   If missing or stale, fall back safely to our default hardcoded weights, maintaining absolute stability.

---

## 4. Red-Teaming & Stress-Testing (How to "Break" It?)

*   **1. The Overfitting Trap:** If a single biotech stock went up +500% in the last 30 days, a simple solver will skew the weights entirely to purchase that one stock.
    *   *Mitigation:* The L2 regularization penalty ($\lambda$) and strict boundary boxes prevent the optimizer from chasing outliers.
*   **2. Data Starvation:** If some symbols have missing history during the 30 days, the matrix will have NaNs.
    *   *Mitigation:* The script will execute strict listwise deletion of any symbol row that does not contain a complete 30-day pricing history.
*   **3. Weight Staleness:** If we calibrate once and never again, we defeat the adaptive purpose.
    *   *Mitigation:* Schedule `calibrate_model.py` to run automatically **every Saturday at 10:00 AM PST** as part of our `AETHER_RD_Scientist` retrospective task.
