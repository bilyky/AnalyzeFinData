# AnalyzeFinData — Claude Instructions

> 🛡️ **MANDATE:** You MUST read and strictly adhere to the unified workspace instructions in [AGENT.md](./AGENT.md) as your absolute first priority before executing any operations or reasoning in this workspace!

## General Principles

*   **Rule of Zero Trust:** NEVER trust an initial data point or assumption. ALWAYS verify by checking file timestamps, reading raw JSON cache, or cross-referencing E*TRADE quotes before raising doubts or making moves.
*   **Rule of Temporal Zero-Trust:** NEVER assume or guess the current date or time. Before ANY time-dependent operation (checking market hours, verifying file freshness, or generating reports), the system MUST execute an empirical clock check via a shell tool first. Never trust memory for temporal state.
*   **Mandatory Backup Policy:** NEVER run a script or modify a file without a verified backup. All core state files (`ai_portfolio_game.json`, `state_of_the_day.xlsx`, `ops_and_rd_tracker.xlsx`) must be cloned to a timestamped backup directory (`Data/Backup/`) before any write is executed.
*   **Empirical Verification:** Before recommending an action (EXIT/BUY), the system must backtrack: check the last 3 times a similar technical setup occurred for that symbol in the local cache to verify its historical hit rate.
*   **Self-Correction:** If the system identifies a discrepancy (e.g., live price vs. OHLCV close), it must automatically log the correction and re-calculate dependent scores.
*   **Rule of Loss Minimization:** NEVER prioritize returns over risk. Capital preservation is the absolute priority. The downside on any single trade must be strictly capped using dynamic ATR stop-losses, and the portfolio must maintain a high defensive cash cushion (at least 50%) during broad market corrections (SPY L60 < -2).

## Saturday R&D Roadmap (Next Sessions)

*   **Session: Saturday, July 4, 2026 (10:00 AM PST) — Jeremy Grantham & E*TRADE Alerts API:**
    1.  ✅ *The 2.5-Sigma Bubble Detector:* **SHIPPED** — `calculate_bubble_z_score()` in `ai_portfolio_game.py`; rejects any symbol > 2.5 SD above its 500-day mean.
    2.  ✅ *Structural Scarcity Core:* **SHIPPED** — 20% allocation cap, LLM classifier, shrink-ray sizer in `instruments.py` + `ai_portfolio_game.py`.
    3.  *E*TRADE Alerts API Integration:* Research and prototype a headless client for E*TRADE's native Alerts API (`GET /v1/user/alerts`) to dynamically capture "Power Inflow" and broker order-flow events directly from the broker in real-time.
    4.  *The Intraday Sentinel Trigger:* Code the real-time event-driven loop to parse these broker alerts every 15 minutes, run technical bottom checks, and instantly execute buy orders inside the active session.
    5.  ✅ *The Real-Account Shadow Copilot:* **SHIPPED** — `real_copilot.py` headlessly scans real E*TRADE holdings and emails HTML trade tickets.

*   **Session: Saturday, July 11, 2026 (10:00 AM PST) — Peter Lynch & Portfolio Gardening:**
    1.  *The "Flower Protection" Trailing Stop:* Flower Protection logic is active in `sell_rules.exit_decision()` — soft exits downgraded to REVIEW when position is in profit above 50-DMA. Full dynamic trailing stop (see `plans/trailing-stop-adjuster.md`) not yet implemented.
    2.  *The "Weed Cutter" (Breaking the Disposition Effect):* ATR hard stop enforced in `sell_rules.exit_decision()` — positions at or below stop trigger immediate EXIT regardless of fundamentals.
    3.  *The "Lynch Local Edge" Filter:* Not yet implemented — requires fundamental data integration.
    4.  ✅ *Trader Vic's Reversal Engines:* **SHIPPED** — "1-2-3 Reversal" and "2B Pattern" in `patterns.py` with `+2.0` score boost to bottom-confirmed setups.
    5.  *The "Non-Correlated Satellite" Engine:* Not yet implemented (`satellite_slot.py` not created).
    6.  ✅ *The SPY-RSP Breadth Divergence Filter:* **SHIPPED** — `SPY_Score - RSP_Score > 4.0` auto-downgrades strategy profile; active in scoring pipeline.

*   **Session: Saturday, July 18, 2026 (10:00 AM PST) — The Autonomic Self-Tuning Optimizer & Retrospective:**
    1.  *The Backtest Parser:* Code a headless parser inside `calibrate_model.py` that executes `backtest_ratings.py` programmatically, extracts the 10-day forward return spreads for all technical and pattern factors, and calculates the optimal weights using normalized spread ratio distributions.
    2.  *The Dynamic Code-Writer:* Build a safe, self-contained file-writing routine that opens `scoring.py` and `patterns.py`, locates the designated central weight configurations (marked by developer anchor comments), and programmatically rewrites the active coefficients with the newly optimized values.
    3.  *The Automated QA Gate:* Program the calibration loop to instantly run the full unit testing suite (`python -m unittest discover tests`) after any weight adjustment to ensure 100% code compilation and system stability.
    4.  *The Calibration Audit Email:* Integrate a notification routine that emails a detailed HTML report to the user, displaying the old weights, the newly optimized weights, the statistical alpha improvements, and a green "QA Passed" confirmation badge.
    5.  *The Post-Mortem Retrospective & Antifragile Vulnerability Ledger:* Code a post-mortem diagnostic parser (`retrospective.py`). Whenever a trade is closed at a loss, it will automatically backtrack the historical state of the day of that entry, analyze the exact combination of technical scores, OB/OS, and candlestick patterns, log the "Failure DNA" to a local, ignored `Data/vulnerability_ledger.json`, and instruct our Optimizer to automatically penalize those specific fragile setups on future runs.

*   **Session: Saturday, July 25, 2026 (10:00 AM PST) — "Right Kind of Wrong" (Amy Edmondson): Failing Well by Design:**
    Research the FT/Schroders 2023 Business Book of the Year — Amy Edmondson's *Right Kind of Wrong: The Science of Failing Well* — and apply its failure taxonomy to how AETHER learns from losses. Core idea: **not all losses are equal; prevent the preventable, welcome the productive.** Three archetypes:
    - **Basic failure** — preventable, in familiar territory, a deviation from a known standard.
    - **Complex failure** — a "perfect storm" of several causes aligning in familiar territory.
    - **Intelligent failure** — the "right kind of wrong": a hypothesis-driven bet in *new* territory, adequately prepared, and **as small as possible**, whose loss buys real information.

    Application principles:
    1.  *The Failure-Type Classifier:* Extend the Jul-18 Vulnerability Ledger / `decision_eval` scorecard so every closed loss is tagged **basic / complex / intelligent**, not just "loss." Basic = the preventable ones (sold a winner on stale data à la ANET/RPD, ignored/mis-set an ATR stop, traded on a stale workbook, bypassed the gap guard, any data-integrity violation). Complex = multi-cause market storms (e.g. the Jul-8 Iran + oil-spike + Fed-hawkish day). Intelligent = a well-reasoned new-setup bet, sized small, that simply didn't pan out.
    2.  *Drive Basic Failures to Zero:* Basic failures are the only kind the Optimizer should penalize hard — they represent broken process, and the system must learn to never repeat them (this is exactly what the winner-protection + enforced-stop work in `sell_rules.py` already started).
    3.  *Do NOT Over-Penalize Intelligent Failures:* Critical nuance / guardrail — the Jul-18 optimizer's "penalize fragile setups" reflex must **exempt intelligent failures**, or the system slowly becomes over-conservative and stops discovering new alpha ("fail fast" done blindly is a trap Edmondson explicitly warns against). Treat them as paid-for information, not defects.
    4.  *The Intelligent-Failure Budget:* Reserve a small, explicit slice of capital for hypothesis-driven experimental entries (new patterns/sectors), sized "as small as possible," tracked separately as experiments — expected to fail often but cheaply, generating learning without threatening capital preservation.
    5.  *Blameless, Data-Driven Post-Mortem:* Frame the reflection/retrospective process as blameless and evidence-based (reflect on the *pattern* and its Failure DNA, never a single "bad call") — the decision log + scorecard is the objective record. Mitigate complex failures structurally (cash buffer, diversification, breadth filter) rather than blaming any one signal.
    6.  *Reflect on EVERY Close, Not Just Losses (Outcome ≠ Decision Quality):* The retrospective must run on **every** closed position — gains included — because a profitable exit can be a *bigger* failure than a clean loss. Two cases to flag explicitly: (a) **Opportunity-cost / sold-too-early** — booking RPD at +63% that then runs to +120% is a large failure disguised as a win; the `decision_eval` "missed-upside" metric already measures this and it must count against the process. (b) **Right outcome, wrong process (lucky win)** — a gain produced by a rule that shouldn't have fired is a *process* failure to correct, not a success to repeat. Score decisions by their quality at decision time, never by the P&L sign of the result. This is the flip side of the winner-protection lesson (see the `dont-sell-winners` reflection): a "win" that dumps a flower is still a mistake.

*   **Session: Saturday, August 1, 2026 (10:00 AM PST) — Bill Williams & the Profitunity ("Trading Chaos") System:**
    Research Bill Williams' *Trading Chaos* / Profitunity method and formalize it across AETHER. We already shipped the foundational piece — his **Fractals** now drive `risk_utils.detect_support` (a confirmed swing low = a bar whose low is the extreme within ±k bars, requiring k bars *after* to confirm, so an unconfirmed recent dip is ignored). This session extends the rest of his toolkit as scoring/regime signals. Williams frames the market in five dimensions; take them in order:
    1.  *Fractal Engine (Space) — extend what we started:* Generalize `detect_support` into a two-sided fractal engine — **fractal lows = support/stops, fractal highs = targets/resistance** — and expose multi-degree fractals (a fractal that itself brackets smaller fractals is stronger). Feed fractal highs into the Target column and fractal lows into the stop ladder (already the top rung). Trail the stop up to the most recent *up* fractal as price advances (Williams' fractal trailing stop) — the code form of the Jul-11 "Flower Protection" trailing idea.
    2.  *The Alligator (Balance Line / Trend) — regime filter:* Implement the Alligator = three smoothed moving averages (Jaw 13, Teeth 8, Lips 5, forward-shifted 8/5/3). Lines intertwined = the Alligator "sleeps" (range/no-trade); lines fanned and ordered = it "eats" (trend). Use it as an **entry gate** — suppress fresh buys when the Alligator is sleeping (choppy), matching our existing "don't chase consolidation" instinct, and as a cleaner trend read than the raw SMA stack.
    3.  *Awesome Oscillator (Momentum):* Code the AO = SMA5 − SMA34 of bar midpoints `(H+L)/2`. Add its two canonical signals — the **zero-line cross** and the **saucer** — as an `S10` momentum factor and a bottom-confirmation input alongside `is_bottom_confirmed`.
    4.  *Market Facilitation Index + Volume (Zone):* Compute MFI = `(High − Low) / Volume` and pair each bar's MFI change with its volume change into Williams' four-bar matrix — **Green** (MFI↑ Vol↑, real move), **Fade** (both↓, exhaustion), **Fake** (MFI↑ Vol↓, no conviction), **Squat** (MFI↓ Vol↑, battle → likely breakout). Use "Green" and "Squat" as high-quality entry-timing confirmations and "Fake/Fade" as filters. (Note the current daily Chaikin append writes `low = close` and `volume = 0` — MFI needs **real OHLCV with volume**, so this dimension depends on the RapidAPI-sourced bars, not the Chaikin daily append. Fold into the OHLCV-freshness work.)
    5.  *Gator Oscillator + Profitunity Synthesis:* Add the Gator (Alligator jaw/teeth/lips convergence-divergence histogram) to visualize the sleep→awaken transition, then synthesize the five dimensions into a single Profitunity readiness score, gated by the Alligator regime and confirmed by AO + MFI — so a signal only fires when *space* (fractal), *trend* (Alligator), *momentum* (AO), and *zone* (MFI) agree. Backtest each dimension's marginal alpha via `backtest_ratings.py` before wiring weights, per the Jul-18 optimizer discipline.

*   **Session: Saturday, August 8, 2026 (10:00 AM PST) — Proper Levels for Leveraged / Inverse / Crypto Instruments:**
    The level backtest (`backtest_levels.py`) showed the long swing-low/high framework works for ~85% of the universe (median win-rate ~65%) but **fails on leveraged, inverse, and crypto ETFs** (SQQQ, BITO, BITI, SOXS, … clustered at the bottom, 33–49% win-rate) — a long-biased support model doesn't fit products that are structurally short, decay daily, or mean-revert. As a **temporary stopgap** those instruments are now classified in `instruments.py`, excluded from new long BUYs, and routed to the generic **ATR** stop/target (they are never left without a stop). This session designs the *real* algorithm:
    1.  *Direction-aware levels:* For an inverse ETF, "support" for a long is the wrong frame — model the **underlying's resistance** (inverse of it) or treat the position directionally. Detect the instrument's structural bias and flip the swing logic accordingly.
    2.  *Volatility-band stops (Keltner / Bollinger / Chandelier):* Leveraged products need volatility-scaled bands, not price-pivot swing lows. Prototype Keltner/Chandelier (ATR-multiple off a moving anchor) and backtest against the swing-low baseline for this cohort.
    3.  *Mean-reversion targets:* Many of these oscillate rather than trend; model targets as reversion to a moving-average / VWAP band instead of the next fractal high.
    4.  *Decay awareness:* Leveraged/inverse ETFs suffer volatility decay over holding periods — fold an explicit time-decay penalty into any hold/target logic so we never treat them as buy-and-hold.
    5.  *Re-validate & lift the exclusion:* Extend `backtest_levels.py` to score this cohort under the new algorithm; only remove the temporary `instruments.is_excluded` gate once the cohort's win-rate matches the broad universe. Until then the exclusion stays.

*   **Session: Saturday, August 15, 2026 (10:00 AM PST) — W.D. Gann Square of Nine as a Price Level Layer:**
    Research W.D. Gann's Square of Nine theory and backtest whether Gann angle proximity adds statistically significant alpha to AETHER's level-detection stack. The core idea: numbers spiral outward from 1 in a square matrix; rotating 90°/180°/270°/360° around any anchor price (key high or low) produces natural support/resistance levels that many traders watch — creating self-fulfilling order clustering. The "Power of Nine" is that nine's digit-sum always collapses to 9, making it structurally self-completing in Gann's model.

    Implementation plan:
    1.  *Deep Research First:* Run `/deep-research` on "W.D. Gann Square of Nine: exact construction algorithm, price-to-angle formula, empirical backtest studies, known failure modes" before writing any code.
    2.  *Core Math (`aether/primal_funcs.py`):* Implement `gann_sq9_levels(anchor_price, rotations=[90,180,270,360])` using the standard shortcut: `√anchor → ± 0.25·n → square`. Anchor on confirmed fractal lows/highs from `risk_utils.detect_support`.
    3.  *Scoring factor (`aether/scoring.py`):* Add `gann_sq9_score(price, ohlcv_ts)` alongside the existing `fibonacci_retracement_score` — returns a score in the same [-2, +2] range based on proximity to the nearest Gann level.
    4.  *Backtest via `backtest_ratings.py`:* Measure 10-day forward return spread for the new factor across the full 500-symbol universe. Only add to the S10 composite if the spread is statistically meaningful. If not, document why and park it.
    5.  *Integration gate:* If alpha is confirmed, wire into `scoring.short_score()` with a weight determined by the optimizer (Jul-18 calibration). Keep the factor independent (not bundled with Fibonacci) so each can be turned on/off separately.

*   **Session: Saturday, August 22, 2026 (10:00 AM PST) — Price Pattern Research Lab (Numerology, Calendar & Behavioral):**
    Research and backtest six candidate micro-factors across the full 500-symbol OHLCV universe using the same pipeline as the digit-sum study (`scripts/backtesting/digit_sum_study.py` as the template). For each factor: (1) run `/deep-research` on the academic/practitioner literature first, (2) implement the metric in `aether/primal_funcs.py`, (3) backtest 10-day forward return spread via `backtest_ratings.py`, (4) only wire into `aether/scoring.py` if the spread is statistically meaningful. Factors to investigate:

    **Numerology / Number Theory:**
    1.  *Units-Digit Pattern:* Last digit only of integer open price (0–9). Simpler than digit-sum; tests whether market-maker price clustering at whole-dollar boundaries creates directional bias. Architecture: same as `digit_sum_open_score` / `digit_sum_score` — separate standing (close units digit → next day) from real-time (open units digit → same day, applied in `_execute_buys`).
    2.  *Round-Number Proximity:* Distance of current price from nearest $5, $10, $25, $50, $100 levels, expressed as a percentage of price. Institutional orders cluster at round numbers creating real support/resistance. Score: far below = approaching support (+), far above = approaching resistance (−). Likely stronger than pure digit-sum.
    3.  *Cents Pattern (.99 / .50 / .00):* Does $X.99 (sub-round, market-maker accumulation zone) behave differently from $X.01 (just-broke-through) or $X.50 (mid-level)? Bucket by fractional cents, test open→same-day direction.

    **Calendar / Time Patterns (per-symbol, not universal):**
    4.  *Day-of-Week Effect (per symbol):* Some stocks have consistent Monday gaps (weekend news), Friday fade (option positioning), or Thursday beats (pre-earnings drift). Extend the existing `compute_seasonality()` to a weekday dimension per symbol. Result: a 5-bucket lookup table per symbol, same architecture as the digit-sum study JSON.
    5.  *Week-of-Month Effect (per symbol):* Option expiration (3rd Friday) and index rebalancing create regular intramonth patterns. The global `week_of_month()` already exists in `aether/scoring.py`; test whether symbol-specific week-of-month buckets add marginal alpha on top.

    **Price Action / Behavioral Patterns:**
    6.  *Gap Persistence vs. Reversal:* When the open gaps above/below prior close by >0.5%, >1%, >2% — does the stock continue in the gap direction by close (momentum), or revert (fade)? Bucketed by gap size and direction. Per-symbol. **Most immediately actionable** — applied at the open as a real-time signal in `_execute_buys` alongside the open-digit signal.
    7.  *Inside Bar Compression:* When yesterday's high-low range is fully inside the prior day's range (volatility squeeze), is the next session more likely to be directional? Classic setup for breakout entries — validate whether it shows up in our universe with meaningful edge.
    8.  *Consecutive Streak Reversal:* After N consecutive up/down days (N=3,4,5), does the (N+1)th day tend to continue or reverse? `ohlcv_streak_count` already exists; backtest it as a direction predictor rather than just a descriptor.

    **Shared implementation pattern for all eight factors:**
    - Each factor produces a per-symbol lookup table (JSON) stored under `Data/` — same format as `digit_sum_study.json`
    - Minimum N=50 per bucket, |z|≥2.0 for inclusion
    - Symbol-modal digit-sum table is extended to show whichever factors have significant signals for that symbol
    - Monthly refresh via `scripts/backtesting/` standalone scripts
    - Only factors with a confirmed 10-day forward-return spread in `backtest_ratings.py` get added to `short_score()` / `long_score()`; others are logged and displayed but not scored

## Workflow Conventions

When the user asks to "send", "push", "create", or "save" something (e.g., Gmail draft, commit, file), execute the full action — do not just preview or show content for review unless explicitly asked.

## Excel / openpyxl

For Excel/openpyxl work: when a fix is claimed to be working, verify by actually reopening the file (or simulating the reopen path) and checking the warning/state is gone before reporting success.

Before telling the user a fix works, re-run the exact reproduction steps the user would take (reopen the file, re-execute the script, re-query) and paste the output proving the issue is resolved. If you cannot verify end-to-end, say so explicitly.

## Project Conventions

Standard ranking/query conventions: Setup field uses `'OK'`/`''` strings (not 1/0), and Win% is stored as a decimal (multiply by 100 for display).
