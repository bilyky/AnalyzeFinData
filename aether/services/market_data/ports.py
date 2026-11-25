"""Ports the market-data service depends on (ADR 0002: ``typing.Protocol``)."""
from typing import Protocol


class MarketClock(Protocol):
    """A live "is the market open right now?" answer, from a broker."""

    def is_open_now(self):
        """True or False from the broker; None when there is no answer (the caller decides)."""
        ...


class PriceSource(Protocol):
    """Quotes for symbols; a symbol with no price is left out. ``today`` is the date the prices
    are for (a source that needs it uses the local date when it is not given)."""

    def quotes(self, symbols, today=None) -> dict: ...


class BrokerQuotes(Protocol):
    """Live broker quotes. ``session()`` is None when there is no login; ``quotes`` needs one."""

    def session(self): ...

    def quotes(self, session, symbols) -> dict: ...


class OhlcvStore(Protocol):
    """Daily bars per symbol. ``series`` is the raw ``Time Series (Daily)`` mapping, or None
    when there is no cache for the symbol; unreadable data raises."""

    def path(self, symbol): ...

    def series(self, symbol): ...
