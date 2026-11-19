"""Data Mapper between the stored JSON dict and the portfolio domain model (ADR 0003).

``to_dict(from_dict(pid, d)) == d`` for every state the game writes today, including the key
order (so ``json.dump`` gives the same bytes): unknown keys are kept in ``extra``, absent
fields stay absent, and values are not converted (an int stays an int).

A nested value of the wrong shape (for example ``positions`` that is not a dict of dicts)
raises ``StorageError``: the mapper never guesses.
"""
import copy

from aether.core import PortfolioId, StorageError
from aether.services.portfolio.domain.model import (ABSENT, BuyDNA, Portfolio, Position, QueuedOrder,
                                                   Transaction)


def _split(cls, data: dict, where: str):
    if not isinstance(data, dict):
        raise StorageError(f"{where}: expected an object, got {type(data).__name__}")
    known = {k: copy.deepcopy(v) for k, v in data.items() if k in cls.FIELDS}
    extra = {k: copy.deepcopy(v) for k, v in data.items() if k not in cls.FIELDS}
    return known, {"extra": extra, "key_order": tuple(data)}


def _join(obj, convert=None) -> dict:
    """The stored keys in their stored order, then new fields, then new extra keys."""
    convert = convert or {}
    out = {}

    def emit(key):
        if key in out:
            return
        if key in obj.FIELDS:
            value = getattr(obj, key)
            if value is ABSENT:
                return
            out[key] = convert[key](value) if key in convert else copy.deepcopy(value)
        elif key in obj.extra:
            out[key] = copy.deepcopy(obj.extra[key])

    for key in obj.key_order:
        emit(key)
    for key in obj.FIELDS:
        emit(key)
    for key in obj.extra:
        emit(key)
    return out


def _list(value, where, load):
    if not isinstance(value, list):
        raise StorageError(f"{where}: expected a list, got {type(value).__name__}")
    return [load(item, f"{where}[{i}]") for i, item in enumerate(value)]


def buy_dna_from_dict(data, where="buy_dna") -> BuyDNA:
    known, carry = _split(BuyDNA, data, where)
    return BuyDNA(**known, **carry)


def transaction_from_dict(data, where="transaction") -> Transaction:
    known, carry = _split(Transaction, data, where)
    return Transaction(**known, **carry)


def queued_order_from_dict(data, where="queued order") -> QueuedOrder:
    known, carry = _split(QueuedOrder, data, where)
    return QueuedOrder(**known, **carry)


def position_from_dict(data, where="position") -> Position:
    known, carry = _split(Position, data, where)
    if "buy_dna" in known and known["buy_dna"] is not None:
        known["buy_dna"] = buy_dna_from_dict(known["buy_dna"], f"{where}.buy_dna")
    return Position(**known, **carry)


def from_dict(portfolio_id: PortfolioId, data) -> Portfolio:
    known, carry = _split(Portfolio, data, f"portfolio {portfolio_id}")
    if "positions" in known:
        if not isinstance(known["positions"], dict):
            raise StorageError(f"portfolio {portfolio_id}.positions: expected an object")
        known["positions"] = {sym: position_from_dict(p, f"position {sym}") for sym, p in known["positions"].items()}
    if "history" in known:
        known["history"] = _list(known["history"], "history", transaction_from_dict)
    if "queued_orders" in known:
        known["queued_orders"] = _list(known["queued_orders"], "queued_orders", queued_order_from_dict)
    return Portfolio(id=portfolio_id, **known, **carry)


def _dump_value_object(obj) -> dict:
    return _join(obj)


def _dump_position(pos: Position) -> dict:
    return _join(pos, {"buy_dna": lambda v: _join(v) if isinstance(v, BuyDNA) else copy.deepcopy(v)})


def to_dict(portfolio: Portfolio) -> dict:
    return _join(portfolio, {
        "positions": lambda held: {sym: _dump_position(p) for sym, p in held.items()},
        "history": lambda txs: [_dump_value_object(t) for t in txs],
        "queued_orders": lambda qs: [_dump_value_object(q) for q in qs],
    })
