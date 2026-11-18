"""Portfolio domain model (R&D #78 S4, ADR 0002). Pure: no I/O and no storage format.

- ``Portfolio`` is the aggregate root: one portfolio's cash, positions, history and queued
  orders. Rules that change it go through its methods, which record domain events.
- ``Position`` is an entity inside it (identity = its symbol).
- ``BuyDNA``, ``Transaction`` and ``QueuedOrder`` are value objects (frozen).

Two fields on every object exist only so storage round-trips exactly and are never read by a
rule: ``extra`` keeps keys the model does not know yet, and ``key_order`` keeps the order the
keys were stored in. A field that was not stored holds ``ABSENT`` (not ``None``, which is a
real JSON null).

Today the trading code still works on the stored dict (see ``PortfolioService.load_state``).
The open, close and scale methods move here with their callers in S8, where the behaviour
golden covers them. Until the F8 study, a stop can still go down, but only through the
explicit ``restore_stop``.
"""
from dataclasses import dataclass, field
from typing import Any

from aether.core import DomainError, DomainEvent, PortfolioId


class _Absent:
    __slots__ = ()

    def __repr__(self):
        return "ABSENT"

    def __bool__(self):
        return False


ABSENT: Any = _Absent()
"""A field that is not stored at all (different from a stored ``None``)."""


def _extra():
    """Stored keys the model does not know yet (see the module docstring)."""
    return field(default_factory=dict, repr=False)


def _order():
    """The order the keys were stored in (see the module docstring)."""
    return field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class BuyDNA:
    """The research facts at the time of the buy (used by the trade-DNA ledger)."""
    FIELDS = ("buy_date", "pgr", "s10", "l60", "score", "z_score", "industry")
    buy_date: Any = ABSENT
    pgr: Any = ABSENT
    s10: Any = ABSENT
    l60: Any = ABSENT
    score: Any = ABSENT
    z_score: Any = ABSENT
    industry: Any = ABSENT
    extra: dict = _extra()
    key_order: tuple = _order()


@dataclass(frozen=True)
class Transaction:
    """One history entry: BUY, SELL, BUY_SCALE_IN, COST_DEDUCTION, OPTION_* ..."""
    FIELDS = ("date", "time", "type", "symbol", "price", "qty", "pnl", "details", "stop_loss")
    date: Any = ABSENT
    time: Any = ABSENT
    type: Any = ABSENT
    symbol: Any = ABSENT
    price: Any = ABSENT
    qty: Any = ABSENT
    pnl: Any = ABSENT
    details: Any = ABSENT
    stop_loss: Any = ABSENT
    extra: dict = _extra()
    key_order: tuple = _order()


@dataclass(frozen=True)
class QueuedOrder:
    """An order queued after hours, run at the next session's open."""
    FIELDS = ("type", "symbol", "reason")
    type: Any = ABSENT
    symbol: Any = ABSENT
    reason: Any = ABSENT
    extra: dict = _extra()
    key_order: tuple = _order()


@dataclass(frozen=True)
class StopChanged(DomainEvent):
    portfolio_id: str
    symbol: str
    old: Any
    new: Any
    reason: str


@dataclass
class Position:
    """An open position. Entity: its identity is its symbol (the key in ``Portfolio.positions``)."""
    FIELDS = ("qty", "cost", "stop_loss", "is_scarcity", "buy_dna", "highest_close_since_acq",
              "banked_pct", "original_stop_loss", "breaker_stop_loss", "written_call")
    qty: Any = ABSENT
    cost: Any = ABSENT
    stop_loss: Any = ABSENT
    is_scarcity: Any = ABSENT
    buy_dna: Any = ABSENT  # BuyDNA when stored as an object
    highest_close_since_acq: Any = ABSENT
    banked_pct: Any = ABSENT
    original_stop_loss: Any = ABSENT
    breaker_stop_loss: Any = ABSENT
    written_call: Any = ABSENT  # owned by the options code; kept as stored
    extra: dict = _extra()
    key_order: tuple = _order()

    def raise_stop(self, new_stop) -> bool:
        """Move the stop up to ``new_stop``. A stop never goes down here; returns True if it moved."""
        if self.stop_loss is not ABSENT and self.stop_loss is not None and new_stop <= self.stop_loss:
            return False
        self.stop_loss = new_stop
        return True

    def restore_stop(self, stop) -> None:
        """Set the stop to ``stop`` even if lower: today's circuit-breaker restore (F8 studies it)."""
        self.stop_loss = stop

    def track_peak(self, close) -> None:
        """Keep the highest close since the buy."""
        peak = self.highest_close_since_acq
        if peak is ABSENT or peak is None or close > peak:
            self.highest_close_since_acq = close


@dataclass
class Portfolio:
    """Aggregate root for one portfolio (ADR 0002/0004)."""
    FIELDS = ("balance", "equity", "positions", "queued_orders", "profile", "start_date", "history",
              "profile_mode", "days_active", "iteration")
    id: PortfolioId
    balance: Any = ABSENT
    equity: Any = ABSENT
    positions: Any = ABSENT  # dict[str, Position]
    queued_orders: Any = ABSENT  # list[QueuedOrder]
    profile: Any = ABSENT
    start_date: Any = ABSENT
    history: Any = ABSENT  # list[Transaction]
    profile_mode: Any = ABSENT
    days_active: Any = ABSENT
    iteration: Any = ABSENT
    extra: dict = _extra()
    key_order: tuple = _order()
    _events: list = field(default_factory=list, repr=False, compare=False)

    @classmethod
    def new(cls, portfolio_id: PortfolioId, starting_capital: float, start_date: str,
            profile: str = "BALANCED") -> "Portfolio":
        """A fresh portfolio: all cash, nothing held (today's first-run state, key for key)."""
        return cls(id=portfolio_id, balance=starting_capital, equity=starting_capital, positions={},
                   history=[], start_date=start_date, profile=profile,
                   key_order=("balance", "equity", "positions", "history", "start_date", "profile"))

    def position(self, symbol: str) -> Position:
        held = self.positions if self.positions is not ABSENT else {}
        if symbol not in held:
            raise DomainError(f"{self.id}: no open position in {symbol}")
        return held[symbol]

    def raise_stop(self, symbol: str, new_stop, reason: str) -> bool:
        pos = self.position(symbol)
        old = pos.stop_loss
        moved = pos.raise_stop(new_stop)
        if moved:
            self._events.append(StopChanged(str(self.id), symbol, old, new_stop, reason))
        return moved

    def restore_stop(self, symbol: str, stop, reason: str) -> None:
        pos = self.position(symbol)
        old = pos.stop_loss
        pos.restore_stop(stop)
        if old != stop:
            self._events.append(StopChanged(str(self.id), symbol, old, stop, reason))

    def pull_events(self) -> list:
        """The events recorded since the last pull (the unit of work publishes them on commit)."""
        events, self._events = self._events, []
        return events
