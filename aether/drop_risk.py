"""How prone a stock is to sudden large drops, from its own daily history: an advisory flag and a
position-size hint for recommendations. Nothing is blocked.

PROD 2026-11-18/19: SA was recommended on 11-18 and fell ~30% on 11-19. R&D #115 (tail-risk study)
then found that among admitted buys, a stock with 4 or more drop days in the prior 756 sessions hit
a -15% worst close within 10 sessions 8.9% of the time, vs 1.2% for 0-1 such days (HAC t 8.89).
The flag shows exactly that count: ``risk_utils.drop_day_flags`` is the one definition (a return
<= -10% the next session does not fully reverse; returns across session gaps left out), on real,
split-adjusted bars (``risk_utils._load_ohlcv_bars``). The history says how likely a big move is,
not when. R&D #117 found that skipping such buys did not help a portfolio; this stays a warning.
"""
from __future__ import annotations

import math

from aether import risk_utils
from aether.config import CFG

LOOKBACK = 756           # sessions before the decision day, as in R&D #115
HIGH_DROPS = 4           # R&D #115's HIGH group


def profile(symbol: str, as_of: str | None = None) -> dict | None:
    """{"sessions", "drops", "p1", "worst"} for the LOOKBACK sessions before the latest bar (the
    decision day), or None when there are fewer (R&D #115 leaves the count undefined then)."""
    dates, _h, _l, closes = risk_utils._load_ohlcv_bars(symbol, as_of)
    n = len(closes)
    if n <= LOOKBACK:
        return None
    flags = risk_utils.drop_day_flags(dates, closes)
    window = range(n - 1 - LOOKBACK, n - 1)          # returns i-756 .. i-1 for decision day i = n-1
    rets = [r for r in risk_utils.daily_returns(dates, closes)[n - 2 - LOOKBACK:n - 2] if r is not None]
    if not rets:
        return None
    ordered = sorted(rets)
    return {"sessions": LOOKBACK, "drops": sum(1 for i in window if flags[i]),
            "p1": ordered[int(len(ordered) * 0.01)], "worst": ordered[0]}


def is_high(p: dict | None) -> bool:
    return bool(p) and p["drops"] >= HIGH_DROPS


def note(p: dict | None) -> str:
    """'high drop risk: 5 days of -10% or worse in 3 years; worst 1% of days -9.7%', or ""."""
    if not is_high(p):
        return ""
    return (f"high drop risk: {p['drops']} days of -10% or worse in {p['sessions'] / 252:.0f} years; "
            f"worst 1% of days {p['p1'] * 100:+.1f}%")


def size_hint(price: float, stop: float, equity: float, p: dict | None) -> int | None:
    """Most shares such that the larger of a stop-out and a worst-1% day costs the game's per-trade
    risk cap (``CFG.system_max_trade_risk_pct``) of equity. None when the inputs don't allow a number."""
    if not price or price <= 0 or not equity or equity <= 0:
        return None
    per_share = max(price - stop if stop and 0 < stop < price else 0.0,
                    price * abs(p["p1"]) if p else 0.0)
    if per_share <= 0:
        return None
    return int(math.floor(CFG.system_max_trade_risk_pct * equity / per_share))
