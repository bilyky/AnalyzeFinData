"""Ports the portfolio service depends on (ADR 0002: ``typing.Protocol``, no inheritance)."""
from typing import Protocol


class StateStore(Protocol):
    """Raw portfolio state, read and written as one JSON-shaped dict.

    The lowest storage layer: it owns the file (or table), backups and recovery. The domain
    mapper and ``PortfolioRepository`` (S4b) sit on top of it.
    """

    def load(self) -> dict: ...

    def save(self, state: dict) -> None: ...
