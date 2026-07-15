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
    """
    Dynamically use AI to classify if an asset fits Grantham's 'Hard Asset' Scarcity Core.
    We classify assets as Scarcity if they represent metals, mining, agricultural commodities,
    farming inputs/fertilizers, water, grid/power transmission, uranium, or energy.
    """
    s = (symbol or "").upper().strip()
    ind = (industry_str or "").strip()
    
    # Exclude leveraged and crypto immediately
    if classify(s) != "normal":
        return False
        
    cache = _load_scarcity_cache()
    if s in cache:
        return cache[s]
        
    system = "You are Project AETHER's elite financial asset classifier. Output exactly the string 'YES' or 'NO'."
    user = f"""
    Evaluate if the stock '{s}' (Industry: '{ind}') represents a 'Hard Asset' / 'Scarcity Core' commodity or utility play.
    This includes:
    1. Metals & Mining: Gold, silver, copper, base metals, steel, lithium, iron, metallurgical coal, uranium, rare earths.
    2. Agriculture & Water: Farming, crop production, timber, forestry, agricultural fertilizers/chemicals, water utilities.
    3. Grid & Energy Utilities: Power generation, electric grid transmission, regulated gas/water utilities.
    4. Primary Energy: Oil, gas, coal extraction.

    Does '{s}' ({ind}) belong to this group? Respond with exactly 'YES' or 'NO'. No explanation.
    """
    try:
        import ai_client
        res = ai_client.evaluate(system, user, max_tokens=5, temperature=0.0)
        is_scarcity = "YES" in (res or "").strip().upper()
    except Exception as e:
        print(f"Warning: AI classification failed for {s}: {e}. Defaulting to NO.")
        is_scarcity = False
        
    cache[s] = is_scarcity
    _save_scarcity_cache(cache)
    return is_scarcity

