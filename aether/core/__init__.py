"""aether.core: the shared kernel of the AETHER services (R&D #78, ADR 0001/0002).

Small, stable building blocks every service may use, and nothing else:

* ``ids``: typed identifiers (``PortfolioId``).
* ``clock``: the ``Clock`` port, ``SystemClock`` for the app and ``FixedClock`` for tests.
* ``events``: ``DomainEvent`` and an in-process, ordered ``EventBus``.
* ``errors``: the exception types services raise.

The kernel depends only on the standard library. ``tests/test_architecture.py`` fails the
build if it imports anything else (another ``aether`` module, a third-party package or a
root script).
"""
from aether.core.clock import Clock, FixedClock, SystemClock
from aether.core.errors import AetherError, DomainError, StorageError
from aether.core.events import DomainEvent, EventBus
from aether.core.ids import DEFAULT_PORTFOLIO, PortfolioId

__all__ = [
    "AetherError",
    "Clock",
    "DEFAULT_PORTFOLIO",
    "DomainError",
    "DomainEvent",
    "EventBus",
    "FixedClock",
    "PortfolioId",
    "StorageError",
    "SystemClock",
]
