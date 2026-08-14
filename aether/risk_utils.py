import datetime
import json
import os
import pandas as pd
from pathlib import Path
from aether.config import CFG
from aether.logger import get_logger as _get_logger

_log = _get_logger("risk_utils")

BASE_DIR = Path(__file__).resolve().parent.parent
OHLCV_DIR = BASE_DIR / "Data" / "Symbol_full"

def calculate_atr(symbol, period=14):
    """Calculate the Average True Range (ATR) from local daily OHLCV files."""
    path = OHLCV_DIR / f"{symbol}_daily.json"
    if not path.exists():
        return None

    try:
        with open(path) as f:
            data = json.load(f)

        ts = data.get("Time Series (Daily)")
        if not ts:
            return None

        # Convert to DataFrame
        df = pd.DataFrame.from_dict(ts, orient="index")
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # De-duplicate columns (bug-fix: prevents Length mismatch on duplicate columns)
        df = df.loc[:, ~df.columns.duplicated()]

        # Select only standard Alpha Vantage columns by name to ignore provenance stamps/custom metadata
        standard_cols = ["1. open", "2. high", "3. low", "4. close", "5. volume"]
        existing_cols = [c for c in standard_cols if c in df.columns]
        df = df[existing_cols]

        # Safely rename columns to avoid length mismatch issues
        mapping = {
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. volume": "volume"
        }
        df = df.rename(columns=mapping)
        df = df.astype(float)

        # Split-adjust (Alpha-Vantage source is UNADJUSTED): a split inside the ATR
        # window otherwise spikes True Range and corrupts the stop distance. Reuse the
        # single tested adjuster; positions align (df is sorted chronological).
        _o, _h, _l, _c = (df["open"].tolist(), df["high"].tolist(),
                          df["low"].tolist(), df["close"].tolist())
        _h, _l, _c = _split_adjust_ohlcv(_o, _h, _l, _c)
        df["high"], df["low"], df["close"] = _h, _l, _c

        # True Range components
        df["h-l"] = df["high"] - df["low"]
        df["h-pc"] = (df["high"] - df["close"].shift(1)).abs()
        df["l-pc"] = (df["low"] - df["close"].shift(1)).abs()

        df["tr"] = df[["h-l", "h-pc", "l-pc"]].max(axis=1)

        # ATR is a simple moving average of TR
        atr = df["tr"].rolling(window=period).mean().iloc[-1]
        return round(atr, 2) if not pd.isna(atr) else None
    except Exception as e:
        _log.error(f"Error calculating ATR for {symbol}: {e}")
        return None

_SWING_LOOKBACK = 3     # days for the swing-low technical stop
_ATR_STOP_MULT  = 2.5   # ATR multiple for the volatility stop
_PCT_FALLBACK   = 0.08  # last-resort stop = price * (1 - 8%)
STALE_STOP_DAYS = 10    # OHLCV cache older than this -> don't trust swing-low/ATR


# --- split adjustment -------------------------------------------------------
# Alpha-Vantage daily bars are UNADJUSTED. Across a split, pre-split pivots sit at the
# wrong price scale and poison detect_support / detect_resistance / ATR (the leveraged
# cohort splits almost yearly — e.g. SQQQ reverse ~5x, TQQQ forward ~0.5x). We
# BACK-adjust so the series is continuous on the CURRENT scale (the newest bar keeps its
# real value), which is exactly the scale of the live `price` callers pass in. A
# day-over-day close ratio outside [_SPLIT_LO,_SPLIT_HI] is a split candidate, confirmed
# ONLY when the OPEN moved by ~the same factor (open-agreement guard) — so a real
# intraday crash (open near the prior close, only the close down) is PRESERVED, not
# adjusted away. Validated in scripts/backtesting/leveraged_levels_study.py.
_SPLIT_HI, _SPLIT_LO, _SPLIT_OPEN_AGREE = 1.8, 0.55, 0.35


def _split_adjust_ohlcv(opens, highs, lows, closes):
    """Back-adjust OHLC for detected splits onto one continuous (current) price scale.
    Returns (highs, lows, closes) — the originals unchanged when no split is found.
    `opens` are used only for split detection (the crash guard)."""
    n = len(closes)
    if n < 2:
        return highs, lows, closes
    factor = [1.0] * n
    cum = 1.0
    found = False
    # Walk newest->oldest: factor[i] holds the product of split ratios AFTER bar i, so a
    # detected split folds into cum and rescales every OLDER bar (newest stays nominal).
    for i in range(n - 1, 0, -1):
        factor[i] = cum
        prev_c = closes[i - 1]
        if prev_c <= 0:
            continue
        r_close = closes[i] / prev_c
        if r_close >= _SPLIT_HI or r_close <= _SPLIT_LO:
            op = opens[i] if i < len(opens) else None
            if not op or op <= 0:
                continue                       # can't verify open -> don't adjust
            r_open = op / prev_c
            if abs(r_open - r_close) <= _SPLIT_OPEN_AGREE * r_close:
                cum *= r_close
                found = True
    if not found:
        return highs, lows, closes
    factor[0] = cum
    return ([highs[i] * factor[i] for i in range(n)],
            [lows[i] * factor[i] for i in range(n)],
            [closes[i] * factor[i] for i in range(n)])


def _load_ohlcv_series(symbol, as_of=None):
    """(highs, lows, closes, last_date) chronological from the local OHLCV cache,
    SPLIT-ADJUSTED onto the current price scale; ([], [], [], None) when
    missing/unreadable. When as_of ('YYYY-MM-DD') is given, truncate to bars on/before
    that date (for entry-anchored, as-of-buy-date levels) BEFORE adjusting, so the
    series is on the entry date's scale."""
    path = OHLCV_DIR / f"{symbol}_daily.json"
    if not path.exists():
        return [], [], [], None
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        if as_of:
            dates = [d for d in dates if d <= as_of]
        if not dates:
            return [], [], [], None
        opens = [float(ts[d].get("1. open", 0) or 0) for d in dates]
        highs = [float(ts[d]["2. high"]) for d in dates]
        lows = [float(ts[d]["3. low"]) for d in dates]
        closes = [float(ts[d]["4. close"]) for d in dates]
        highs, lows, closes = _split_adjust_ohlcv(opens, highs, lows, closes)
        return highs, lows, closes, dates[-1]
    except Exception:
        return [], [], [], None


def _age_days(last_date, today=None):
    """Days since `last_date` (ISO string), or None if unparseable/missing."""
    if not last_date:
        return None
    try:
        d = datetime.date.fromisoformat(str(last_date)[:10])
        return ((today or datetime.date.today()) - d).days
    except Exception:
        return None


def ohlcv_age_days(symbol):
    """Age in days of a symbol's newest cached bar, or None if no usable cache."""
    return _age_days(_load_ohlcv_series(symbol)[3])


def _atr_from_series(highs, lows, closes, period=14):
    """ATR (simple average of True Range) from in-memory series, or None."""
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return None
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i - 1]),
               abs(lows[i] - closes[i - 1])) for i in range(1, n)]
    window = trs[-period:]
    return round(sum(window) / len(window), 2) if window else None


PIVOT_K        = 3    # bars each side that must confirm a swing (pivot) low/high
PIVOT_LOOKBACK = 60   # bars scanned back for swing lows/highs
_TARGET_RECENT = 10   # window for the shallow recent-high target fallback


def detect_support(price, lows, k=PIVOT_K, lookback=PIVOT_LOOKBACK):
    """Most recent CONFIRMED swing-low below `price` (Sperandeo / Williams-fractal
    standard): a bar whose low is the lowest within +-k bars, requiring k bars AFTER
    it to confirm — so the newest k bars (an unconfirmed dip) never qualify. Returns
    that support level, or None if there's no confirmed pivot below price.
    """
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0 or not lows or len(lows) < 2 * k + 1:
        return None
    n = len(lows)
    pivots = []
    for i in range(max(k, n - lookback), n - k):
        if lows[i] == min(lows[i - k:i + k + 1]):
            pivots.append(lows[i])
    # walk newest-first: the last swing low price still trades above is live support
    for low in reversed(pivots):
        if 0 < low < price:
            return low
    return None


def resolve_stop_detailed(price, symbol=None, highs=None, lows=None, closes=None,
                          max_stale_days=STALE_STOP_DAYS, exclude_swing=False, as_of=None):
    """Resolve a stop below `price` and report how it was derived.

    exclude_swing=True skips the long swing-low support (used for leveraged/inverse
    instruments) and goes straight to the ATR stop. as_of ('YYYY-MM-DD') computes the
    level from bars up to that date (entry-anchored held-position stop) and skips the
    staleness gate — an entry level is historical by design.

    Returns {'stop', 'source', 'support', 'age', 'stale'} where source is:
        support — confirmed swing-low (industry standard, preferred)
        swing   — min of the last 3 lows x 0.99 (shallow recent low)
        atr     — price - 2.5 x ATR(14)
        pct     — 8% off the live price (last resort)
        stale   — cache older than max_stale_days -> 8% off live price
        none    — price invalid / no usable data (stop is None)
    """
    out = {"stop": None, "source": "none", "support": None, "age": None, "stale": False}
    try:
        price = float(price)
    except (TypeError, ValueError):
        return out
    if price <= 0:
        return out

    if highs is None and lows is None and closes is None and symbol:
        highs, lows, closes, last = _load_ohlcv_series(symbol, as_of=as_of)
        out["age"] = _age_days(last)
        if as_of is None and (out["age"] is None or out["age"] > max_stale_days):
            out.update(stop=round(price * (1 - _PCT_FALLBACK), 2), source="stale", stale=True)
            return out
    highs, lows, closes = highs or [], lows or [], closes or []

    # Long swing-low support (skipped for leveraged/inverse — see instruments.py).
    if not exclude_swing:
        # 1. confirmed swing-low support (Sperandeo standard) — preferred
        support = detect_support(price, lows)
        if support:
            out.update(stop=round(support * 0.99, 2), source="support", support=round(support, 2))
            return out
        # 2. shallow recent swing low (min of last 3)
        recent = lows[-_SWING_LOOKBACK:]
        if recent:
            tech = round(min(recent) * 0.99, 2)
            if 0 < tech < price:
                out.update(stop=tech, source="swing")
                return out
    # 3. ATR-based stop
    atr = _atr_from_series(highs, lows, closes)
    if atr and atr > 0:
        s = round(price - _ATR_STOP_MULT * atr, 2)
        if 0 < s < price:
            out.update(stop=s, source="atr")
            return out
    # 4. percentage fallback
    out.update(stop=round(price * (1 - _PCT_FALLBACK), 2), source="pct")
    return out


def resolve_stop(price, symbol=None, highs=None, lows=None, closes=None,
                 max_stale_days=STALE_STOP_DAYS):
    """The stop price only (see resolve_stop_detailed for how it was derived)."""
    return resolve_stop_detailed(price, symbol=symbol, highs=highs, lows=lows,
                                 closes=closes, max_stale_days=max_stale_days)["stop"]


def detect_resistance(price, highs, k=PIVOT_K, lookback=PIVOT_LOOKBACK):
    """Nearest CONFIRMED swing-high above `price` — the overhead resistance a rally
    must clear (Williams up-fractal): a bar whose high is the max within +-k bars,
    requiring k bars AFTER to confirm. Returns that resistance level, or None.
    """
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0 or not highs or len(highs) < 2 * k + 1:
        return None
    n = len(highs)
    pivots = []
    for i in range(max(k, n - lookback), n - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            pivots.append(highs[i])
    above = [h for h in pivots if h > price]
    return min(above) if above else None   # nearest resistance overhead


def resolve_target_detailed(price, symbol=None, highs=None, lows=None, closes=None,
                            max_stale_days=STALE_STOP_DAYS, exclude_swing=False, as_of=None):
    """Resolve an upside target above `price` and report how it was derived.

    exclude_swing=True skips swing-high resistance (leveraged/inverse) -> ATR target.
    as_of ('YYYY-MM-DD') computes from bars up to that date (entry-anchored) and skips
    the staleness gate.

    Returns {'target', 'source', 'resistance', 'age', 'stale'} where source is:
        resistance — nearest confirmed swing-high (preferred)
        high       — max of the last 10 highs (shallow recent high)
        atr        — price + 2.5 x ATR(14) (blue-sky projection)
        pct        — +8% off the live price (last resort)
        stale      — cache older than max_stale_days -> +8% off live price
        none       — price invalid / no usable data (target is None)
    """
    out = {"target": None, "source": "none", "resistance": None, "age": None, "stale": False}
    try:
        price = float(price)
    except (TypeError, ValueError):
        return out
    if price <= 0:
        return out

    if highs is None and lows is None and closes is None and symbol:
        highs, lows, closes, last = _load_ohlcv_series(symbol, as_of=as_of)
        out["age"] = _age_days(last)
        if as_of is None and (out["age"] is None or out["age"] > max_stale_days):
            out.update(target=round(price * (1 + _PCT_FALLBACK), 2), source="stale", stale=True)
            return out
    highs, lows, closes = highs or [], lows or [], closes or []

    # Long swing-high resistance (skipped for leveraged/inverse — see instruments.py).
    if not exclude_swing:
        # 1. confirmed swing-high resistance (Williams up-fractal) — preferred
        r = detect_resistance(price, highs)
        if r:
            out.update(target=round(r, 2), source="resistance", resistance=round(r, 2))
            return out
        # 2. shallow recent high
        recent = highs[-_TARGET_RECENT:]
        if recent and max(recent) > price:
            out.update(target=round(max(recent), 2), source="high")
            return out
    # 3. ATR projection (blue-sky: no overhead resistance)
    atr = _atr_from_series(highs, lows, closes)
    if atr and atr > 0:
        out.update(target=round(price + _ATR_STOP_MULT * atr, 2), source="atr")
        return out
    # 4. percentage fallback
    out.update(target=round(price * (1 + _PCT_FALLBACK), 2), source="pct")
    return out


def get_position_size(price, stop_price, risk_usd=500):
    """Calculate shares based on Price - Stop gap."""
    if not price or not stop_price or price <= stop_price:
        return 0
    risk_per_share = price - stop_price
    return int(risk_usd // risk_per_share)

def get_atr_position_size(price, atr, risk_usd=500):
    """Calculate shares based on 2 * ATR risk (Volatility-based)."""
    if not price or not atr or atr <= 0:
        return 0
    # Common rule: Risk = 2 * ATR
    risk_per_share = 2 * atr
    return int(risk_usd // risk_per_share)

def is_elite_breakout_candidate(total_score: float, short10: float) -> bool:
    """Centralized, AI-agnostic quantitative validation gate for elite breakout leaders.
    Waives both Chaikin PGR and strict Risk-to-Reward limits when trend momentum is extreme.
    """
    score_floor = getattr(CFG, "system_bypass_score_floor", 8.0)
    s10_floor = getattr(CFG, "system_bypass_s10_floor", 2.0)
    return (total_score >= score_floor) and (short10 >= s10_floor)



