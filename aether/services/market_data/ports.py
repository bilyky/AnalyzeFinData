"""Ports the market-data service depends on (ADR 0002: ``typing.Protocol``)."""
from typing import Protocol


class MarketClock(Protocol):
    """A live "is the market open right now?" answer, from a broker."""

    def is_open_now(self):
        """True or False from the broker; None when there is no answer (the caller decides)."""
        ...
