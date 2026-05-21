"""
Pure scoring and computation functions.
No dependency on the PowerGauge class or the Chaikin API.
Called by _compute_pgr_fields() in powergauge.py.
"""

import json
import os
from utils import _to_float

# ── Market regime config ─────────────────────────────────────────────────────
# Ticker whose SMA(50) trend gates short/long scores.
# Override with REGIME_SYMBOL env var, e.g. REGIME_SYMBOL=SPY. Set to "" to disable.
REGIME_SYMBOL = os.environ.get("REGIME_SYMBOL", "RSP")

_STREAK_LOOKBACK_SHORT = 15   # max lookback days for ohlcv_streak_perc
_STREAK_LOOKBACK_LONG  = 30   # max lookback days for ohlcv_streak_count

_regime_cache: dict = {}  # (REGIME_SYMBOL, date_str) → "Bull"/"Neutral"/"Bear"


# ── OHLCV streak helpers ─────────────────────────────────────────────────────

def ohlcv_streak_perc(ohlcv_ts: dict, all_dates: list, idx: int, cur_pct: float) -> float:
    """Sum consecutive same-direction daily % changes ending at idx."""
    if idx < 1 or cur_pct == 0:
        return round(cur_pct, 4)
    going_up = cur_pct > 0
    total = cur_pct
    for i in range(idx - 1, max(0, idx - _STREAK_LOOKBACK_SHORT) - 1, -1):
        if i + 1 >= len(all_dates):
            break
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
    for i in range(idx - 1, max(0, idx - _STREAK_LOOKBACK_LONG) - 1, -1):
        if i + 1 >= len(all_dates):
            break
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


def clear_regime_cache():
    """Clear cached regime values — call between independent runs or after changing REGIME_SYMBOL."""
    _regime_cache.clear()


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
    Result is cached by date_str — safe to call once per symbol with same date.
    """
    if not REGIME_SYMBOL:
        return "Neutral"
    _cache_key = (REGIME_SYMBOL, date_str)
    if _cache_key in _regime_cache:
        return _regime_cache[_cache_key]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "Data", "Symbol_full", f"{REGIME_SYMBOL}_daily.json")
    if not os.path.exists(path):
        _regime_cache[_cache_key] = "Neutral"
        return "Neutral"
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        past  = [d for d in dates if d <= date_str]
        if len(past) < sma_period:
            print(f"  [Regime] {REGIME_SYMBOL}: only {len(past)} dates before {date_str}, need {sma_period} — using Neutral")
            result = "Neutral"
        else:
            closes = [float(ts[d]["4. close"]) for d in past[-sma_period:]]
            sma = sum(closes) / sma_period
            pct = (closes[-1] - sma) / sma
            if pct > 0.02:    result = "Bull"
            elif pct < -0.02: result = "Bear"
            else:             result = "Neutral"
    except (json.JSONDecodeError, KeyError, ValueError, ZeroDivisionError) as e:
        print(f"  [Regime] {REGIME_SYMBOL} {date_str}: error computing regime — {e}")
        result = "Neutral"
    _regime_cache[_cache_key] = result
    return result


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


# ── Fibonacci Retracement ────────────────────────────────────────────────────

def fibonacci_retracement_score(ohlcv_ts: dict, date_str: str, lookback: int = 252) -> float:
    """
    Calculate Fibonacci retracement levels and return a score based on support/resistance.
    Support (Price > Level): Positive bias.
    Resistance (Price < Level): Negative bias.
    Threshold: 0.5% proximity.
    """
    if not ohlcv_ts:
        return 0.0
    dates = sorted(ohlcv_ts.keys())
    past = [d for d in dates if d <= date_str]
    if len(past) < 20:
        return 0.0
    
    window = past[-lookback:]
    high = max(_to_float(ohlcv_ts[d].get('2. high'), 0) for d in window)
    low = min(_to_float(ohlcv_ts[d].get('3. low'), 0) for d in window)
    diff = high - low
    if diff <= 0:
        return 0.0
    
    current_price = _to_float(ohlcv_ts[past[-1]].get('4. close'), 0)
    levels = [
        high,
        high - 0.236 * diff,
        high - 0.382 * diff,
        high - 0.500 * diff,
        high - 0.618 * diff,
        high - 0.786 * diff,
        low
    ]
    
    for i, level in enumerate(levels):
        if level <= 0: continue
        prox = (current_price - level) / level
        if abs(prox) < 0.005:  # 0.5% proximity
            is_golden = (2 <= i <= 4)
            base_score = 0.5 if is_golden else 0.25
            return base_score if prox > 0 else -base_score
            
    return 0.0


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
      Fibonacci      +-1.0
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
    score += pg_fields.get('fibonacci', 0.0)
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
      Fibonacci      +-0.5
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
    score += pg_fields.get('fibonacci', 0.0) * 0.5
    return round(max(-10.0, min(10.0, score)), 1)

