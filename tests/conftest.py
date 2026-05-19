"""Shared fixtures and helpers for all test modules."""
import sys
import os

# Make project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta


def make_ohlcv(
    closes: list,
    volumes: list = None,
    highs: list = None,
    lows: list = None,
    start_date: str = "2024-01-02",
) -> dict:
    """Build a minimal Alpha Vantage Time Series dict from price lists.

    If highs/lows are omitted, ±0.5% bands are synthesised from consecutive
    close prices so the data looks plausible.
    """
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows = [c * 0.995 for c in closes]

    ts = {}
    d = date.fromisoformat(start_date)
    for i in range(n):
        prev = closes[i - 1] if i > 0 else closes[i]
        ts[str(d)] = {
            "1. open":   str(round(prev, 4)),
            "2. high":   str(round(highs[i], 4)),
            "3. low":    str(round(lows[i], 4)),
            "4. close":  str(round(closes[i], 4)),
            "5. volume": str(int(volumes[i])),
        }
        d += timedelta(days=1)
    return ts
