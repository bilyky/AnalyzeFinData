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
