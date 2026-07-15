# 🛡️ AETHER Strategy Profiles & Risk Parameters

This reference documents the deterministic risk constraints and safety boundaries for each strategy profile in Project AETHER. The LLM's role is strictly qualitative (interpreting market context); all sizing and capital allocation boundaries are hard-gated by local execution scripts.

---

## 📈 Summary of Hard Risk Gaps

| Metric / Parameter | 🟢 DEFENSIVE Profile | 🟡 BALANCED Profile | 🔴 AGGRESSIVE Profile |
| :--- | :--- | :--- | :--- |
| **Max Open Positions** | **3 Positions** (Strict Cap) | **5 Positions** | **8 Positions** |
| **Cash Buffer Floor** | **50% Cash Buffer** (Safety) | **20% Cash Buffer** | **0% Cash Buffer** (Fully Invested) |
| **Max Allocation per Trade** | **15.0%** of Total Equity | **15.0%** | **12.5%** |
| **Stop-Loss ATR Multiplier** | **1.5x ATR** (Tight floor) | **2.0x ATR** (Standard) | **3.0x ATR** (Loose room) |
| **Target Profit Ratio** | **1.5x Risk** (Risk-Reward) | **2.0x Risk** | **3.0x Risk** |

---

## 🧠 LLM Reasoning & "Second-Opinion" Rubric

When the deterministic engine proposes a `SELL` or `HOLD` action, the LLM-analyst performs a qualitative second-opinion check against these rules:

1.  **Winner Flower Protection:** If a position is in profit and trading above its 50-DMA, but the short-term momentum dipped (`S10 + L60 < 0`), the LLM should flag this as `FLAG-FOR-REVIEW` ("Do not cut a flower early!").
2.  **Momentum Dip vs. Breakdown:** 
    *   *Short-term Dip:* Strong PGR (Bullish) and above 50-DMA with minor score drop → Recommend patience (`AGREE` with holding).
    *   *Real Breakdown:* Weak PGR (Bearish), price falls below 50-DMA, and momentum scores negative → Confirm breakdown (`AGREE` with selling).
3.  **Preservation Floor:** If the current price is at or through the stop-loss floor, the LLM must *always* output `AGREE` with the exit to prioritize capital preservation above all else.
