"""A model object as a plain record: its stored keys in their stored order (R&D #78).

Used by the JSON mapper to write objects back exactly, and by the read views to hand a
transaction to the dashboard exactly as it was recorded. Pure: no I/O.
"""
import copy

from aether.services.portfolio.domain.model import ABSENT


def as_record(obj, convert=None) -> dict:
    """The stored keys in their stored order, then new fields, then new extra keys.

    A field holding ``ABSENT`` is left out; ``convert`` maps a field name to a function that
    turns its value into plain data (for nested model objects).
    """
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
