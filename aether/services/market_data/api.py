"""Public interface of the market-data service (R&D #78 S5). API_VERSION follows semver."""
from aether.services.market_data.app import SessionService, run_scope
from aether.services.market_data.domain.prices import cached_close_is_current, missing
from aether.services.market_data.domain.session import in_trading_window, regular_hours
from aether.services.market_data.ports import BrokerQuotes, MarketClock, PriceSource
from aether.services.market_data.prices_app import PriceService

API_VERSION = "0.2.0"  # 0.2: PriceService and the price sources

__all__ = ["API_VERSION", "BrokerQuotes", "MarketClock", "PriceService", "PriceSource", "SessionService",
           "cached_close_is_current", "in_trading_window", "missing", "regular_hours", "run_scope"]
