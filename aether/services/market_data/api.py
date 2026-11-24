"""Public interface of the market-data service (R&D #78 S5). API_VERSION follows semver."""
from aether.services.market_data.app import SessionService, run_scope
from aether.services.market_data.domain.session import in_trading_window, regular_hours
from aether.services.market_data.ports import MarketClock

API_VERSION = "0.1.0"

__all__ = ["API_VERSION", "MarketClock", "SessionService", "in_trading_window", "regular_hours", "run_scope"]
