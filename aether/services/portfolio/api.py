"""Public interface of the portfolio service: the only module other code may import.

Callers name a portfolio by ``PortfolioId`` and never see how it is stored; the store is
chosen in ``aether.bootstrap`` (JSON by default). API_VERSION follows semver (ADR 0004).
"""
from aether.services.portfolio.app import PortfolioService, UnitOfWork
from aether.services.portfolio.domain.model import (ABSENT, BuyDNA, Portfolio, Position, QueuedOrder,
                                                   StopChanged, Transaction)
from aether.services.portfolio.errors import UnknownPortfolio
from aether.services.portfolio.legacy_state import LegacyStateGateway
from aether.services.portfolio.ports import PortfolioRepository

API_VERSION = "0.1.0"

__all__ = ["ABSENT", "API_VERSION", "BuyDNA", "LegacyStateGateway", "Portfolio", "PortfolioRepository",
           "PortfolioService", "Position", "QueuedOrder", "StopChanged", "Transaction", "UnitOfWork",
           "UnknownPortfolio"]
