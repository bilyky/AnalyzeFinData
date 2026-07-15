# 📉 Trader Vic "Bottom Snipers" Heuristics

This reference documents Victor Sperandeo's (Trader Vic) legendary bottom-detection price-action patterns. The LLM's role is strictly qualitative (annotating/explaining pattern formation); the actual mathematical pattern detection and the +2.0 score boost are executed deterministically by python code.

---

## 🏛️ Pattern 1: The legendary "1-2-3 Reversal"
Automatically detects the termination of a downtrend and the transition to an uptrend:
1.  **Point 1 (Trendline Break):** Price must break above the major downward-sloping trendline.
2.  **Point 2 (Test of Lows):** Price makes a minor rally and then pulls back to test the previous low, but **successfully holds above it** (creating a higher low).
3.  **Point 3 (Breakout):** Price rallies again and breaks above the intermediate peak (the high created between Point 1 and 2), confirming the trend reversal.

---

## 🏛️ Pattern 2: The explosive "2B Pattern" (Spring)
A high-probability price-action setup that exploits institutional stop-running:
1.  **Low Creation:** Price makes a major new low, establishing a key support floor.
2.  **The Stop Run:** Price drops below this key support floor to stop out retail traders, but **instantly fails to sustain the breakout**, quickly reversing.
3.  **The Trigger:** Price closes back **above the previous support floor** (the breakout point). This triggers an immediate, explosive bottom buy.

---

## ⚙️ Deterministic Python Scanner (`patterns.py`)
All calculations are performed with 100% mathematical precision:
*   **The Code:** Runs daily on the OHLCV database, scanning for recent low breaks, closes, and higher-low formations.
*   **The Reward:** Any ticker successfully triggering a confirmed "1-2-3" or "2B" setup receives an immediate, automated **`+2.0` score boost** to its `Short10` indicator, instantly highlighting them on your dashboard.
