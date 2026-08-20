# 🔬 Design Document: Earnings-Shock Failure Gate & Overbought Breakout Guard

This document outlines the quantitative research, architectural design, and operational rules for two critical defensive filters designed to eliminate momentum-chasing blindspots on the AETHER quantitative desk.

---

## 📅 1. Historical Post-Mortem (August 14, 2026)
During the rebalancing of August 14–17, the virtual AI Portfolio Game experienced a **-2.65% drawdown** ($10,705.61 → $10,421.23) driven by two specific acquisitions: **KE (-10.15%)** and **CCL (-3.64%)**. 

An empirical audit of the JSON technical caches on disk for the day of acquisition revealed two major blindspots:

1.  **KE (Kimball Electronics):**
    *   *The Buy:* Selected on Aug 14 due to its strong 60-day price trend (`l60` of `8.0`).
    *   *The Blindspot:* Just **2 days prior (Aug 12)**, KE reported a catastrophic earnings miss ($-0.01 vs $0.39 expected, missing by $0.40!) with an active "Very Bearish" surprise warning. Our trend-only scoring model was completely blind to this fundamental shock.
2.  **CCL (Carnival):**
    *   *The Buy:* Selected on Aug 14 due to its strong 60-day price trend (`l60` of `7.8`).
    *   *The Blindspot:* CCL's Chaikin checklist on that exact day reported **`0 of 3` positive strength indicators and `0 of 3` positive timing indicators**, and its industry rating was **`Weak`**. Our model was lured in by a temporary, unconfirmed short-squeeze inside a decaying sector.

---

## 🏛️ 2. Proposed Defensive Rules

### 🛡️ Filter A: Earnings-Shock Failure Gate (Pillar 1 Guard)
To prevent buying into a fundamental cliff right after an earnings disappointment, we introduce a hard gate inside `check_failure_rules()` or `failure_dna_rules.json`:
*   **The Rule:** If a candidate symbol has reported earnings within the last **5 calendar days**, and:
    *   The `eps_diff_description` contains `"missed by"` and the miss exceeds **10%** of the estimate, OR
    *   The `warning_impact` is `"Very Bearish"`,
*   **The Action:** **Automatically VETO the candidate from purchase**, regardless of how high its momentum score is.

### 🛡️ Filter B: Overbought / Weak-Sector Breakout Guard (R&D #32)
To prevent chasing unconfirmed breakouts inside weak industries:
*   **The Rule:** Apply a soft **`-1.5` combined score penalty** if:
    *   The symbol has `< 1` positive strength checkmarks in its Chaikin checklist, AND
    *   Its Chaikin `industry` relative strength rating is `"Weak"`.
*   **The Action:** Adjusts the combined score downwards, allowing healthy breakout candidates in strong sectors to pass while pushing weak-sector squeezes below our purchase threshold.

---

## 📊 3. Backtesting Methodology
To prove the value of these rules before deployment, we will write a backtesting script `scripts/backtesting/rebalance_retro_study.py` that parses historical daily run CSVs and daily symbol JSON files between **June 2025 and August 2026** to measure:
1.  **Baseline Return:** Forward 10-day return of all candidates selected by the standard momentum model.
2.  **Filtered Return:** Forward 10-day return after applying both the Earnings-Shock Gate and the Overbought/Weak-Sector Guard.
3.  **Metrics to Track:**
    *   Total Trades Triggered (Volume)
    *   Win Rate (10d Close > Entry Close)
    *   Mean 10d Expected Return
    *   Maximum Drawdown / Stop-Loss Breaches
