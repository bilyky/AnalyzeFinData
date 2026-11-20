"""JsonPortfolioQueries: read-only views over the JSON state files (plan P9).

It reads the same files as ``JsonPortfolioRepository`` but never writes: a missing file is a
portfolio that was never saved (``None``), and an empty or unreadable file raises
``StorageError``. Restoring from a backup stays the repository's job, on the next run's load.
"""
import json
from pathlib import Path

from aether.core import PortfolioId, StorageError
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.domain.views import PortfolioView, portfolio_view
from aether.services.portfolio.errors import UnknownPortfolio


class JsonPortfolioQueries:
    def __init__(self, files: dict):
        self._files = {k if isinstance(k, PortfolioId) else PortfolioId(str(k)): v for k, v in files.items()}

    def view(self, portfolio_id: PortfolioId) -> PortfolioView | None:
        try:
            f = self._files[portfolio_id]
        except KeyError:
            raise UnknownPortfolio(f"no store configured for portfolio {portfolio_id}") from None
        path = Path(f.path)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:  # ValueError covers an empty or invalid JSON file
            raise StorageError(f"state for {portfolio_id} cannot be read ({path.name}: {e})") from e
        return portfolio_view(json_mapper.from_dict(portfolio_id, state))
