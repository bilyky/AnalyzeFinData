"""The trading regime: one rule and one set of labels (Development page F5).

The game trades on a profile (AGGRESSIVE / BALANCED / DEFENSIVE) from SPY's Long60 score, then
one step more defensive when breadth is narrow (ai_portfolio_game.breadth_downgrade). Reports
used to rebuild the first half on their own (workbook_read.get_market_regime: no breadth step) or
read a key that was never set (data_api.get_system_health()['market_regime'] -> "Unknown"), so on
2026-11-18/19 the status email said "Unknown" while the Autopilot email said "BULLISH".

Pure: no I/O. The traded profile itself comes from ai_portfolio_game.get_market_regime(); its
label from ai_portfolio_game.get_market_regime_label(). scoring.market_regime(date) is a different
thing on purpose (SPY vs its 50-day as a per-date score input) and stays as it is.
"""

L60_BAND = 2.0   # SPY Long60 above +2 is a bull market, below -2 a bear market

LABELS = {
    "AGGRESSIVE": ("🚀 BULLISH (Risk-On)", "#2ecc71"),
    "BALANCED": ("⚖️ NEUTRAL (Consolidation)", "#f39c12"),
    "DEFENSIVE": ("⚠️ BEARISH (Risk-Off / Defensive)", "#e74c3c"),
}
UNKNOWN = ("Unknown", "#7f8c8d")


def base_profile(spy_l60) -> str:
    """The profile from SPY's Long60 alone, before the breadth step."""
    l60 = spy_l60 or 0
    if l60 > L60_BAND:
        return "AGGRESSIVE"
    if l60 < -L60_BAND:
        return "DEFENSIVE"
    return "BALANCED"


def label(profile) -> tuple:
    """(text, colour) for a profile; Unknown for anything else."""
    return LABELS.get(profile, UNKNOWN)
