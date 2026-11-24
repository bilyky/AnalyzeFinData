"""The live market clock from E*TRADE (``MarketClock`` adapter).

Moved as-is from step 4 of ``ai_portfolio_game.is_market_hours``: ask E*TRADE whether the
market is open now. ``None`` means "no answer" (no token, no reply, or an error), and the
caller then trusts the static checks. ``aether.etrade`` is looked up at call time, so a
patched client in a test applies.
"""
from aether import etrade
from aether.logger import get_logger


class EtradeMarketClock:
    def __init__(self, log=None):
        self._log = log or get_logger("ai_game")

    def is_open_now(self):
        try:
            _et = etrade.ETradeClient("production", role="auth")
            tokens = _et.auth.get_tokens()
            if tokens:
                is_open = _et.market.is_open_now(tokens)
                if is_open is not None:
                    if not is_open:
                        self._log.console("  [AETHER] Dynamic Market Checks confirm market is CLOSED (Holiday/Weekend).")
                    return is_open
        except Exception as e:
            self._log.warning(f"  [AETHER] Dynamic market clock failed: {e}. Falling back to static datetime checks.")
        return None
