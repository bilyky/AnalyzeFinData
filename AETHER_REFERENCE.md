# 🛡️ Project AETHER: Master System Reference Manual

Welcome to the **AETHER Master System Reference Manual**. This document serves as the absolute, single-source-of-truth registry for AETHER's architecture, predictive models, technical indicators, risk heuristics, self-healing systems, quality assurance protocols, and calibration configurations. 

Use this manual as your core blueprint whenever you want to **add, remove, or modify** any system feature, symbol, or mathematical parameter.

---

## 🗺️ 1. System Data-Flow Architecture

AETHER operates via a strict, circular **"Zero-Trust" data loop** designed to maintain absolute data integrity and prevent overwrite collisions:

```text
[Master Source] (state_of_the_day.xlsx in Root)
       │
       ▼
[History Backfiller] (run_history.py & rapidapi.py) ──► Saves to ──► [Local Database] (Data/Symbol_full/)
       │                                                                      │
       ▼                                                                      ▼
[Processing Engine] (main.py / powergauge.py / scoring.py / patterns.py) ─────┘
       │
       ├─► Generates ──► [Automated Output] (Data/state_of_the_day.xlsx)
       │
       └─► Executes ──► [AI Risk Portfolio Manager] (ai_portfolio_game.py)
                                │
                                └─► Triggers ──► [Shadow Copilot] (real_copilot.py) ──► Emails tickets
```

### 🚫 Core Rule of the Master Source:
*   **The Root `state_of_the_day.xlsx` is the Master Source.** All new symbols, initial watchlists, or manual profile parameters must be added to *this* file.
*   **The `Data/state_of_the_day.xlsx` is the Automated Output.** The processing engine (`main.py`) reads the Master Source, runs the quantitative scores, and overwrites the file in the `Data/` folder. **Never edit the file inside the `Data/` folder directly, as your changes will be erased on the next pipeline run.**

---

## 📊 2. Master Capabilities & Features Catalog

### A. Core Prediction & Scoring Models
*   **`buying_ratio` (BR) Model (`powergauge.py`):** Compiles Chaikin PGR, sub-category ratings, relative volume, OB/OS, Long-Term Trend, Money Flow, and weekly seasonality into a consolidated score in `[-10.0, +10.0]`.
*   **`S10` Short-Term Entry Score (`scoring.py`):** Calculates entry quality over a 10-day horizon (`[-10.0, +10.0]`). Compiles Relative Volume, OB/OS, Money Flow, contrarian Industry Strength, contrarian Long-Term Trend, Seasonality, Market Regime, Fibonacci, RSI Divergence, and pattern overlays.
*   **`L60` Long-Term Position Score (`scoring.py`):** Measures intermediate-to-long-term trend durability over a 60-day horizon (`[-10.0, +10.0]`). Focuses on core moving average alignments and primary trend strength.
*   **Weekly Seasonality Detection Engine (`scoring.py`):** Groups historical daily closing prices over 25 years by `(month, week_of_month)`. Calculates the historical 10-day forward return of that calendar week and applies a weighted tailwind (`+1.0`) or headwind (`-1.0`) factor.
*   **Fibonacci Retracement Score (`scoring.py`):** Maps the current price against key retracement levels (23.6%, 38.2%, 50.0%, 61.8%) computed from historical high-low channels.
*   **RSI Divergence Engine (`scoring.py`):** Detects classic bullish and bearish divergences between price and RSI(14) to identify short-term momentum exhaustion and trend exhaustion.

### B. Advanced Pattern Recognition Suite (`patterns.py`)
*   **Candlestick Pattern Engine:** Tracks and aggregates **17 distinct Japanese candlestick patterns** over a 5-day lookback window.
*   **Chart Pattern Engine:** Detects structural chart formations including **Head & Shoulders**, **Inverse Head & Shoulders**, **Double Tops**, **Double Bottoms**, **Cup & Handle**, **Bull Flags**, and **Bear Flags**.
*   **Momentum Pattern Engine:** Identifies 20/50 SMA crossovers (Golden/Death Crosses) and MACD crossovers (signal lines and trend crossovers).
*   **Contrarian Calibration Override:** Programmatically negates the combined pattern score (`-1.0 * pattern_score`) because backtests prove overbought patterns act as contrarian, meaning low scores represent bullish bottoming recovery plays.

### C. Active Risk Management Heuristics (`ai_portfolio_game.py`)
*   **Adaptive Strategy Profiles:** Automatically scales portfolio settings based on broad market momentum (`SPY L60` score):
    *   `DEFENSIVE`: Max 3 open positions, 10% maximum trade size, minimum 50% cash buffer.
    *   `BALANCED`: Max 5 open positions, 15% maximum trade size, minimum 20% cash buffer.
    *   `AGGRESSIVE`: Max 6 open positions, 15% maximum trade size, minimum 0% cash buffer.
*   **`is_bottom_confirmed()` 3-day Slope Trigger:** Computes the first and second derivatives of a stock's 3-day price slope to verify that the downward velocity of a pullback has flattened and turned positive (average slope > 0.5% and accelerating).
*   **The "On-Trigger Strike" (Strike Trigger):** Allows immediate entry into bottom-confirmed setups, bypassing strict Defensive profile score restrictions.
*   **Dynamic ATR Stop-Loss Trailing Stops:** Computes trailing stops based on Average True Range multipliers adjusted for risk tolerance (1.5*ATR Defensive, 2.5*ATR Balanced, 3.5*ATR Aggressive).
*   **The "Catastrophic Gap Guard" (CNXC Trap Protection):** Instantly rejects any BUY order if today's live price is more than 8% below yesterday's workbook close, protecting capital from waterfall crashes on earnings panics.

---

## 🧱 3. System Resilience & Headless Safety Gates

*   **The "AETHER Healer" (`watchdog.py`):** Headless, synchronously blocking AI self-healing loop. Intercepts script tracebacks, launches Gemini CLI, repairs code defects, and restarts the task while maintaining a `self_healing.lock` circuit breaker.
*   **Windows CP1252 Hardening:** Enforces `SafeStreamWrapper` and OS-level `PYTHONIOENCODING=utf-8` environmental variables to completely prevent legacy Windows console encoding crashes during background Task Scheduler runs.
*   **The RapidAPI "Content Validation Gate" (`rapidapi.py`):** Verifies that the API response contains valid `'Time Series (Daily)'` data before writing. If it finds a rate limit or API key error message, **it aborts the write, protecting our multi-year historical files from corruption.**
*   **The E\*TRADE "Always Refresh" Safety Gate:** Forces live HTTP token renewal requests on every single price pull. If token renewal fails headlessly (preventing browser manual inputs), **it immediately raises a RuntimeError and crashes cleanly (Exit 1)** rather than hanging on manual console prompts, allowing the Watchdog to recover.

---

## 🛡️ 4. Production & Real-Account Integrations

*   **The Real-Account Shadow Copilot (`real_copilot.py`):** Securely logs into your real-money production E\*TRADE account headlessly, retrieves your **44 active real positions**, cross-references our local technical sheets, and **automatically emails beautifully styled HTML SELL and BUY Trade Tickets directly to your inbox** (zero execution keys required, maintaining 100% account security).
*   **The Agnostic Online Fallback Scraper (`ai_portfolio_game.py`):** Scrapes real-time prices from Google Finance (`jsname="Pdsbrc"`).
*   **NYSEARCA ETF Exchange Suffixing:** Automatically suffixes ETFs with **`NYSEARCA`**, **`NASDAQ`**, **`NYSE`**, and **`AMEX`** to guarantee 100% pricing redundancy for international ETFs (like **EWT** and **EWY**) when the E\*TRADE API is offline.

---

## 🧪 5. Quality Assurance & Testing Suite

AETHER possesses an automated, rigorous unit-testing suite containing **77 comprehensive tests** that programmatically verify the mathematical and operational correctness of all system components.

### 🚀 How to Execute the QA Suite:
Before committing any code modifications, always execute the full test suite using our virtual environment:
```powershell
.\venv_new\Scripts\python.exe -m unittest discover tests
```
*   **Expected Output:** `Ran 77 tests in XXs. OK`
*   **Pre-Flight Linter:** `daily_task.py` executes `ruff check` on every single daily run before executing the trading logic, blocking any run if syntax or style issues are detected.

---

## 🎛️ 6. Calibration & Model Tuning Guide

AETHER's factor weights are fully customizable and backtest-driven:

1.  **Where Weights Are Stored:** All short-term and long-term weights are centrally configured as lookup dictionaries in **`scoring.py`** and **`patterns.py`**.
2.  **How to Run the Backtester:** To calculate how predictive our factors are across nearly 300,000 historical data points, run the factor ratings backtester:
    ```powershell
    .\venv_new\Scripts\python.exe backtest_ratings.py
    ```
3.  **How to Interpret Backtest Output:** The backtester prints the **10-day forward return spread** for each factor and outputs a **Suggested Weight**:
    ```text
    --- CHART PATTERN SCORE - Raw Factor Spread (Phase A) ---
      10d spread (high vs low bucket): 11.83%
      -> Suggested weight: +-1.5 in short_score, +-0.75 in long_score
    ```
    Simply copy these suggested weights directly into `scoring.py` to optimize the model's accuracy.

4.  **🗓️ Scheduled Milestone: Saturday, July 18, 2026 — The Autonomic Self-Tuning Optimizer & Retrospective (`calibrate_model.py` & `retrospective.py`):**
    We will develop a fully automated self-calibration and learning engine. 
    *   **The Optimizer (`calibrate_model.py`):** Headlessly runs `backtest_ratings.py`, parses the generated factor spreads, programmatically writes the newly optimized coefficients directly into `scoring.py` and `patterns.py` (updating designated anchor blocks), automatically runs the **119-test QA suite** to verify 100% code stability, and emails a beautiful comparative audit report to your inbox.
    *   **The Post-Mortem Retrospective (`retrospective.py`):** Whenever a trade is closed at a loss, it will automatically backtrack the historical state of the day of that entry, analyze the exact combination of technical scores, OB/OS, and candlestick patterns, log the "Failure DNA" to a local, ignored `Data/vulnerability_ledger.json` (Taleb's Antifragile Vulnerability Ledger), and instruct our Optimizer to automatically penalize those specific fragile setups on future runs, ensuring AETHER never makes the same trading mistake twice!

---

## 🏁 7. Developer Modification Checklist

Use this checklist whenever you want to **add, remove, or modify** any system asset or parameter:

### A. When ADDING a New Symbol:
1.  [ ] Open the root **`state_of_the_day.xlsx`** (Master Source).
2.  [ ] Locate the next empty row in the **`Research`** sheet.
3.  [ ] Write the Symbol in Column D, the Industry in Column E, and set the PGR and Setup placeholders. Save the file.
4.  [ ] The next morning at 5:30 AM PST, the pipeline (`run_history.py`) will automatically fetch, backfill, and save the historical OHLCV data to `Data/Symbol_full/` natively.

### B. When MODIFYING a Factor Weight:
1.  [ ] Run `.\venv_new\Scripts\python.exe backtest_ratings.py` to extract the empirically suggested weights.
2.  [ ] Open **`scoring.py`** or **`patterns.py`**.
3.  [ ] Modify the target weight coefficient (e.g., `candlestick_score * -0.15`).
4.  [ ] Execute the test suite `.\venv_new\Scripts\python.exe -m unittest discover tests` to verify no regressions were introduced.
5.  [ ] Commit your changes to git and push to `main`.

---

AETHER is engineered for absolute clarity, precision, and long-term performance. Stick to these rules, trust the data, and let the machine execute! 🚀💼🛡️⚙️⚡🧬
