"""Price sources (R&D #78 S5): E*TRADE quotes, Google Finance and the local OHLCV cache.

Each one is moved as-is from ``ai_portfolio_game`` (``etrade.ETradeClient`` quotes,
``get_live_google_price`` / ``get_google_prices_fallback``, ``get_json_prices_fallback``),
with the same log lines. ``aether.etrade`` and ``requests`` are looked up at call time, so a
patched client or HTTP call in a test applies.
"""
import datetime
import json
import re
from pathlib import Path

import requests

from aether import etrade
from aether.logger import get_logger
from aether.services.market_data.domain.prices import cached_close_is_current


class EtradeQuotes:
    """``BrokerQuotes`` over E*TRADE. ``session()`` is None when there is no token."""

    def __init__(self, log=None):
        self._log = log or get_logger("ai_game")

    def session(self):
        # The hardened get_tokens() is safe and has an active headless safety gate, so a run
        # always actively attempts to re-authenticate when tokens expire.
        client = etrade.ETradeClient("production", role="auth")
        tokens = client.auth.get_tokens()
        return (client, tokens) if tokens else None

    def quotes(self, session, symbols) -> dict:
        client, tokens = session
        return client.market.quotes(symbols, tokens)


class GooglePrices:
    """``PriceSource`` that scrapes Google Finance, one symbol at a time."""

    EXCHANGES = ("NASDAQ", "NYSE", "NYSEARCA", "AMEX")
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def __init__(self, log=None):
        self._log = log or get_logger("ai_game")

    def price(self, symbol):
        """Scrape the latest price from Google Finance; None if no exchange page has one."""
        for ex in self.EXCHANGES:
            url = f'https://www.google.com/finance/quote/{symbol}:{ex}'
            try:
                r = requests.get(url, headers=self.HEADERS, timeout=5)
                if r.status_code == 200:
                    # Regex looking specifically for the jsname="Pdsbrc" span enclosing the dollar price
                    match = re.search(r'jsname="Pdsbrc"[^>]*>\s*<span>\$([0-9,.]+)<', r.text)
                    if match:
                        return float(match.group(1).replace(',', ''))
            except Exception:
                pass
        return None

    def quotes(self, symbols, today=None) -> dict:
        quotes = {}
        self._log.console(f"  [Google] Scraping live quotes for: {symbols}")
        for sym in symbols:
            price = self.price(sym)
            if price and price > 0:
                quotes[sym] = price
                self._log.info(f"    - Google Verified {sym}: ${price:.2f}")
        return quotes


class OhlcvCloses:
    """``PriceSource`` over the per-symbol OHLCV JSON cache: the newest close, if current."""

    def __init__(self, symbol_dir, log=None):
        self.symbol_dir = Path(symbol_dir)
        self._log = log or get_logger("ai_game")

    def quotes(self, symbols, today=None) -> dict:
        """The newest current close per symbol. ``today`` defaults to the local date (#320 review:
        a missing ``today`` used to return {} with a misleading log line)."""
        today = today or datetime.date.today()
        quotes = {}
        try:
            for sym in symbols:
                path = self.symbol_dir / f"{sym}_daily.json"
                if path.exists():
                    with open(path) as f:
                        ts = json.load(f).get("Time Series (Daily)", {})
                    if ts:
                        newest_date = sorted(ts.keys())[-1]
                        if cached_close_is_current(newest_date, today):
                            quotes[sym] = float(ts[newest_date]["4. close"])
            return quotes
        except Exception as e:
            self._log.console(f"  [AETHER] Failed to load local JSON prices: {e}")
            return {}
