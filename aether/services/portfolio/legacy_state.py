"""Anti-corruption layer for code that still works on the raw state dict (R&D #78).

The trading code (``ai_portfolio_game`` and ``aether.scenario``) changes the stored dict in
place. Until it moves onto the ``Portfolio`` methods in S8, it loads and saves through this
gateway. Every load and save still passes through the domain model and the repository, so the
model is proved on real state on every run. Deleted in S9.
"""
from aether.core import DEFAULT_PORTFOLIO, PortfolioId
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.app import PortfolioService


class LegacyStateGateway:
    def __init__(self, service: PortfolioService):
        self._service = service

    def load_state(self, portfolio_id: PortfolioId = DEFAULT_PORTFOLIO) -> dict:
        return json_mapper.to_dict(self._service.load(portfolio_id))

    def save_state(self, state: dict, portfolio_id: PortfolioId = DEFAULT_PORTFOLIO) -> None:
        with self._service.unit_of_work() as uow:
            uow.add(json_mapper.from_dict(portfolio_id, state))
            uow.commit()
