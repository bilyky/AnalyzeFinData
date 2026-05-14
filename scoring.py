"""
Pure scoring and computation functions.
No dependency on the PowerGauge class or the Chaikin API.
Called by _compute_pgr_fields() in powergauge.py.
"""

import json
import os

# ── Market regime config ─────────────────────────────────────────────────────
# Ticker whose SMA(50) trend gates short/long scores.
# Set to "" to disable. Change to "SPY", "QQQ", "IWM", etc. as needed.
REGIME_SYMBOL = "RSP"

# ── Local helper (mirrors powergauge._to_float, kept here to avoid circular import) ──
def _to_float(val, default):
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


# ── OHLCV streak helpers ─────────────────────────────────────────────────────

def ohlcv_streak_perc(ohlcv_ts: dict, all_dates: list, idx: int, cur_pct: float) -> float:
    """Sum consecutive same-direction daily % changes ending at idx."""
    if idx < 1 or cur_pct == 0:
        return round(cur_pct, 4)
    going_up = cur_pct > 0
    total = cur_pct
    for i in range(idx - 1, max(0, idx - 15) - 1, -1):
        prev_close = _to_float(ohlcv_ts[all_dates[i]].get('4. close'), 0)
        curr_close = _to_float(ohlcv_ts[all_dates[i + 1]].get('4. close'), 0)
        if prev_close <= 0 or curr_close <= 0:
            break
        daily_pct = (curr_close - prev_close) / prev_close * 100
        if (daily_pct > 0) == going_up:
            total += daily_pct
        else:
            break
    return round(total, 4)


def ohlcv_streak_count(ohlcv_ts: dict, all_dates: list, idx: int, cur_pct: float) -> int:
    """Count consecutive same-direction days ending at idx (positive=up, negative=down)."""
    if idx < 1 or cur_pct == 0:
        return 0
    going_up = cur_pct > 0
    count = 1 if going_up else -1
    for i in range(idx - 1, max(0, idx - 30) - 1, -1):
        prev_close = _to_float(ohlcv_ts[all_dates[i]].get('4. close'), 0)
        curr_close = _to_float(ohlcv_ts[all_dates[i + 1]].get('4. close'), 0)
        if prev_close <= 0 or curr_close <= 0:
            break
        daily_pct = (curr_close - prev_close) / prev_close * 100
        if (daily_pct > 0) == going_up:
            count += 1 if going_up else -1
        else:
            break
    return count


# ── Seasonality ──────────────────────────────────────────────────────────────

def week_of_month(day: int) -> int:
    if day <= 7:  return 1
    if day <= 15: return 2
    if day <= 22: return 3
    return 4


def compute_seasonality(ohlcv_ts: dict, current_month: int, current_day: int) -> float:
    """
    Historical avg 10-day return for current (month, week-of-month) across all years.
    Returns +1.0 (strong tailwind) .. -1.0 (headwind), or 0.0 if < 3 data points.
    """
    if not ohlcv_ts:
        return 0.0

    target_wk = week_of_month(current_day)
    all_dates  = sorted(ohlcv_ts.keys())
    date_idx   = {d: i for i, d in enumerate(all_dates)}

    wk_last = {}
    for d in all_dates:
        y, m, day = int(d[:4]), int(d[5:7]), int(d[8:10])
        w = week_of_month(day)
        wk_last[(y, m, w)] = d

    returns = []
    for (y, m, w), start_date in wk_last.items():
        if m != current_month or w != target_wk:
            continue
        idx = date_idx[start_date]
        future_idx = idx + 10
        if future_idx >= len(all_dates):
            continue
        c0 = _to_float(ohlcv_ts[start_date].get('4. close'), 0)
        c1 = _to_float(ohlcv_ts[all_dates[future_idx]].get('4. close'), 0)
        if c0 > 0 and c1 > 0:
            returns.append((c1 - c0) / c0 * 100)

    if len(returns) < 3:
        return 0.0
    avg = sum(returns) / len(returns)
    if avg > 2.0:   return  1.0
    if avg > 1.0:   return  0.5
    if avg > -1.0:  return  0.0
    if avg > -2.0:  return -0.5
    return -1.0


# ── Win% lookup table (BR score buckets) ────────────────────────────────────

_WIN_PCT_TABLE = [
    (4.0,  0.643),
    (2.0,  0.576),
    (0.0,  0.531),
    (-2.0, 0.503),
]


def predicted_win_pct(br: float) -> float:
    for threshold, pct in _WIN_PCT_TABLE:
        if br >= threshold:
            return pct
    return 0.463


# ── Market regime ─────────────────────────────────────────────────────────────

def market_regime(date_str: str, sma_period: int = 50) -> str:
    """
    Bull / Neutral / Bear based on REGIME_SYMBOL SMA(sma_period).
      Bull:    price > SMA by > 2%
      Bear:    price < SMA by > 2%
      Neutral: within +-2% of SMA, or data unavailable
    """
    if not REGIME_SYMBOL:
        return "Neutral"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "Data", "Symbol_full", f"{REGIME_SYMBOL}_daily.json")
    if not os.path.exists(path):
        return "Neutral"
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        past  = [d for d in dates if d <= date_str]
        if len(past) < sma_period:
            return "Neutral"
        closes = [float(ts[d]["4. close"]) for d in past[-sma_period:]]
        sma = sum(closes) / sma_period
        pct = (closes[-1] - sma) / sma
        if pct > 0.02:   return "Bull"
        if pct < -0.02:  return "Bear"
        return "Neutral"
    except Exception:
        return "Neutral"


# ── Relative volume ──────────────────────────────────────────────────────────

def rel_volume_bucket(ohlcv_ts: dict, date_str: str, lookback: int = 20) -> str | None:
    """
    Entry-day volume / avg volume of prior `lookback` days.
      High (1.5-2x): +2.5 short, +2.0 long  (best bucket by backtest)
      Very High (2x+): +0.5 short, 0 long    (news/spike effect dampens signal)
      Normal (0.75-1.5x): 0
      Low (<0.75x): -2.0 short, -1.0 long
    """
    if not ohlcv_ts:
        return None
    dates = sorted(ohlcv_ts.keys())
    past  = [d for d in dates if d <= date_str]
    if len(past) < lookback + 1:
        return None
    idx       = len(past) - 1
    entry_vol = _to_float(ohlcv_ts[past[idx]].get('5. volume'), 0)
    avg_vol   = sum(
        _to_float(ohlcv_ts[past[i]].get('5. volume'), 0)
        for i in range(idx - lookback, idx)
    ) / lookback
    if avg_vol <= 0 or entry_vol <= 0:
        return None
    rv = entry_vol / avg_vol
    if rv >= 2.0:   return "Very High"
    if rv >= 1.5:   return "High"
    if rv >= 0.75:  return "Normal"
    return "Low"


# ── Short-term and long-term scores ─────────────────────────────────────────
# These take plain dicts extracted from PowerGauge fields — no class dependency.

def short_score(pg_fields: dict) -> float:
    """
    10-day entry-quality score: -10 to +10.

    Factor weights (backtest, 336k obs 2023-2025, NA-filtered):
      Rel Volume     4.4% spread: High +2.5, Very High +0.5, Low -2.0
      OB/OS          4.3%:        Optimal +3, Early +1, Wait -2
      Money Flow     3.5%:        Strong +3, Weak -2
      Industry Str   3.1%:        Weak +2, Strong -2  (contrarian)
      LT Trend       2.1%:        Weak +1.5, Strong -1.5  (contrarian)
      Seasonality    +-1.0
      Regime         +-1.0
    """
    score = 0.0
    rv = pg_fields.get('rel_vol')
    score += {'High': 2.5, 'Very High': 0.5, 'Normal': 0.0, 'Low': -2.0}.get(rv or '', 0.0)
    score += {'Optimal': 3.0, 'Early': 1.0, 'Neutral': 0.0, 'Wait': -2.0}.get(pg_fields.get('ob_os', ''), 0.0)
    score += {'Strong': 3.0, 'Neutral': 0.0, 'Weak': -2.0}.get(pg_fields.get('money_flow', ''), 0.0)
    score += {'Weak': 2.0, 'Strong': -2.0}.get(pg_fields.get('industry_strength', ''), 0.0)
    score += {'Weak': 1.5, 'Neutral': 0.0, 'Strong': -1.5}.get(pg_fields.get('lt_trend', ''), 0.0)
    score += pg_fields.get('seasonality', 0.0)
    score += {'Bull': 1.0, 'Neutral': 0.0, 'Bear': -1.0}.get(pg_fields.get('market_regime', 'Neutral'), 0.0)
    return round(max(-10.0, min(10.0, score)), 1)


def long_score(pg_fields: dict) -> float:
    """
    60-day position-quality score: -10 to +10.

    Factor weights (backtest, 336k obs 2023-2025, NA-filtered):
      LT Trend       4.5% spread: Weak +4, Strong -3  (contrarian)
      Rel Volume     2.8%:        High +2, Low -1
      Money Flow     2.5%:        Strong +2.5, Weak -2
      Industry Str   2.4%:        Weak +2, Strong -1.5  (contrarian)
      OB/OS          2.3%:        Optimal +1.5, Early +0.5, Wait -0.5
      Seasonality    +-0.5
      Regime         +-1.5
    """
    score = 0.0
    score += {'Weak': 4.0, 'Neutral': 0.0, 'Strong': -3.0}.get(pg_fields.get('lt_trend', ''), 0.0)
    rv = pg_fields.get('rel_vol')
    score += {'High': 2.0, 'Very High': 0.0, 'Normal': 0.0, 'Low': -1.0}.get(rv or '', 0.0)
    score += {'Strong': 2.5, 'Neutral': 0.0, 'Weak': -2.0}.get(pg_fields.get('money_flow', ''), 0.0)
    score += {'Weak': 2.0, 'Strong': -1.5}.get(pg_fields.get('industry_strength', ''), 0.0)
    score += {'Optimal': 1.5, 'Early': 0.5, 'Neutral': 0.0, 'Wait': -0.5}.get(pg_fields.get('ob_os', ''), 0.0)
    score += pg_fields.get('seasonality', 0.0) * 0.5
    score += {'Bull': 1.5, 'Neutral': 0.0, 'Bear': -1.5}.get(pg_fields.get('market_regime', 'Neutral'), 0.0)
    return round(max(-10.0, min(10.0, score)), 1)
