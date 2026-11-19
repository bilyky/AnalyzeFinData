"""Anti-corruption layer for code that still works on the raw state dict (R&D #78).

The trading code (``ai_portfolio_game`` and ``aether.scenario``) changes the stored dict in
place. Until it moves onto the ``Portfolio`` methods in S8, it loads and saves through this
gateway. It is a driving adapter: it turns the dict into the model with the JSON mapper and
calls the service, so every load and save still goes through the model and the repository.
``aether.bootstrap`` builds it (``App.portfolio_state``). Deleted in S9.

**A state the mapper refuses is never lost.** Before #295 the game wrote whatever dict it had.
Now a wrong shape (for example ``queued_orders`` set to ``None``) cannot be mapped, so the
save would fail and the run's changes would be lost. Instead the raw dict goes to a quarantine
file next to the state file, an ERROR is logged, and ``StorageError`` is raised. The run stops
loudly and the state can be repaired from that file.
"""
from aether.core import DEFAULT_PORTFOLIO, PortfolioId, StorageError
from aether.logger import get_logger
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.app import PortfolioService


class LegacyStateGateway:
    def __init__(self, service: PortfolioService, *, quarantine=None, log=None):
        self._service = service
        self._quarantine = quarantine  # callable(portfolio_id, state) -> where it was written
        self._log = log or get_logger("ai_game")

    def load_state(self, portfolio_id: PortfolioId = DEFAULT_PORTFOLIO) -> dict:
        return json_mapper.to_dict(self._service.load(portfolio_id))

    def save_state(self, state: dict, portfolio_id: PortfolioId = DEFAULT_PORTFOLIO) -> None:
        try:
            portfolio = json_mapper.from_dict(portfolio_id, state)
        except StorageError as e:
            where = None
            if self._quarantine:
                try:
                    where = self._quarantine(portfolio_id, state)
                except Exception as q:  # never hide the first error behind this one
                    where = f"nowhere (the quarantine write failed: {q})"
            self._log.error(f"  [PORTFOLIO] State for {portfolio_id} NOT saved: {e}. "
                            f"Unsaved state kept in: {where or 'nowhere (no quarantine configured)'}")
            raise StorageError(f"state for {portfolio_id} not saved ({e}); unsaved copy: {where}") from e
        with self._service.unit_of_work() as uow:
            uow.add(portfolio)
            uow.commit()
