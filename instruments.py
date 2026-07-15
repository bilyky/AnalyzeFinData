"""
Instrument classification.

TEMPORARY: leveraged / inverse / crypto ETFs are excluded from new long entries and
from the long swing-low support/resistance method, because a long-biased framework
underperforms on those (the level backtest showed SQQQ/BITO/BITI etc. at the bottom).
They are NOT stripped of risk levels — they still get an ATR-based stop/target, and
holdings stay protected. Exclusion only:
  1. skips them as new BUY candidates, and
  2. routes their stop/target to the ATR method instead of the long swing-low.

This is a stopgap until a proper mean-reversion / volatility-band / inverse-aware
algorithm is built for these instruments (see CLAUDE.md R&D roadmap).
"""

import json
from pathlib import Path

_SCARCITY_CACHE_FILE = Path(__file__).resolve().parent / "Data" / "scarcity_cache.json"

# Curated, extensible set of common leveraged / inverse ETFs.
_LEVERAGED_INVERSE = frozenset({
    "SQQQ", "TQQQ", "SPXU", "UPRO", "SPXL", "SPXS", "SDS", "SSO", "QID", "QLD",
    "SH", "PSQ", "TNA", "TZA", "URTY", "SRTY", "FAS", "FAZ", "SOXL", "SOXS",
    "LABU", "LABD", "TECL", "TECS", "NUGT", "DUST", "JNUG", "JDST", "GUSH", "DRIP",
    "YINN", "YANG", "TMF", "TMV", "FNGU", "FNGD", "WEBL", "WEBS", "UVXY", "SVXY",
    "VIXY", "UVIX", "SVIX", "BOIL", "KOLD", "UCO", "SCO",
})
_CRYPTO = frozenset({
    "BITO", "BITI", "BITX", "ETHU", "ETHD", "ETHE", "GBTC", "BTF", "YBTC", "ETHA",
})

# In-process cache so repeated calls within one run never re-read the JSON file.
_scarcity_mem: dict | None = None


def _mem_cache() -> dict:
    global _scarcity_mem
    if _scarcity_mem is None:
        _scarcity_mem = _load_scarcity_cache()
    return _scarcity_mem


def classify(symbol) -> str:
    """Return 'crypto', 'leveraged_inverse', or 'normal'."""
    s = (symbol or "").upper().strip()
    if s in _CRYPTO:
        return "crypto"
    if s in _LEVERAGED_INVERSE:
        return "leveraged_inverse"
    return "normal"


def is_excluded(symbol) -> bool:
    """TEMPORARY: True for instruments where the long swing-low framework doesn't
    apply. They keep an ATR stop/target and are skipped only for new buys."""
    return classify(symbol) != "normal"


def _load_scarcity_cache() -> dict:
    if _SCARCITY_CACHE_FILE.exists():
        try:
            with open(_SCARCITY_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_scarcity_cache(cache: dict):
    try:
        _SCARCITY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SCARCITY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4)
    except Exception as e:
        print(f"Warning: Failed to save scarcity cache: {e}")


def is_scarcity_asset(symbol: str, industry_str: str) -> bool:
    """True when the symbol belongs to Grantham's 'Hard Asset' Scarcity Core
    (metals, mining, agriculture, water, grid utilities, primary energy). Result
    is cached in-process and persisted to Data/scarcity_cache.json so AI is only
    called once per symbol across the lifetime of a run."""
    s = (symbol or "").upper().strip()
    ind = (industry_str or "").strip()

    if classify(s) != "normal":
        return False

    cache = _mem_cache()
    if s in cache:
        return cache[s]

    system = "You are a financial asset classifier. Output exactly 'YES' or 'NO'."
    user = (
        f"Does '{s}' (Industry: '{ind}') belong to the Hard Asset / Scarcity Core group?\n"
        "Group includes: metals, mining, agriculture, water utilities, grid/power transmission, "
        "uranium, rare earths, oil, gas, coal extraction.\n"
        "Respond with exactly YES or NO."
    )
    try:
        import ai_client
        res = ai_client.evaluate(system, user, max_tokens=5, temperature=0.0)
        result = "YES" in (res or "").strip().upper()
    except Exception as e:
        print(f"Warning: AI classification failed for {s}: {e}. Not caching — will retry next run.")
        return False   # don't persist a transient failure as a permanent NO

    cache[s] = result
    _save_scarcity_cache(cache)
    return result
