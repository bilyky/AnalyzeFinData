"""Buy-idea specs: shared, pure checks a recommender runs on a candidate (plan P6, first pieces).

Each check takes values the caller already has (the closes, the R/R result) and returns None when
the candidate passes, or a short reason when it is held back. The caller does the I/O and decides
what to do with the reason (log it, show it). One home for each rule, so every recommender can call
the same check instead of keeping its own copy; see "One buy-idea spec set" in
plans/aether-services.md.

The two checks here come from R&D #113 (2026-11-19, `scripts/backtesting/waiver_trend_loss_study.py`,
from SA: recommended by the Oracle on 11-18, -29% on 11-19). On 2023-01..2026-10:
  * waiver admits more than 10% below their 52-week high hit a -15% path low within 20 sessions
    at 11.1% vs 6.4% near the high (HAC t 5.36, both halves), with no better return;
  * admits below a falling 200-day average: 10.5% vs 8.1% (t 2.66), no better return.
The ATR-matched follow-up (disclosed, after the result) keeps the sign of the first in every ATR
third (none alone at t 1.96: part of it is volatility) and finds the second mostly volatility. So
the Oracle HOLDS BACK on the first and only FLAGS the second; a volatility rule is R&D #114. The
paper game's gate uses neither yet (it needs a portfolio replay, R&D #113 notes).
"""

FAR_FROM_HIGH_PCT = 0.10   # the elite waiver applies only within 10% of the 52-week high
HIGH_WINDOW = 252          # sessions in the 52-week high
SMA_N = 200
SLOPE_LAG = 20             # "falling" = the 200-day average is lower than 20 sessions ago


def trend_features(closes: list) -> dict | None:
    """Distance below the 252-session high close and the falling-200-day flag at the last close.
    None when there is not enough history (the checks then pass: no evidence, no block)."""
    n = len(closes)
    if n < max(HIGH_WINDOW, SMA_N + SLOPE_LAG):
        return None
    last = closes[-1]
    high = max(closes[-HIGH_WINDOW:])
    sma = sum(closes[-SMA_N:]) / SMA_N
    sma_then = sum(closes[n - SLOPE_LAG - SMA_N: n - SLOPE_LAG]) / SMA_N
    return {"below_high": 1 - last / high if high else 0.0,
            "below_falling_200": last < sma and sma < sma_then}


def waiver_near_high(rr_waived: bool, feat: dict | None) -> str | None:
    """The elite-breakout waiver's premise is a leader at new highs: hold back a waiver-only admit
    that sits more than FAR_FROM_HIGH_PCT below its 52-week high."""
    if not rr_waived or not feat or feat["below_high"] <= FAR_FROM_HIGH_PCT:
        return None
    return (f"{feat['below_high']:.0%} below its 52-week high, admitted only by the elite-breakout "
            f"waiver (R&D #113)")


def not_below_falling_200(feat: dict | None) -> str | None:
    """Hold back a buy idea trading below a falling 200-day average."""
    if not feat or not feat["below_falling_200"]:
        return None
    return "below a falling 200-day average (R&D #113; mostly a volatility sign)"


def loss_filter_reasons(rr_waived: bool, closes: list) -> list:
    """Reasons to HOLD BACK a candidate ([] = passes): the waiver only near the 52-week high."""
    reason = waiver_near_high(rr_waived, trend_features(closes))
    return [reason] if reason else []


def loss_flags(closes: list) -> list:
    """Warnings shown with a candidate, not a hold-back: below a falling 200-day (mostly a
    volatility sign after the ATR-matched check; R&D #114 tests a volatility rule)."""
    flag = not_below_falling_200(trend_features(closes))
    return [flag] if flag else []
