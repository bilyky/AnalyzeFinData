"""Public interface of the market-data service (R&D #78 S5). API_VERSION follows semver."""
from aether.services.market_data.app import SessionService, run_scope
from aether.services.market_data.domain.ohlcv import MAX_STALE_DAYS, age_days, closes, is_stale
from aether.services.market_data.domain.prices import cached_close_is_current, missing
from aether.services.market_data.domain.session import in_trading_window, regular_hours
from aether.services.market_data.ports import BrokerQuotes, MarketClock, OhlcvStore, PriceSource
from aether.services.market_data.ohlcv_app import OhlcvService
from aether.services.market_data.prices_app import PriceService

API_VERSION = "0.3.0"  # 0.3: OhlcvService (0.2: PriceService)

__all__ = ["API_VERSION", "BrokerQuotes", "MAX_STALE_DAYS", "MarketClock", "OhlcvService", "OhlcvStore",
           "PriceService", "PriceSource", "SessionService", "age_days", "cached_close_is_current", "closes",
           "in_trading_window", "is_stale", "missing", "regular_hours", "run_scope"]
