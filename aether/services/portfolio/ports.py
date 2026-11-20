"""Ports the portfolio service depends on (ADR 0002: ``typing.Protocol``, no inheritance)."""
from typing import Protocol

from aether.core import PortfolioId
from aether.services.portfolio.domain.model import Portfolio
from aether.services.portfolio.domain.views import PortfolioView


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


class PortfolioQueries(Protocol):
    """Read-only views for the dashboard, reports and tools (plan P9, CQRS-lite).

    Never writes: no restore from a backup, no copy of a broken file. Adapters:
    ``JsonPortfolioQueries`` (the default) and ``InMemoryPortfolioQueries``; both pass the same
    contract tests (tests/test_portfolio_queries.py).
    """

    def view(self, portfolio_id: PortfolioId) -> PortfolioView | None:
        """The portfolio as stored; None if it was never saved. Unreadable: ``StorageError``."""
        ...
