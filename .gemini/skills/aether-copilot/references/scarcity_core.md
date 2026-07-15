# Structural Scarcity Core (80/20 Core-Satellite)

The LLM's role is classification only (does a symbol fit the scarcity paradigm?); all capital limits and order sizing run deterministically in Python.

---

## The 80/20 Split

*   **Satellites (80%):** Standard equities (Tech, Healthcare, Consumers, etc.) — capped at 80% of portfolio equity.
*   **Scarcity Core (20%):** Hard/real assets only — capped at 20% of portfolio equity.

---

## LLM Classification Heuristics

A symbol qualifies as a Scarcity Asset if its primary revenue derives from:

1.  **Metals & Mining:** Copper, Uranium, Gold, Silver, Lithium, Platinum.
2.  **Agriculture & Fertilizer:** Wheat, Potash, Nitrogen, Phosphate, Farmland.
3.  **Grid Utilities & Power:** Electrical grid, water supply, nuclear, power generation.
4.  **Fossil Fuels & Carbon:** Natural gas, crude oil, pipeline, refinery.

### Local Caching
Once classified, the result is saved to `Data/scarcity_cache.json` (gitignored). Subsequent runs read from the cache — no LLM call. The in-process dict (`_scarcity_mem`) avoids file reads within a single run.

---

## Deterministic Order Sizer

If adding the standard trade size would breach the 20% cap, the script calculates the remaining room in the scarcity bucket and downsizes the share count to fit exactly within it. If no room remains, the buy is rejected.
