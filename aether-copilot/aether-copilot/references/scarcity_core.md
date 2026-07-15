# 🌾 Structural Scarcity Core (80/20 Core-Satellite)

This reference documents our dedicated asset allocation shield. The LLM's role is strictly cognitive (classifying whether a company fits the scarcity paradigm); all capital limits, position-sizing, and order sizing operations are executed deterministically by Python scripts.

---

## 🏛️ The 80/20 Walled-Off Split
*   **The Satellites (80%):** Standard equities (Tech, Healthcare, Consumers, etc.). Capped at **80.0%** of total portfolio equity.
*   **The Scarcity Core (20%):** Dedicated long-term allocation capped strictly at **20.0%** of total portfolio equity, reserved exclusively for physical, real, and hard assets to ride multi-year secular resource cycles.

---

## 🧠 LLM Asset Classification Heuristics
When a new setup is discovered, the LLM-classifier determines if the symbol is a "Scarcity Asset" by evaluating if its primary revenue is derived from:

1.  **Metals & Mining:** Copper, Uranium, Gold, Silver, Lithium, Platinum.
2.  **Agriculture & Fertilizer:** Wheat, Potash, Nitrogen, Phosphate, Farmland.
3.  **Grid Utilities & Power:** Electrical grid infrastructure, water supply, nuclear utilities, power generation.
4.  **Fossil Fuels & Carbon:** Natural gas, crude oil production, pipeline transportation, refinery infrastructure.

### 🛡️ Indestructible Local Caching
To completely avoid recurring LLM token consumption and eliminate network lag:
*   Once classified, the output is saved to **`Data/scarcity_cache.json`**.
*   Subsequent scans load the classification **instantly and locally** from the JSON cache with zero LLM API calls.

---

## ⚙️ Deterministic "Shrink-Ray" Order Sizer (Python)
If a high-conviction scarcity setup is triggered, but adding the standard 15% trade size would breach our strict 20.0% structural cap, the python execution scripts will **not** reject the trade.
*   Instead, the script dynamically activates our **"Shrink-Ray" Order Sizer**.
*   It calculates the remaining room inside the 20% scarcity bucket, and **downsizes the order quantity (shares count) to fit exactly under the cap limit**, achieving 100% capital efficiency with zero risk!
