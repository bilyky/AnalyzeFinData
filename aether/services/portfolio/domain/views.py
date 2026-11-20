"""Read views of a portfolio (R&D #78 S4, plan P9: the read model). Pure: no I/O.

``PortfolioQueries`` hands these to the dashboard, reports and tools, so they never open the
state file or depend on how it is stored. They are frozen snapshots: changing a portfolio
goes through the service's unit of work, never through a view.

A value that was not stored, or was stored as null, is ``None`` here (readers apply their own
defaults). ``extra`` holds stored keys the model does not know yet, read-only.
"""
import copy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from aether.services.portfolio.domain.model import ABSENT, BuyDNA, Portfolio, Position, Transaction
from aether.services.portfolio.domain.records import as_record


def _v(value):
    return None if value is ABSENT else value


def _frozen(mapping) -> Mapping:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True)
class TransactionView:
    date: Any = None
    time: Any = None
    type: Any = None
    symbol: Any = None
    price: Any = None
    qty: Any = None
    pnl: Any = None
    details: Any = None
    stop_loss: Any = None
    _record: Mapping = field(default_factory=dict, repr=False, compare=False)

    def record(self) -> dict:
        """The transaction exactly as it was recorded (every key, in order), as a new dict."""
        return copy.deepcopy(dict(self._record))


@dataclass(frozen=True)
class WrittenCallView:
    """An open covered call on a position."""
    qty: Any = None
    strike: Any = None
    premium: Any = None
    expiration_date: Any = None
    sigma: Any = None


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: Any = None
    cost: Any = None
    stop_loss: Any = None
    highest_close_since_acq: Any = None
    is_scarcity: Any = None
    buy_date: Any = None
    written_call: WrittenCallView | None = None
    extra: Mapping = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class PortfolioView:
    portfolio_id: str
    balance: Any = None
    equity: Any = None
    profile: Any = None
    start_date: Any = None
    positions: tuple = ()  # PositionView, in stored order
    history: tuple = ()    # TransactionView, oldest first (stored order)

    @property
    def held_symbols(self) -> tuple:
        return tuple(p.symbol for p in self.positions)

    def written_call_symbols(self) -> list:
        """Symbols with an open covered call, sorted."""
        return sorted(p.symbol for p in self.positions if p.written_call is not None)


def transaction_view(tx: Transaction) -> TransactionView:
    return TransactionView(date=_v(tx.date), time=_v(tx.time), type=_v(tx.type), symbol=_v(tx.symbol),
                           price=_v(tx.price), qty=_v(tx.qty), pnl=_v(tx.pnl), details=_v(tx.details),
                           stop_loss=_v(tx.stop_loss), _record=_frozen(as_record(tx)))


def _written_call_view(value) -> WrittenCallView | None:
    if not value or not isinstance(value, dict):  # the game treats a falsy value as "no call"
        return None
    return WrittenCallView(qty=value.get("qty"), strike=value.get("strike"), premium=value.get("premium"),
                           expiration_date=value.get("expiration_date"), sigma=value.get("sigma"))


def position_view(symbol: str, pos: Position) -> PositionView:
    dna = pos.buy_dna
    buy_date = _v(dna.buy_date) if isinstance(dna, BuyDNA) else None
    return PositionView(symbol=symbol, qty=_v(pos.qty), cost=_v(pos.cost), stop_loss=_v(pos.stop_loss),
                        highest_close_since_acq=_v(pos.highest_close_since_acq), is_scarcity=_v(pos.is_scarcity),
                        buy_date=buy_date, written_call=_written_call_view(_v(pos.written_call)),
                        extra=_frozen(pos.extra))


def portfolio_view(portfolio: Portfolio) -> PortfolioView:
    positions = portfolio.positions if portfolio.positions is not ABSENT and portfolio.positions else {}
    history = portfolio.history if portfolio.history is not ABSENT and portfolio.history else []
    return PortfolioView(portfolio_id=str(portfolio.id), balance=_v(portfolio.balance), equity=_v(portfolio.equity),
                         profile=_v(portfolio.profile), start_date=_v(portfolio.start_date),
                         positions=tuple(position_view(s, p) for s, p in positions.items()),
                         history=tuple(transaction_view(t) for t in history))
