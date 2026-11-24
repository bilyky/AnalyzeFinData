"""Use cases of the market-data service: is the market open (R&D #78 S5)."""
import contextlib
import contextvars

from aether.core import Clock
from aether.logger import get_logger
from aether.services.market_data.domain.session import in_trading_window, regular_hours
from aether.services.market_data.ports import MarketClock

# One run's answers. Inside ``run_scope()`` the live clock is asked once and the answer is
# reused for the rest of the run; outside a scope nothing is kept (each call asks again, as
# before). Transitional: from S8 the run passes its session to each stage instead.
_RUN = contextvars.ContextVar("market_data_run", default=None)


@contextlib.contextmanager
def run_scope():
    token = _RUN.set({})
    try:
        yield
    finally:
        _RUN.reset(token)


class SessionService:
    def __init__(self, clock: Clock, market_clock: MarketClock, log=None):
        self._clock = clock
        self._market_clock = market_clock
        self._log = log or get_logger("ai_game")

    def market_open(self, now=None) -> tuple:
        """The run gate: (open, message). See ``domain.session.regular_hours``."""
        return regular_hours(now if now is not None else self._clock.now())

    def market_hours(self, now=None) -> bool:
        """In the trading window and, if E*TRADE answers, open by its live clock."""
        try:
            if not in_trading_window(now if now is not None else self._clock.now()):
                return False
            is_open = self._live_open()
            return True if is_open is None else is_open
        except Exception:
            self._log.warning("is_market_hours: timezone check failed — defaulting to False (skip run).")
            return False  # safe default: skip trade rather than run at unknown hours

    def _live_open(self):
        run = _RUN.get()
        if run is None:
            return self._market_clock.is_open_now()
        if "live_open" not in run:
            run["live_open"] = self._market_clock.is_open_now()
        return run["live_open"]
