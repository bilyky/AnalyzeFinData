"""In-memory ``PortfolioRepository`` for tests and tools (no files).

It stores each portfolio as its mapped dict, so a loaded portfolio never shares objects with
a saved one (like a real store). It passes the same contract tests as the JSON repository.
"""
import datetime

from aether.core import PortfolioId
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.domain.model import Portfolio
from aether.services.portfolio.errors import UnknownPortfolio


class InMemoryPortfolioRepository:
    def __init__(self, portfolio_ids=None, *, starting_capital: float = 10000.0, today=None):
        self._known = None if portfolio_ids is None else set(portfolio_ids)
        self.starting_capital = starting_capital
        self._today = today or (lambda: str(datetime.date.today()))
        self.stored: dict[PortfolioId, dict] = {}

    def _check(self, portfolio_id: PortfolioId) -> None:
        if self._known is not None and portfolio_id not in self._known:
            raise UnknownPortfolio(f"no store configured for portfolio {portfolio_id}")

    def load(self, portfolio_id: PortfolioId) -> Portfolio:
        self._check(portfolio_id)
        if portfolio_id not in self.stored:
            return Portfolio.new(portfolio_id, self.starting_capital, self._today())
        return json_mapper.from_dict(portfolio_id, self.stored[portfolio_id])

    def save(self, portfolio: Portfolio) -> None:
        self._check(portfolio.id)
        self.stored[portfolio.id] = json_mapper.to_dict(portfolio)
