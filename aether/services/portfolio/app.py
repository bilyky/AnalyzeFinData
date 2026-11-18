"""Use cases of the portfolio service: load, save, and change portfolios in a unit of work."""
from aether.core import DEFAULT_PORTFOLIO, EventBus, PortfolioId
from aether.services.portfolio.domain.model import Portfolio
from aether.services.portfolio.ports import PortfolioRepository


class UnitOfWork:
    """Load once, change the aggregates, commit once (ADR 0003).

    ``commit()`` saves every portfolio loaded (or added) through this unit, then publishes the events they
    recorded, in order. Leaving the ``with`` block without ``commit()`` saves nothing. With the
    JSON store the events are published after the files are written; the DB store (S14) writes
    them to an outbox in the same transaction.
    """

    def __init__(self, repository: PortfolioRepository, events: EventBus):
        self._repository = repository
        self._events = events
        self._loaded: dict[PortfolioId, Portfolio] = {}

    def __enter__(self) -> "UnitOfWork":
        return self

    def __exit__(self, *exc) -> None:
        self._loaded.clear()

    def get(self, portfolio_id: PortfolioId = DEFAULT_PORTFOLIO) -> Portfolio:
        if portfolio_id not in self._loaded:
            self._loaded[portfolio_id] = self._repository.load(portfolio_id)
        return self._loaded[portfolio_id]

    def add(self, portfolio: Portfolio) -> None:
        """Register a portfolio built outside this unit (it replaces any loaded copy)."""
        self._loaded[portfolio.id] = portfolio

    def commit(self) -> None:
        for portfolio in self._loaded.values():
            self._repository.save(portfolio)
        events = [e for p in self._loaded.values() for e in p.pull_events()]
        self._events.publish(events)


class PortfolioService:
    def __init__(self, repository: PortfolioRepository, events: EventBus):
        self._repository = repository
        self._events = events

    def load(self, portfolio_id: PortfolioId = DEFAULT_PORTFOLIO) -> Portfolio:
        """A portfolio to read. Changes go through ``unit_of_work()``."""
        return self._repository.load(portfolio_id)

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._repository, self._events)
