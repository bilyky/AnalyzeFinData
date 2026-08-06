"""
OHLCV bar provenance predicates.

Leaf module (stdlib only) so both the RapidAPI recovery layer and the pattern/volume
consumers can share one definition of "is this bar real?" without importing each other.

Provenance model for a bar in Data/Symbol_full/{sym}_daily.json:
  - ``provisional`` — Chaikin close-only placeholder (fake open/high/low, volume 0).
    Written by powergauge._append_ohlcv_entry; repaired by the RapidAPI recovery pass.
  - ``verified``    — confirmed real by a RapidAPI fetch (stamped only when volume > 0).
  - neither         — legacy real bar (volume > 0), trusted and never re-fetched.
Invariant: a bar is never both ``verified`` and ``provisional`` (verified requires
volume > 0; provisional treats volume <= 0 as a placeholder).
"""


def is_provisional(bar: dict) -> bool:
    """True if a bar is a Chaikin close-only placeholder (no real volume/range).

    Trusts the explicit ``provisional`` marker first; ``volume == 0`` is a fallback for
    bars written before the marker existed (real Alpha Vantage bars always carry
    volume > 0). Volume/range consumers (MFI, RBR) must skip these bars.
    """
    if not isinstance(bar, dict):
        return False
    if bar.get("provisional"):
        return True
    try:
        return float(bar.get("5. volume", 0) or 0) <= 0
    except (TypeError, ValueError):
        return False


def is_verified(bar: dict) -> bool:
    """True if a bar was confirmed real by a RapidAPI fetch.

    Provenance signal for volume-confirmation consumers (MFI, RBR) that want RapidAPI-sourced
    volume specifically, not merely a non-provisional legacy bar. The recovery gate does NOT
    use this — it keys off ``is_provisional`` alone (a verified bar is non-provisional anyway).
    """
    return isinstance(bar, dict) and bool(bar.get("verified"))


def mark_verified(bar: dict) -> None:
    """Stamp a bar ``verified`` in place iff it carries real volume (> 0).

    A zero-volume bar from the API (e.g. a delisted symbol's final print, or a near-dead
    penny stock) is NOT a confirmation of real trading, so it must never be marked verified
    — that would contradict ``is_provisional`` (which treats volume==0 as a placeholder).
    """
    try:
        if float(bar.get("5. volume", 0) or 0) > 0:
            bar["verified"] = True
    except (TypeError, ValueError):
        pass
