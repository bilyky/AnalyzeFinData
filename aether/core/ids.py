"""Typed identifiers shared by the services."""
import re
from dataclasses import dataclass

_PORTFOLIO_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


@dataclass(frozen=True)
class PortfolioId:
    """The id of one portfolio (ADR 0004): lowercase letters, digits and underscores.

    The value is also used in file names (``Data/portfolios/<id>.json``) and lock names, so
    it is restricted to characters that are safe in both.
    """

    value: str

    def __post_init__(self):
        if not isinstance(self.value, str) or not _PORTFOLIO_ID.fullmatch(self.value):
            raise ValueError(f"invalid portfolio id {self.value!r}: use 1-40 of a-z, 0-9, _ and start with a letter")

    def __str__(self):
        return self.value


# The AI portfolio game that runs today. Its state file stays Data/ai_portfolio_game.json.
DEFAULT_PORTFOLIO = PortfolioId("ai_game")
