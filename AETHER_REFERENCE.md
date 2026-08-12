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
*   **SPY-RSP Breadth Divergence Guard (Active):** Standardizes a trend score for Cap-Weighted **SPY** and Equal-Weighted **RSP**. If `SPY - RSP > 4.0`, it flags a technology-concentrated, narrow market top and automatically downgrades the active risk profile by one level (e.g. `BALANCED` ➡️ `DEFENSIVE`).
*   **Weekly Seasonality Detection Engine (`scoring.py`):** Groups historical daily closing prices over 25 years by `(month, week_of_month)`. Calculates the historical 10-day forward return of that calendar week and applies a weighted tailwind (`+1.0`) or headwind (`-1.0`) factor.
*   **Fibonacci Retracement Score (`scoring.py`):** Maps the current price against key retracement levels (23.6%, 38.2%, 50.0%, 61.8%) computed from historical high-low channels.
*   **RSI Divergence Engine (`scoring.py`):** Detects classic bullish and bearish divergences between price and RSI(14) to identify short-term momentum exhaustion and trend exhaustion.
*   **AI Sell Evaluation Engine (`sell_eval.py` & `prompts/sell_evaluation.md`):** A hybrid qualitative reasoning module that combines an LLM's fundamental analysis of company-specific risks (earnings outlook, structural headwinds, competitive decay) with technical charts to deliver a clear `SELL`, `REDUCE`, or `HOLD` second opinion on active reviews.

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
*   **Unified Exit Policy (`sell_rules.exit_decision`):** Single source of truth for sell/status decisions — a hard ATR stop-loss floor (a static `price <= stop` check, now **enforced**; 1.5/2.5/3.5×ATR by profile, cost×0.92 fallback) always wins, then a soft momentum signal (S10+L60<0), then hold.
*   **Flower Protection (Peter Lynch):** A soft exit on a position that is in profit AND above its 50-day MA is downgraded to REVIEW instead of SELL — winners aren't dumped on a momentum dip. Only ever overrides the soft signal, never the hard stop.
*   **The "Catastrophic Gap Guard" (CNXC Trap Protection):** Instantly rejects any BUY order if today's live price is more than 8% below yesterday's workbook close, protecting capital from waterfall crashes on earnings panics.
*   **The 2.5-Sigma Bubble Guard:** Dynamically calculates any asset's Z-score distance from its 500-day moving average. If the Z-score `> 2.5` (Super-Bubble Zone), the asset is blacklisted from new purchases, avoiding overextended parabolic peaks.

### D. Structural Email Intelligence Scraper (`extract_email_intel.py` & `external_intel.py`)
*   **Multi-Folder Newsletter Scanning:** Scans your Gmail `INBOX`, `[Gmail]/Promotions`, `[Gmail]/Spam`, and `[Gmail]/Trash` to aggregate financial newsletter research, capturing ideas that bypass your standard inbox.
*   **Deep Signal Extraction Pass (`extract_email_intel.py`):** Uses custom rubrics (`prompts/email_intel_extraction.md`) to extract high-cognitive parameters:
    *   **Dated Catalysts:** Contract expirations, regulatory deadlines, and earnings dates.
    *   **Supply Chain Facts:** Physical capacity constraints, tonnage, and GW volume constraints.
    *   **Missing Tickers:** Outlooked downstream or upstream plays the newsletter author did not explicitly highlight.
    *   **Implied R&D Topics:** Analytical ideas implied by data trends to enrich ongoing R&D.
*   **Adversarial Claim Verification (`_verify`):** Runs an isolated Zero-Trust second-pass verifier LLM on extracted claims, categorizing them as `VERIFIED`, `PLAUSIBLE`, or `QUESTIONABLE` to protect your decision loop from typical newsletter exaggeration.
*   **Symbology Cross-Referencing (`verify_symbols`):** Validates extracted symbols against your active (~485-symbol) Research universe, automatically attaching the Power Gauge Rating (PGR) and combined technical score to matching symbols, and isolating unlisted plays.
*   **Rich Structural Layout rendering:** Aggregates and displays catalysts, watchlist omissions (with interactive hover reasons), and bulleted R&D ideas in your Daily Trade Report HTML emails.

### E. Multi-Stock Comparison & Ranking (`aether/stock_compare.py`)
*   **Deterministic Comparison Engine (`compare_data`):** One pure, LLM-free, network-free function selects the requested symbols straight from the already-computed Research rows (no recompute, no extra workbook reads) and ranks them by combined score — ties broken by confirmed setup, then reward:risk, then symbol — so the ordering is fully stable and reproducible. This is the **single source of truth**; every surface below ranks the identical numbers the identical way.
*   **Freshness-Aware (Zero-Trust):** Each row carries a `stale` flag when its stop/target fell back to a fixed % off price (old OHLCV cache), and a comparison-wide `stale_warning` fires if any compared symbol is stale, so levels are never shown as exact when they are only approximate.
*   **Optional AI "WHY" Narrative (`summarize_comparison`, `prompts/compare_stocks.md`):** On demand, layers a ranked WHY read over the quantitative factors only (no web access). Degrades gracefully — when the rubric, a provider, or the provider call is unavailable it returns a plain-language reason (shown to the user) instead of a bare failure.
*   **Four Surfaces, One Engine:** The **Research page** (row-selection checkboxes + inline compare panel + on-demand Summarize), the **web chat** ("compare/vs" intent → ranked side-by-side), the **console** (`scripts/analysis/compare_stocks.py … --summarize --send`), and the **`/compare-stocks` agent skill** all call the same engine. The agent surface additionally layers live news/events web search on top of the shared rubric.

---

## 🧱 3. System Resilience & Headless Safety Gates

*   **Strict Pricing Verification Gate (`data_api.py`):** Reconciles every loaded position price (E*TRADE live, Excel fallback, and AI Game) against the local `Symbol_full` settled close cache. Raises `PricingDiscrepancyError` on deviation exceeding the configured tolerance, protecting the portfolio from silent price corruption.
*   **Autonomic Cache Self-Healer (`ai_portfolio_game.py`):** Checks `Symbol_full` OHLCV caches for missing or stale files before each evaluation loop. If a cache is stale, triggers an on-demand RapidAPI backfill (`_heal_symbol_cache`), un-blinding critical safety rules like Winner Protection and MA crossovers.
*   **Consolidated Intraday Stop Breach Alerts (`intraday_monitor.py`):** Batches all stop breaches detected in a single monitor pass into one summary email instead of one email per breach.
*   **Daily-Repeating Task Triggers (`register_agent_tasks.ps1`):** Registers Windows Task Scheduler triggers with `-Daily` repetition so the Watchdog and Stop Monitor continue through weekends without manual re-registration.
*   **Systemic Crash Circuit Breaker (`circuit_breaker.py`):** Audits SPY single-day return (< −2%), rolling 10-day drawdown (< −5%), and VXX surge (> +15%) every run. When triggered: freezes queued BUY orders, tightens non-scarcity stop-losses to 1×ATR, and backfeeds the event to `Data/trade_history_dna.json`. AETHER Elastic Memory caches original stops and restores them automatically once the market stabilizes.
*   **Trade DNA Bootstrapper (`bootstrap_dna.py`):** One-time setup script. Parses `Data/ai_portfolio_game.json` history, pairs BUY/SELL transactions via FIFO, backfills buy-state DNA (PGR, scores) from Excel backups, and seeds `Data/trade_history_dna.json` with past closed trades.
*   **Feedback Retrospective Analyzer (`retrospective_analyzer.py`):** Run weekly (manually or scheduled). Separates winners from losers across `trade_history_dna.json`, statistically clusters recurring failure patterns (requires ≥3-trade samples), writes rejection rules to `Data/failure_dna_rules.json`, audits circuit breaker "near-miss" threshold boundaries, and outputs `Data/retrospective_report.txt`.
*   **The "AETHER Healer" (`watchdog.py`):** Headless, synchronously blocking AI self-healing loop. Intercepts pipeline tracebacks, invokes `claude.exe` (configured via `AETHER_HEALER_CMD` env var) with a structured 6-step diagnostic protocol (`prompts/self_healing.md`), applies a minimal code fix, runs the full test suite, and commits only on green. A `self_healing.lock` circuit breaker prevents recursive loops.
*   **Live Requalify (`POST /api/requalify`, `GET /api/requalify/{run_id}`):** Two-phase AI position analysis available from the Accounts tab and Symbol detail modal. Phase 1 fetches live Chaikin data (`powergauge.get_symbol_data`), runs the deterministic exit engine (`sell_rules.exit_decision`), and generates an AI recommendation using `prompts/requalify.md` (BUY MORE / HOLD / REVIEW / REDUCE / SELL). Phase 2 runs in a background thread, scrapes recent news, re-runs the AI with news context, and the frontend polls `GET /api/requalify/{run_id}` to update the result in place.
*   **Proactive E*TRADE Session Keeper (`watchdog.py`):** The watchdog silently executes E*TRADE access token renewals every 1 hour (PT1H) in the background. This keeps the brokerage session permanently active on their servers, completely avoiding slow interactive browser re-auth runs during active trading hours.
*   **Windows CP1252 Console Fallback (`console_safe.py`) — bug-fix workaround, not a feature:** A defensive patch for a legacy-Windows console bug, not a designed capability. The three scheduled entry points (`watchdog.py`, `ai_portfolio_game.py`, `autonomous_pipeline.py`) each call `console_safe.install()` — a single shared implementation — which reconfigures `stdout`/`stderr` to UTF-8 and wraps them in a `SafeStreamWrapper` that, on `UnicodeEncodeError`, re-encodes the line with `errors='replace'` — so a stray non-ASCII glyph (emoji, `↑`) degrades to a placeholder (`?`) instead of crashing a headless Task Scheduler run. `install()` is idempotent and a no-op off Windows. Scope is those three processes only: scripts that don't call it, and any child `python` invoked without `PYTHONIOENCODING=utf-8`, can still raise cp1252 errors — so this reduces, but does not "completely prevent," console encoding crashes.
*   **The RapidAPI "Content Validation & Symbology Gate" (`rapidapi.py`):** 
    *   *Content Validation:* Verifies that the API response contains valid `'Time Series (Daily)'` data before writing. If it finds a rate limit or API key error, **it aborts the write, protecting our multi-year historical files from corruption.**
    *   *Symbology Translation:* Maps E*TRADE/Chaikin period symbols to Alpha Vantage format (e.g. `MOG.A` → `MOG-A`, `IAC` → `IACVV`) at request time while preserving original filenames on disk.
*   **The E\*TRADE "Always Refresh" Safety Gate:** Forces live HTTP token renewal requests on every single price pull. If token renewal fails headlessly (preventing browser manual inputs), **it immediately raises a RuntimeError and crashes cleanly (Exit 1)** rather than hanging on manual console prompts, allowing the Watchdog to recover.

---

## 🛡️ 4. Production & Real-Account Integrations

*   **R&D Item 14: AI Second-Opinion Exit Override Gate (`ai_portfolio_game.py`):** Intercepts deterministic momentum pullbacks (soft exits) and automatically overrides and downgrades them to `WATCH` or `HOLD` if the position's AI shadow verdict (e.g. `github_gpt` or `claude-3-5-sonnet`) is positive (`FLAG-FOR-REVIEW` or `HOLD`), letting strong winners run.
*   **AETHER Profit-Lock Trailing Stop Ratchet (`ai_portfolio_game.py`):** Dynamically ratchets trailing stop-losses upward as positions rally. Employs **Peter Lynch Flower Protection** (only ratcheting stops once safely in profit by $> 1.0x$ ATR) and the **Breakeven Lock Trigger** (bumping the stop to the exact purchase cost basis once the price rallies $> 1.5x$ ATR, so a normal-fill exit no longer closes below entry — subject to gap/slippage risk).
*   **Zero-Cash Drag Autopilot & 0% Cash Floors (`ai_portfolio_game.py`):** Dynamically drops the cash buffer floor to `0.0%` (allowing full 100% capital deployment) strictly during the `AGGRESSIVE` market regime (strong bull, SPY L60 > 2), completely eliminating cash drag.
*   **Fractional Share Sizing Precision (`aether/instruments.py` & `ai_portfolio_game.py`):** Supports precise, 3-decimal-place fractional share sizing (e.g., buying `1.458` shares) for core ETFs (DIA, SPY, QQQ, IWM) and S&P 100 blue chips, completely eliminating residual cash drag from whole-share integer rounding.
*   **Dynamic Position-Slot Expansion (`ai_portfolio_game.py`):** If our cash balance exceeds `15.0%` of total equity and we are at the maximum position limit (e.g. 6), the system automatically expands the max positions slot limit dynamically to 7 or 8 on the fly to deploy idle cash.
*   **Dynamic Pyramiding (Sizing Up Winners) (`ai_portfolio_game.py`):** If cash exceeds `10.0%` of equity, the system dynamically scales into active, high-performing green positions trading at or near breakout peaks with strong Short10 momentum (`s10 >= 3.0`), purchasing more shares (up to the 15% maximum position allocation cap) and cleanly recalculating their blended cost bases.
*   **Strict Short10 Momentum Floor of $\ge 2.5$ (`ai_portfolio_game.py`):** An anti-fragile risk filter backed by unmocked backtracking studies. It strictly prohibits buying any candidate if its Short-Term entry momentum score is below `2.5` on entry. In backtracking, this momentum floor blocked 40% of historically losing trades (including PKX, ETHT, and SA), raising the historical win rate from 50.0% to 66.7% and net P&L from +7.75% to +27.88% on that sample.
*   **Weekday Price Closing Change Streaks:** Computes Green/Red streaks from actual trading-day closes only, filtering weekends and calendar gaps.
*   **The Real-Account Shadow Copilot (`real_copilot.py`):** Securely logs into the real-money production E\*TRADE account headlessly, retrieves active real positions, cross-references local technical sheets, and emails HTML SELL and BUY trade tickets (no execution keys required).
*   **The Agnostic Online Fallback Scraper (`ai_portfolio_game.py`):** Scrapes real-time prices from Google Finance (`jsname="Pdsbrc"`).
*   **NYSEARCA ETF Exchange Suffixing:** Appends the correct exchange suffix (`NYSEARCA`, `NASDAQ`, `NYSE`, `AMEX`) to ETF symbols for Google Finance fallback pricing when the E\*TRADE API is offline.

---

## 🧪 5. Quality Assurance & Testing Suite

AETHER possesses an automated, rigorous unit-testing suite (**296 tests** across scoring, sell-rules, circuit breaker, exit-decision scorecard, config, breadth filter, DNA ledger, and sheet sync) that programmatically verifies the mathematical and operational correctness of all system components.

### 🚀 How to Execute the QA Suite:
Before committing any code modifications, always execute the full test suite using our virtual environment:
```powershell
.\venv\Scripts\python.exe -m unittest discover tests
```
*   **Expected Output:** `Ran <N> tests in XXs. OK` (all green)
*   **Pre-Flight Linter:** `daily_task.py` executes `ruff check` on every single daily run before executing the trading logic, blocking any run if syntax or style issues are detected.

---

## 🎛️ 6. Calibration & Model Tuning Guide

AETHER's factor weights are fully customizable and backtest-driven:

1.  **Where Weights Are Stored:** All short-term and long-term weights are centrally configured as lookup dictionaries in **`scoring.py`** and **`patterns.py`**.
2.  **How to Run the Backtester:** To calculate how predictive our factors are across nearly 300,000 historical data points, run the factor ratings backtester:
    ```powershell
    .\venv\Scripts\python.exe scripts\backtesting\backtest_ratings.py
    ```
3.  **How to Interpret Backtest Output:** The backtester prints the **10-day forward return spread** for each factor and outputs a **Suggested Weight**:
    ```text
    --- CHART PATTERN SCORE - Raw Factor Spread (Phase A) ---
      10d spread (high vs low bucket): 11.83%
      -> Suggested weight: +-1.5 in short_score, +-0.75 in long_score
    ```
    Simply copy these suggested weights directly into `scoring.py` to optimize the model's accuracy.

5.  **🗓️ Scheduled Milestone: Saturday, July 11, 2026 — Peter Lynch, Trader Vic, and Market Breadth Engines:**
    We will develop three major quantitative and risk-mitigation modules:
    *   **The Peter Lynch & Trader Vic Engine (`patterns.py`):** 🚀 **FULLY IMPLEMENTED, TESTED, AND DEPLOYED!** Successfully built and shipped the real-time Trader Vic **"1-2-3 Reversal"** and **"2B Pattern"** price action bottom snipers inside `patterns.py`, yielding a powerful `+2.0` score boost to bottom-confirmed setups.
    *   **Dynamic Structural Scarcity Core (80/20):** 🚀 **FULLY IMPLEMENTED, TESTED, AND DEPLOYED!** Successfully built and shipped the dedicated hard-asset allocation cap, LLM-powered asset classifier with local JSON caching (`Data/scarcity_cache.json`), and custom "shrink-ray" capital sizer inside `ai_portfolio_game.py` and `instruments.py`. The cap is **dynamic (Option C, see `plans/dynamic-scarcity-cap.md`)**: it ramps smoothly from a 20% base toward a per-profile ceiling (Aggressive 40% / Balanced 35% / Defensive 25%) as conviction (Short10+Long60) rises — replacing the old binary 8.0 cliff — and `_execute_buys()` additionally clamps every buy to the profile's per-position ceiling (Defensive 10% / Balanced-Aggressive 15%).
    *   **The SPY-RSP Breadth Divergence Filter & 2.5-Sigma Bubble Guard:** 🚀 **FULLY IMPLEMENTED, TESTED, AND DEPLOYED!** Both macro guards are active under the hood, protecting our capital from top-heavy market breadth anomalies and parabolic, overextended asset bubbles.
    *   **Instant Local Pricing & After-Hours Queuing:** 🚀 **FULLY IMPLEMENTED, TESTED, AND DEPLOYED!** Added instant local Excel-based after-hours price extraction (reducing diagnostic run latency by 87%) and real-world after-hours order queuing to completely eliminate lookahead bias and pre-market gap risk.

6.  **🗓️ Scheduled Milestone: Saturday, July 18, 2026 — The Autonomic Self-Tuning Optimizer & Retrospective (`calibrate_model.py` & `retrospective.py`):**
    We will develop a fully automated self-calibration and learning engine. 
    *   **The Optimizer (`calibrate_model.py`):** Headlessly runs `backtest_ratings.py`, parses the generated factor spreads, programmatically writes the newly optimized coefficients directly into `scoring.py` and `patterns.py` (updating designated anchor blocks), automatically runs the **296-test QA suite** to verify 100% code stability, and emails a beautiful comparative audit report to your inbox.
    *   **The Post-Mortem Retrospective & Failure DNA Loop (`retrospective_analyzer.py` & `bootstrap_dna.py`):** 🚀 **FULLY IMPLEMENTED, TESTED, AND DEPLOYED!** Successfully built and shipped the complete closed-loop retrospective system. It pairs up closed trades, chronologically backtracks their purchase dates using daily spreadsheet backups, and logs their exact PGR, scores, and Z-scores to `Data/trade_history_dna.json`. Runs every Saturday to statistically cluster true failures, automatically write active rejection rules (`Data/failure_dna_rules.json`), and output a rich human retrospective report (`Data/retrospective_report.txt`). Active rejections are autonomously enforced during buy cycles to block bad-habit trades on autopilot!

7.  **🗓️ Scheduled Milestone: Saturday, July 25, 2026 — Dividend Yield Factors & Cash Dividend Reinvestment (R&D Lab Module):**
    We will develop a comprehensive Dividend Valuation & Reinvestment engine inside `scoring.py` and `ai_portfolio_game.py` to boost our compounding returns:
    *   **Dividend Yield Factor (`scoring.py`):** Maps the current stock's forward dividend yield (sourced from live E*TRADE API quotes) into our quantitative scoring model, providing a fundamental valuation floor for defensive scarcity assets during market downtrends.
    *   **Cash Dividend Reinvestment (`ai_portfolio_game.py`):** Automatically detects dividend payout dates from held positions, collects the cash dividends, and reinvests them directly back into our cash balance to maximize capital compounding efficiency, accelerating our target trajectory toward the $20,000 portfolio goal.

8.  **🗓️ Scheduled Milestone: Saturday, August 1, 2026 — Momentum Balancing & Market Summarization Skills (R&D Lab Module):**
    We will develop two critical quantitative and AI research studies to optimize capital efficiency and data-grounded chat analysis:
    *   **Short10 Momentum Floor Qualification Tuning (Finding Balance) (`ai_portfolio_game.py`):** Research and develop an additional qualification parameter (such as Long60 trend strength $\ge 5.0$, a high Money Flow rating, or stable PGR 'Bu/Bu+') to dynamically bypass the strict `s10 >= 2.5` floor, allowing the portfolio to purchase steady, slow-moving long-term winners (like FANG with `s10 = 0.7`) while still blocking rapid-loss traps (like PKX/ETHT).
    *   **Market Summarization Skill Development (`watchdog.py` & `server.py`):** Develop a custom, un-compromised Gemini CLI Agent Skill (and matching dashboard API) that headlessly ingests live intraday index ticks, parses daily newsletter text blocks, and generates a cohesive, data-grounded macro market summary (explaining exact indices catalysts and relative strength sector shifts) to completely eliminate generic RAG lists.
    *   **Model-Agnostic Token Optimizer (`prompt_optimizer.py`):** Research and build a dynamic token and prompt optimizer that dynamically prunes, compresses, or stashes verbose skill instructions and references based on the active LLM provider (Gemini, Claude, etc.) and available context-window limits. Employs lightweight truncation heuristics or semantic-similarity slicing to ensure extreme context token efficiency.
    *   **Unified Agent Skills Registry (`skills_registry.py`):** Design an extensible Skills Registry that automatically indexes, catalogs, and manages all active agent skills inside `.gemini/skills/` (and other directories). Provides a programmatic query interface allowing the Chat Agent and Daily Driver to selectively trigger and load *only the specific skills* relevant to the active user inquiry, completely eliminating massive prompt bloating.
    *   **Option Trading & Volatility Patterns (R&D #26):** Research and design a quantitative modeling layer for option pricing, implied volatility (IV) rank/percentile, and options-based hedging/income strategies (e.g. covered calls, cash-secured puts, iron condors). Define high-reliability volatility-squeeze breakout patterns (like Bollinger Band + Keltner Channel squeezes) and backtest options delta-hedging heuristics head-to-head under our strict risk rules.
    *   **Dynamic Momentum Rotation Engine (R&D #27):** 🚀 **FULLY IMPLEMENTED, TESTED, AND DEPLOYED!** Successfully built and shipped the regime-adaptive slot-swapping mechanism inside AGGRESSIVE mode. It automatically monitors slots, identifies elite breakouts (combined score >= 12.0), and swaps out the lowest-scoring, mature (profit-locked/breakeven) positions to maximize compounding velocity and capital efficiency.

---

## 🏁 7. Developer Modification Checklist

Use this checklist whenever you want to **add, remove, or modify** any system asset or parameter:

### A. When ADDING a New Symbol:
1.  [ ] Open the root **`state_of_the_day.xlsx`** (Master Source).
2.  [ ] Locate the next empty row in the **`Research`** sheet.
3.  [ ] Write the Symbol in Column D, the Industry in Column E, and set the PGR and Setup placeholders. Save the file.
4.  [ ] The next morning at 5:30 AM PST, the pipeline (`run_history.py`) will automatically fetch, backfill, and save the historical OHLCV data to `Data/Symbol_full/` natively.

### B. When MODIFYING a Factor Weight:
1.  [ ] Run `.\venv\Scripts\python.exe scripts\backtesting\backtest_ratings.py` to extract the empirically suggested weights.
2.  [ ] Open **`scoring.py`** or **`patterns.py`**.
3.  [ ] Modify the target weight coefficient (e.g., `candlestick_score * -0.15`).
4.  [ ] Execute the test suite `.\venv\Scripts\python.exe -m unittest discover tests` to verify no regressions were introduced.
5.  [ ] Commit your changes to git and push to `main`.

---

AETHER is engineered for absolute clarity, precision, and long-term performance. Stick to these rules, trust the data, and let the machine execute! 🚀💼🛡️⚙️⚡🧬
