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
