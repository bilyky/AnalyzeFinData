"""Live prices: which source to ask, and filling the gaps (R&D #78 S5, plan P4).

Moved as-is from ``ai_portfolio_game.get_live_prices``, same order and log lines:

1. Weekend: the OHLCV cache, then Google for the gaps (no broker login).
2. Not market hours and today's workbook is already synced: the cache, then Google.
3. Otherwise the broker (E*TRADE). No login: Google for everything. Otherwise Google fills
   only the gaps, and symbols still without a price are logged as a symbology error.
4. Any error on the way: Google for everything.

``market_hours`` and ``workbook_fresh`` are callables, asked only when the rule reaches them
(market hours is a live clock question).
"""
from aether.logger import get_logger
from aether.services.market_data.domain.prices import missing
from aether.services.market_data.ports import BrokerQuotes, PriceSource


class PriceService:
    def __init__(self, broker: BrokerQuotes, google: PriceSource, cache: PriceSource, log=None):
        self._broker = broker
        self.google = google
        self.cache = cache
        self._log = log or get_logger("ai_game")

    def _cache_then_google(self, symbols, today):
        quotes = self.cache.quotes(symbols, today)
        gaps = missing(symbols, quotes)
        if gaps:
            self._log.console(f"  [AETHER] Local cache missing prices for {gaps}. Falling back to Google Finance.")
            quotes.update(self.google.quotes(gaps))
        return quotes

    def live_prices(self, symbols, *, today, market_hours, workbook_fresh) -> dict:
        log = self._log
        try:
            if today.weekday() in (5, 6):
                log.console("  [AETHER] Weekend detected — bypassing E*TRADE login.")
                log.console("  [AETHER] Attempting instant local price extraction from per-symbol JSON cache...")
                return self._cache_then_google(symbols, today)

            if not market_hours():
                if workbook_fresh():
                    log.console("  [AETHER] After-hours detected & workbook is ALREADY synced today.")
                    log.console("  [AETHER] Bypassing E*TRADE login to load directly from per-symbol JSON cache...")
                    return self._cache_then_google(symbols, today)
                log.console("  [AETHER] After-hours detected, but workbook is STALE/UN-SYNCED today.")
                log.console("  [AETHER] Connecting to E*TRADE to fetch fresh settled closes...")

            session = self._broker.session()
            if not session:
                log.warning("  [AETHER] E*TRADE authentication failed. Attempting Google Finance live fallback.")
                return self.google.quotes(symbols)

            quotes = self._broker.quotes(session, symbols)
            log.info(f"  [AETHER] Prices: E*TRADE quotes for {len(quotes)}/{len(symbols)} symbols.")

            # Fill only the gaps from Google — keep the E*TRADE quotes we already have, and
            # surface dead/delisted/misaligned tickers loudly.
            gaps = missing(symbols, quotes)
            if gaps:
                log.console(f"  [AETHER] E*TRADE returned no quote for {gaps} (possibly dead/delisted/misaligned); filling gaps via Google Finance.")
                quotes.update(self.google.quotes(gaps))
                still_missing = missing(gaps, quotes)
                if still_missing:
                    log.console(f"  [AETHER] SYMBOLOGY ERROR: No live quote from any source for: {still_missing}")
            return quotes
        except Exception as e:
            log.warning(f"  [AETHER] E*TRADE connection failed: {e}. Attempting Google Finance live fallback.")
            return self.google.quotes(symbols)
