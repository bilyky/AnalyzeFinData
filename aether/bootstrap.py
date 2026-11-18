"""Composition root (ADR 0002): builds the app's collaborators once and hands them out.

Today it only holds the shared kernel (a clock and an event bus). Each service added in
R&D #78 (S4 onward) registers its adapters here, chosen from config (JSON or DB, real or
fake), instead of looking them up at call time.
"""
from dataclasses import dataclass, field

from aether.core import Clock, EventBus, SystemClock


@dataclass(frozen=True)
class App:
    clock: Clock
    events: EventBus = field(default_factory=EventBus)


def bootstrap(*, clock: Clock | None = None) -> App:
    """The wired app. Tests pass a ``FixedClock``; the CLI and the dashboard pass nothing."""
    return App(clock=clock or SystemClock())
