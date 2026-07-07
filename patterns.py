"""
Pattern recognition: candlestick, chart, and momentum patterns.
Returns float scores in [-2, +2] for integration into scoring pipeline.

Weights are PLACEHOLDER until backtest_ratings.py Phase A runs produce spread data.
"""

import numpy as np
import signals as sig


# ── OHLCV adapter ────────────────────────────────────────────────────────────

def ohlcv_to_array(ohlcv_ts: dict, date_str: str, lookback: int = 250):
    """Convert OHLCV dict to numpy array shape (N, 5): [open, high, low, close, volume].

    Returns None if fewer than 10 bars are available up to date_str.
    """
    if not ohlcv_ts:
        return None
    dates = sorted(ohlcv_ts.keys())
    past = [d for d in dates if d <= date_str]
    if len(past) < 10:
        return None
    window = past[-lookback:]
    arr = np.zeros((len(window), 5), dtype=float)
    for i, d in enumerate(window):
        row = ohlcv_ts[d]
        arr[i, 0] = float(row.get('1. open',  0) or 0)
        arr[i, 1] = float(row.get('2. high',  0) or 0)
        arr[i, 2] = float(row.get('3. low',   0) or 0)
        arr[i, 3] = float(row.get('4. close', 0) or 0)
        arr[i, 4] = float(row.get('5. volume', 0) or 0)
    return arr


# ── Private helpers ──────────────────────────────────────────────────────────

def _wilder_atr(arr: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder ATR aligned to arr rows (no row deletion). Returns 1D array."""
    n = len(arr)
    tr = np.zeros(n)
    for i in range(1, n):
        hl = arr[i, 1] - arr[i, 2]
        hc = abs(arr[i, 1] - arr[i - 1, 3])
        lc = abs(arr[i, 2] - arr[i - 1, 3])
        tr[i] = max(hl, hc, lc)
    atr = np.zeros(n)
    if n > period:
        atr[period] = float(np.mean(tr[1:period + 1]))
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _ema(values: list, period: int) -> list:
    """Simple EMA. Returns list same length as values, 0.0 before warm-up."""
    result = [0.0] * len(values)
    if len(values) < period:
        return result
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    k = 2.0 / (period + 1)
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1.0 - k)
    return result


def _find_peaks_troughs(closes: np.ndarray, n: int = 3):
    """Local maxima/minima requiring n neighbors on each side.

    Returns (peaks, troughs) as lists of (index, price) tuples.
    """
    peaks, troughs = [], []
    for k in range(n, len(closes) - n):
        c = closes[k]
        if all(c >= closes[k - j] and c >= closes[k + j] for j in range(1, n + 1)):
            peaks.append((k, float(c)))
        if all(c <= closes[k - j] and c <= closes[k + j] for j in range(1, n + 1)):
            troughs.append((k, float(c)))
    return peaks, troughs


# ── Candlestick score ─────────────────────────────────────────────────────────

def candlestick_score(ohlcv_ts: dict, date_str: str, lookback: int = 5) -> float:
    """Run all signals.py patterns; aggregate bullish/bearish fires over last lookback bars.

    Returns float in [-2, +2]. 0.0 if OHLCV unavailable or <10 bars.
    """
    arr = ohlcv_to_array(ohlcv_ts, date_str, lookback=30)
    if arr is None or len(arr) < 10:
        return 0.0

    body = float(np.mean(arr[-lookback:, 3])) * 0.01  # 1% of avg close
    atr_vals = _wilder_atr(arr)

    bull = 0
    bear = 0

    def _check(result, b_col, s_col):
        nonlocal bull, bear
        window = result[-lookback:]
        if any(window[:, b_col] > 0):
            bull += 1
        if any(window[:, s_col] < 0):
            bear += 1

    # Each call needs a fresh copy: signals.py calls pf.add_column() expanding shape
    _check(sig.engulfing_signal(arr.copy(),       0, 3, 5, 6),       5, 6)
    _check(sig.harami_signal(arr.copy(),          0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.harami_strict_signal(arr.copy(),   0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.doji_signal(arr.copy(),            0, 3, 5, 6),       5, 6)
    _check(sig.piersing_signal(arr.copy(),        0, 3, 5, 6),       5, 6)
    _check(sig.star_signal(arr.copy(),            0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.tasuki_signal(arr.copy(),          0, 3, 5, 6),       5, 6)
    _check(sig.bottle_signal(arr.copy(),          0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.neck_signal(arr.copy(),            0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.h_signal(arr.copy(),               0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.slingshot_signal(arr.copy(),       0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.hikkake_signal(arr.copy(),         0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.three_methods_signal(arr.copy(),   0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.stick_sandwich_signal(arr.copy(),  0, 1, 2, 3, 5, 6), 5, 6)
    _check(sig.tweezers_signal(arr.copy(),        0, 1, 2, 3, 5, 6, body), 5, 6)
    _check(sig.quintuplets_signal(arr.copy(),     0, 3, 5, 6, body), 5, 6)

    # double_trouble needs ATR pre-computed as column 5
    arr_atr = np.column_stack([arr, atr_vals])
    _check(sig.double_trouble_signal(arr_atr.copy(), 0, 1, 2, 3, 5, 6, 7), 6, 7)

    raw = bull - bear
    score = max(-2.0, min(2.0, raw / 5.0 * 2.0))
    return round(score, 2)


# ── Chart pattern score ───────────────────────────────────────────────────────

def chart_pattern_score(ohlcv_ts: dict, date_str: str, lookback: int = 250):
    """Detect structural chart patterns.

    Returns (score, names_list). Score in [-2, +2].
    Needs >= 50 bars; returns (0.0, []) otherwise.
    """
    if not ohlcv_ts:
        return 0.0, []
    dates = sorted(ohlcv_ts.keys())
    past = [d for d in dates if d <= date_str]
    if len(past) < 50:
        return 0.0, []

    window = past[-lookback:]
    closes = np.array([float(ohlcv_ts[d].get('4. close', 0) or 0) for d in window])
    highs  = np.array([float(ohlcv_ts[d].get('2. high',  0) or 0) for d in window])
    lows   = np.array([float(ohlcv_ts[d].get('3. low',   0) or 0) for d in window])

    score = 0.0
    names = []

    # ── Head & Shoulders (bearish) ──
    if len(closes) >= 80:
        c80 = closes[-80:]
        peaks, troughs = _find_peaks_troughs(c80)
        if len(peaks) >= 3:
            p3 = peaks[-1]; p2 = peaks[-2]; p1 = peaks[-3]
            if (p2[1] > p1[1] and p2[1] > p3[1]
                    and abs(p1[1] - p3[1]) / p2[1] < 0.05):
                # neckline: troughs between shoulders
                t_left  = [t for t in troughs if p1[0] < t[0] < p2[0]]
                t_right = [t for t in troughs if p2[0] < t[0] < p3[0]]
                if t_left and t_right:
                    neckline = (t_left[-1][1] + t_right[0][1]) / 2.0
                    if closes[-1] < neckline * 0.99:
                        score -= 1.5
                        names.append('H&S↓')

    # ── Inverse Head & Shoulders (bullish) ──
    if len(closes) >= 80 and 'H&S↓' not in names:
        c80 = closes[-80:]
        peaks, troughs = _find_peaks_troughs(c80)
        if len(troughs) >= 3:
            t3 = troughs[-1]; t2 = troughs[-2]; t1 = troughs[-3]
            if (t2[1] < t1[1] and t2[1] < t3[1]
                    and abs(t1[1] - t3[1]) / max(t2[1], 1e-6) < 0.05):
                p_mid = [p for p in peaks if t1[0] < p[0] < t3[0]]
                if p_mid:
                    neckline = p_mid[len(p_mid) // 2][1]
                    if closes[-1] > neckline * 1.01:
                        score += 1.5
                        names.append('InvH&S↑')

    # ── Double Top (bearish) ──
    if len(closes) >= 40:
        peaks, _ = _find_peaks_troughs(closes)
        if len(peaks) >= 2:
            p2 = peaks[-1]; p1 = peaks[-2]
            gap = p2[0] - p1[0]
            if gap >= 15 and abs(p1[1] - p2[1]) / max(p1[1], 1e-6) < 0.03:
                valley = float(np.min(closes[p1[0]:p2[0] + 1]))
                if closes[-1] < valley * 0.99:
                    score -= 1.0
                    names.append('DblTop↓')

    # ── Double Bottom (bullish) ──
    if len(closes) >= 40:
        _, troughs = _find_peaks_troughs(closes)
        if len(troughs) >= 2:
            t2 = troughs[-1]; t1 = troughs[-2]
            gap = t2[0] - t1[0]
            if gap >= 15 and abs(t1[1] - t2[1]) / max(t1[1], 1e-6) < 0.03:
                peak = float(np.max(closes[t1[0]:t2[0] + 1]))
                if closes[-1] > peak * 1.01:
                    score += 1.0
                    names.append('DblBot↑')

    # ── Cup & Handle (bullish) ──
    if len(closes) >= 80:
        c80 = closes[-80:]
        cup_left_idx  = int(np.argmax(c80[-80:-50])) if len(c80) >= 80 else 0
        cup_left_price = float(c80[cup_left_idx])
        search_start = cup_left_idx
        search_end   = len(c80) - 10
        if search_end > search_start:
            cup_bottom_price = float(np.min(c80[search_start:search_end]))
            cup_depth = cup_left_price - cup_bottom_price
            cup_right_price  = float(np.max(c80[-15:]))
            handle_low = float(np.min(c80[-10:]))
            handle_range = float(np.max(c80[-10:])) - handle_low
            if (cup_depth >= cup_left_price * 0.10
                    and cup_right_price >= cup_left_price * 0.95
                    and handle_range < cup_depth * 0.5
                    and closes[-1] >= cup_left_price * 0.97):
                score += 1.5
                names.append('Cup&H↑')

    # ── Bull Flag ──
    if len(closes) >= 30:
        pole_gain = (float(np.max(closes[-25:-5])) - closes[-25]) / max(closes[-25], 1e-6)
        flag_range = (float(np.max(closes[-5:])) - float(np.min(closes[-5:]))) / max(closes[-5], 1e-6)
        if pole_gain >= 0.05 and flag_range <= 0.03:
            score += 0.75
            names.append('Flag↑')

    # ── Bear Flag ──
    if len(closes) >= 30 and 'Flag↑' not in names:
        pole_drop = (closes[-25] - float(np.min(closes[-25:-5]))) / max(closes[-25], 1e-6)
        flag_range = (float(np.max(closes[-5:])) - float(np.min(closes[-5:]))) / max(closes[-5], 1e-6)
        if pole_drop >= 0.05 and flag_range <= 0.03:
            score -= 0.75
            names.append('Flag↓')

    return round(max(-2.0, min(2.0, score)), 2), names


# ── Momentum pattern score ────────────────────────────────────────────────────

def momentum_pattern_score(ohlcv_ts: dict, date_str: str):
    """Detect MA crossovers and MACD signals.

    Returns (score, names_list). Score in [-2, +2].
    Needs >= 52 bars; returns (0.0, []) otherwise.
    """
    if not ohlcv_ts:
        return 0.0, []
    dates = sorted(ohlcv_ts.keys())
    past = [d for d in dates if d <= date_str]
    if len(past) < 52:
        return 0.0, []

    closes = [float(ohlcv_ts[d].get('4. close', 0) or 0) for d in past[-60:]]

    score = 0.0
    names = []

    # ── SMA20 vs SMA50 alignment + crossover ──
    if len(closes) >= 50:
        sma20 = sum(closes[-20:]) / 20.0
        sma50 = sum(closes[-50:]) / 50.0

        # Current alignment
        if sma20 > sma50:
            score += 0.5
        else:
            score -= 0.5

        # Recent crossover (last 6 bars)
        cross_name = None
        for k in range(1, 7):
            if len(closes) < 50 + k:
                break
            prev_s20 = sum(closes[-(20 + k):-k]) / 20.0
            prev_s50 = sum(closes[-(50 + k):-k]) / 50.0
            if prev_s20 <= prev_s50 and sma20 > sma50:
                score += 1.5
                cross_name = 'GoldX↑'
                break
            elif prev_s20 >= prev_s50 and sma20 < sma50:
                score -= 1.5
                cross_name = 'DeathX↓'
                break
        if cross_name:
            names.append(cross_name)

    # ── MACD crossover ──
    if len(closes) >= 40:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd  = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
        # Skip warmup (first 25 values of macd are unreliable)
        valid_macd = macd[25:]
        if len(valid_macd) >= 10:
            signal_line = _ema(valid_macd, 9)
            m1 = valid_macd[-1]
            m0 = valid_macd[-2]
            s1 = signal_line[-1]
            s0 = signal_line[-2]
            if s0 != 0 and s1 != 0:
                if m0 <= s0 and m1 > s1:
                    score += 1.0
                    names.append('MACD↑')
                elif m0 >= s0 and m1 < s1:
                    score -= 1.0
                    names.append('MACD↓')
                elif m1 > s1:
                    score += 0.3
                    names.append('MACD+')
                else:
                    score -= 0.3
                    names.append('MACD-')

    return round(max(-2.0, min(2.0, score)), 2), names


# ── Combined summary ──────────────────────────────────────────────────────────

def pattern_summary(ohlcv_ts: dict, date_str: str):
    """Combine all three pattern types into a single score and text label.

    Returns (pattern_score: float, pattern_text: str).
    Score uses PLACEHOLDER weights pending backtest calibration.

    Backtest integration: add candlestick_score / chart_score / momentum_score
    as separate fields in backtest_ratings.py compute_br() to measure spreads,
    then replace weights below with empirically derived values.
    """
    if not ohlcv_ts:
        return 0.0, ''

    cs           = candlestick_score(ohlcv_ts, date_str, lookback=5)
    cps, cp_names = chart_pattern_score(ohlcv_ts, date_str)
    ms,  mo_names = momentum_pattern_score(ohlcv_ts, date_str)

    # Weights calibrated from backtest (292k obs, 522 symbols, 2023-2025).
    # All three factors are contrarian: high score = lower fwd returns.
    # Spreads: candlestick 3.49%, chart 11.83%, momentum 5.76%.
    # Combined score is negated so positive = bullish input to short/long scores.
    pattern_score = -1.0 * (cs * 0.25 + cps * 0.45 + ms * 0.30)
    pattern_score = round(max(-2.0, min(2.0, pattern_score)), 2)

    text_parts = []
    if cs >= 1.0:
        text_parts.append('CS↑')
    elif cs <= -1.0:
        text_parts.append('CS↓')
    text_parts.extend(cp_names)
    text_parts.extend(mo_names)

    return pattern_score, ' '.join(text_parts)
