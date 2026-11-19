"""Composition root (ADR 0002): builds the app's collaborators once and hands them out.

It chooses each service's adapters from configuration and injects them by constructor, so
no service looks anything up at call time. Tests pass fakes in, for example
``bootstrap(clock=FixedClock(...), portfolio_repository=InMemoryPortfolioRepository())``.

**Portfolio store (ADR 0003):** JSON files by default. ``AETHER_PORTFOLIO_STORE=db`` is the
only switch to the database, and that store arrives in S14, so today it fails loudly instead
of falling back. A ``DATABASE_URL`` alone never changes the store (the lesson from #144).
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from aether.core import DEFAULT_PORTFOLIO, Clock, EventBus, StorageError, SystemClock
from aether.services.portfolio.adapters.json_repository import JsonPortfolioRepository, PortfolioFile
from aether.services.portfolio.adapters.legacy_state import LegacyStateGateway
from aether.services.portfolio.api import PortfolioRepository, PortfolioService

PORTFOLIO_STORE_ENV = "AETHER_PORTFOLIO_STORE"
STARTING_CAPITAL = 10000.0

__all__ = ["App", "LegacyStateGateway", "PortfolioFile", "bootstrap", "default_portfolio_files", "portfolio_store_kind"]

# The checkout's Data/ folder, where the game keeps its state today. This is the one place
# that names the game's state file (the game itself asks bootstrap). Whether the state should
# follow $AETHER_DATA_DIR instead is open item B9 (it would also move the E*TRADE token), so
# this deliberately does not read it. The test harness points it at a temp dir.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "Data"


def default_portfolio_files() -> dict:
    return {DEFAULT_PORTFOLIO: PortfolioFile(DEFAULT_DATA_DIR / "ai_portfolio_game.json",
                                             DEFAULT_DATA_DIR / "Backup" / "Game")}


def portfolio_store_kind() -> str:
    kind = (os.environ.get(PORTFOLIO_STORE_ENV) or "json").strip().lower()
    if kind == "json":
        return kind
    if kind == "db":
        raise StorageError(f"{PORTFOLIO_STORE_ENV}=db: the database store is not built yet "
                           "(R&D #78 S14); unset it to use the JSON files")
    raise StorageError(f"{PORTFOLIO_STORE_ENV}={kind!r}: expected 'json' or 'db'")


@dataclass(frozen=True)
class App:
    clock: Clock
    portfolio: PortfolioService
    portfolio_state: LegacyStateGateway  # the raw state dict for the game, until S9
    events: EventBus = field(default_factory=EventBus)


def bootstrap(*, clock: Clock | None = None, portfolio_repository: PortfolioRepository | None = None,
              portfolio_files: dict | None = None, starting_capital: float = STARTING_CAPITAL,
              log=None) -> App:
    """The wired app. The CLI and the dashboard pass nothing; tests pass fakes or temp paths."""
    events = EventBus()
    if portfolio_repository is None:
        portfolio_store_kind()  # json, or a loud error
        portfolio_repository = JsonPortfolioRepository(
            portfolio_files if portfolio_files is not None else default_portfolio_files(),
            starting_capital=starting_capital, log=log)
    service = PortfolioService(portfolio_repository, events)
    gateway = LegacyStateGateway(service, quarantine=getattr(portfolio_repository, "quarantine", None), log=log)
    return App(clock=clock or SystemClock(), portfolio=service, portfolio_state=gateway, events=events)
