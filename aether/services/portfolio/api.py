"""Public interface of the portfolio service: the only module other code may import.

Callers name a portfolio by ``PortfolioId`` and never see how it is stored; the store is
chosen in ``aether.bootstrap`` (JSON by default). API_VERSION follows semver (ADR 0004).
"""
from aether.services.portfolio.app import PortfolioService, UnitOfWork
from aether.services.portfolio.domain.model import (ABSENT, BuyDNA, Portfolio, Position, QueuedOrder,
                                                   StopChanged, Transaction)
from aether.services.portfolio.domain.views import (PortfolioView, PositionView, TransactionView,
                                                   WrittenCallView)
from aether.services.portfolio.errors import UnknownPortfolio
from aether.services.portfolio.ports import PortfolioQueries, PortfolioRepository

API_VERSION = "0.2.0"  # 0.2: PortfolioQueries and the read views

__all__ = ["ABSENT", "API_VERSION", "BuyDNA", "Portfolio", "PortfolioQueries", "PortfolioRepository", "PortfolioView",
           "PortfolioService", "Position", "PositionView", "QueuedOrder", "StopChanged", "Transaction", "TransactionView", "UnitOfWork",
           "UnknownPortfolio", "WrittenCallView"]
