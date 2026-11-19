"""The Clock port: services ask it for the time instead of calling datetime.now().

Reading the time through one object makes a run's "now" consistent across its steps and
lets tests fix it without patching the ``datetime`` module.
"""
import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime.datetime:
        """The current time, timezone-aware (UTC)."""
        ...


class SystemClock:
    """The real time."""

    def now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc)


class FixedClock:
    """A clock stopped at one instant, for tests. ``advance`` moves it forward."""

    def __init__(self, at: datetime.datetime):
        if at.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._at = at.astimezone(datetime.timezone.utc)

    def now(self) -> datetime.datetime:
        return self._at

    def advance(self, delta: datetime.timedelta) -> None:
        self._at += delta
