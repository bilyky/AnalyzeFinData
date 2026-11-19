"""Errors the portfolio service raises."""
from aether.core import DomainError


class UnknownPortfolio(DomainError):
    """No portfolio with this id is configured."""
