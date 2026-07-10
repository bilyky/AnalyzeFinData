import os
import json
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OHLCV_DIR = BASE_DIR / "Data" / "Symbol_full"

def calculate_atr(symbol, period=14):
    """Calculate the Average True Range (ATR) from local daily OHLCV files."""
    path = OHLCV_DIR / f"{symbol}_daily.json"
    if not path.exists():
        return None
    
    with open(path) as f:
        data = json.load(f)
    
    ts = data.get("Time Series (Daily)")
    if not ts:
        return None
    
    # Convert to DataFrame
    df = pd.DataFrame.from_dict(ts, orient="index")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    
    # Standard Alpha Vantage columns
    df.columns = ["open", "high", "low", "close", "volume"]
    df = df.astype(float)
    
    # True Range components
    df["h-l"] = df["high"] - df["low"]
    df["h-pc"] = (df["high"] - df["close"].shift(1)).abs()
    df["l-pc"] = (df["low"] - df["close"].shift(1)).abs()
    
    df["tr"] = df[["h-l", "h-pc", "l-pc"]].max(axis=1)
    
    # ATR is a simple moving average of TR
    atr = df["tr"].rolling(window=period).mean().iloc[-1]
    return round(atr, 2) if not pd.isna(atr) else None

_SWING_LOOKBACK = 3     # days for the swing-low technical stop
_ATR_STOP_MULT  = 2.5   # ATR multiple for the volatility stop
_PCT_FALLBACK   = 0.08  # last-resort stop = price * (1 - 8%)
STALE_STOP_DAYS = 10    # OHLCV cache older than this -> don't trust swing-low/ATR


def _load_ohlcv_series(symbol):
    """(highs, lows, closes, last_date) chronological from the local OHLCV cache;
    ([], [], [], None) when missing/unreadable."""
    path = OHLCV_DIR / f"{symbol}_daily.json"
    if not path.exists():
        return [], [], [], None
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        if not dates:
            return [], [], [], None
        return ([float(ts[d]["2. high"]) for d in dates],
                [float(ts[d]["3. low"]) for d in dates],
                [float(ts[d]["4. close"]) for d in dates],
                dates[-1])
    except Exception:
        return [], [], [], None


def _age_days(last_date, today=None):
    """Days since `last_date` (ISO string), or None if unparseable/missing."""
    if not last_date:
        return None
    try:
        import datetime
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


def resolve_stop(price, symbol=None, highs=None, lows=None, closes=None):
    """Best stop strictly below `price`, most-preferred first:
        1. swing-low  — min low of the last 3 bars x 0.99   (the screener's rule)
        2. ATR        — price - 2.5 x ATR(14)
        3. 8% fallback

    Provide OHLCV series (chronological) directly, or a `symbol` to load them from
    the cache. Returns a rounded stop < price, or None if `price` is invalid.
    """
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    stale = False
    if highs is None and lows is None and closes is None and symbol:
        highs, lows, closes, last = _load_ohlcv_series(symbol)
        age = _age_days(last)
        stale = age is None or age > STALE_STOP_DAYS
    highs, lows, closes = highs or [], lows or [], closes or []

    # Stale cache -> its swing-low/ATR are unreliable; use the % stop off the
    # (fresh) live price instead of a misleading old level.
    if stale:
        return round(price * (1 - _PCT_FALLBACK), 2)

    # 1. swing-low technical stop
    recent = lows[-_SWING_LOOKBACK:]
    if recent:
        tech = round(min(recent) * 0.99, 2)
        if 0 < tech < price:
            return tech
    # 2. ATR-based stop
    atr = _atr_from_series(highs, lows, closes)
    if atr and atr > 0:
        s = round(price - _ATR_STOP_MULT * atr, 2)
        if 0 < s < price:
            return s
    # 3. percentage fallback
    return round(price * (1 - _PCT_FALLBACK), 2)


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

if __name__ == "__main__":
    # Test
    print(f"ATR for AAPL: {calculate_atr('AAPL')}")
