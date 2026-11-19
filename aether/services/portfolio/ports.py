"""Ports the portfolio service depends on (ADR 0002: ``typing.Protocol``, no inheritance)."""
from typing import Protocol

from aether.core import PortfolioId
from aether.services.portfolio.domain.model import Portfolio


class PortfolioRepository(Protocol):
    """Loads and saves whole ``Portfolio`` aggregates (ADR 0003).

    Adapters: ``JsonPortfolioRepository`` (the default); ``SqlPortfolioRepository`` in S14.
    Both must pass the same contract tests (tests/test_portfolio_repository.py).
    """

    def load(self, portfolio_id: PortfolioId) -> Portfolio:
        """The stored portfolio, or a fresh one on a first run. Unknown id: ``UnknownPortfolio``."""
        ...

    def save(self, portfolio: Portfolio) -> None:
        ...
