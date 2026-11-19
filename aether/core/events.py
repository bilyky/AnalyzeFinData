"""Domain events and an in-process event bus (ADR 0002, P7).

A service records what happened as events (``PositionClosed``, ...); after its unit of work
commits, the events are published to subscribers (trade-DNA ledger, Excel log, ...). This
keeps side effects out of the domain, with one writer per side effect.

The bus is synchronous and ordered: events go out in the order given, and each event's
handlers run in the order they subscribed. A handler error is not swallowed: it propagates
to the publisher, so a failed side effect is never silent.
"""
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class DomainEvent:
    """Base class for events. Subclasses are frozen dataclasses carrying plain values."""


Handler = Callable[[DomainEvent], None]


class EventBus:
    def __init__(self):
        self._handlers: list[tuple[type, Handler]] = []

    def subscribe(self, event_type: type, handler: Handler) -> None:
        """Call ``handler`` for every published event that is an instance of ``event_type``."""
        if not (isinstance(event_type, type) and issubclass(event_type, DomainEvent)):
            raise TypeError(f"{event_type!r} is not a DomainEvent subclass")
        self._handlers.append((event_type, handler))

    def publish(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            if not isinstance(event, DomainEvent):
                raise TypeError(f"{event!r} is not a DomainEvent")
            for event_type, handler in self._handlers:
                if isinstance(event, event_type):
                    handler(event)
