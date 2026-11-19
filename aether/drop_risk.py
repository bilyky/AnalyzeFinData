"""How prone a stock is to sudden large drops, from its own daily history: an advisory flag and a
position-size hint for recommendations. Nothing is blocked.

PROD 2026-11-18/19: SA was recommended on 11-18 and fell ~30% on 11-19. Its history already set it
apart (3 years to 11-17: 5 days of -10% or worse, universe median 2; worst 1% of days -9.7%, median
-6.1%; annual volatility 58%, median 38%), though its worst day had been -15.7%, so no history said
*when*. The history says *how likely* a big move is, which is what a position size should respect.

Bars come from ``risk_utils._load_ohlcv_bars``: split-adjusted, placeholder bars excluded; bars on days NYSE
was closed are dropped. Plateau spikes
(1-3 bars far from the previous close that then snap back) are bad bars and dropped first:
some cached series hold them (RS: a 2023-11-23 Thanksgiving bar at 369.1 between ~268 closes).
Whether a high count predicts the next big drop is being tested separately (the tail-risk study);
until then this is a warning and a sizing hint only.
"""
from __future__ import annotations

import datetime
import math

from aether import market_calendar, risk_utils

LOOKBACK = 756           # ~3 years of sessions
DROP = -0.10             # a "sudden drop" day
SPIKE_PCT = 0.15         # a bar this far from the previous close ...
BACK_PCT = 0.05          # ... with the price back within this of it 1-3 bars later is a bad bar
HIGH_DROPS = 4           # this many drop days (or a worst-1% day at/below HIGH_P1) flags the name
HIGH_P1 = -0.08
RISK_PCT = 0.01          # size so a stop-out or a worst-1% day costs at most this share of equity
MIN_SESSIONS = 250


def _is_session(day: str) -> bool:
    try:
        return market_calendar.is_session(datetime.date.fromisoformat(day))
    except ValueError:
        return False


def drop_spikes(closes: list) -> list:
    """Closes without plateau spikes: 1-3 bars more than SPIKE_PCT away from the previous close,
    after which the price is back within BACK_PCT of it (RS 2023-11-23/24: two bars at 369.1
    between ~268 closes). A real crash that stays down is kept."""
    bad = set()
    for i in range(1, len(closes)):
        base = closes[i - 1]
        for k in (1, 2, 3):
            j = i + k
            if j < len(closes) and abs(closes[j] / base - 1) < BACK_PCT                     and all(abs(closes[m] / base - 1) > SPIKE_PCT for m in range(i, j)):
                bad.update(range(i, j))
                break
    return [c for i, c in enumerate(closes) if i not in bad]


def profile(symbol: str, as_of: str | None = None) -> dict | None:
    """{"sessions", "drops", "p1", "worst"} over the last LOOKBACK sessions, or None when there is
    too little history."""
    dates, _h, _l, closes = risk_utils._load_ohlcv_bars(symbol, as_of)
    # Bars on days NYSE was closed are cache junk (RS: closes of 369.29 on 2024-11-28 and 2025-07-04).
    closes = [c for d, c in zip(dates, closes) if c and c > 0 and _is_session(d)]
    closes = drop_spikes(closes)[-(LOOKBACK + 1):]
    r = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    if len(r) < MIN_SESSIONS:
        return None
    ordered = sorted(r)
    return {"sessions": len(r), "drops": sum(1 for x in r if x <= DROP),
            "p1": ordered[int(len(ordered) * 0.01)], "worst": ordered[0]}


def is_high(p: dict | None) -> bool:
    return bool(p) and (p["drops"] >= HIGH_DROPS or p["p1"] <= HIGH_P1)


def note(p: dict | None) -> str:
    """'high drop risk: 5 days of -10% or worse in 3 years; worst 1% of days -9.7%', or ""."""
    if not is_high(p):
        return ""
    years = p["sessions"] / 252
    return (f"high drop risk: {p['drops']} days of -10% or worse in {years:.0f} years; "
            f"worst 1% of days {p['p1'] * 100:+.1f}%")


def size_hint(price: float, stop: float, equity: float, p: dict | None) -> int | None:
    """Most shares such that the larger of a stop-out and a worst-1% day costs RISK_PCT of equity.
    None when the inputs don't allow a number."""
    if not price or price <= 0 or not equity or equity <= 0:
        return None
    per_share = max(price - stop if stop and 0 < stop < price else 0.0,
                    price * abs(p["p1"]) if p else 0.0)
    if per_share <= 0:
        return None
    return int(math.floor(RISK_PCT * equity / per_share))
