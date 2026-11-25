"""The per-symbol OHLCV JSON cache (``OhlcvStore`` adapter, R&D #78 S5).

One file per symbol, ``<dir>/<SYMBOL>_daily.json``, with a ``Time Series (Daily)`` mapping.
Read raw (no split adjustment), as the game's own readers did; the split-adjusted loader in
``aether.risk_utils`` is separate (F9).
"""
import json
from pathlib import Path


class JsonOhlcvStore:
    def __init__(self, symbol_dir):
        self.symbol_dir = Path(symbol_dir)

    def path(self, symbol) -> Path:
        return self.symbol_dir / f"{symbol}_daily.json"

    def series(self, symbol):
        """The ``Time Series (Daily)`` mapping; None when the file is missing. A file that is
        not valid JSON raises (callers decide what that means)."""
        path = self.path(symbol)
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f).get("Time Series (Daily)", {})
