"""
Unified deterministic exit policy — the single source of truth for sell/status
decisions across the AI game, the Short_Long labels, and advisory surfaces.

Order of authority (never violated):
    1. HARD FLOOR  — ATR/percent stop-loss breach  -> SELL   (Rule of Loss Minimization)
    2. SOFT SIGNAL — momentum decay                 -> SELL, unless winner-protected -> REVIEW
    3. otherwise                                     -> HOLD

Pure functions, no I/O, no LLM calls. The soft threshold currently mirrors the
game's pre-existing rule (S10+L60 < 0) to preserve behavior; Phase 2 will
backtest-tune it. Winner-protection only ever downgrades the SOFT signal — it
can never suppress the hard stop.
"""

# ── Status label (L60-based; matches legacy excel_output thresholds) ────────────

_STATUS_CFG = [
    (4,    "STRONG HOLD"),
    (2,    "HOLD"),
    (0,    "WATCH"),
    (-2,   "REDUCE"),
    (-999, "EXIT"),
]


def status_label(l60) -> str:
    """Map the 60-day position score to a display status. EXIT when L60 < -2."""
    if l60 is None:
        return "N/A"
    for thresh, label in _STATUS_CFG:
        if l60 >= thresh:
            return label
    return "EXIT"


# ── Soft momentum exit signal ──────────────────────────────────────────────────

def soft_exit(s10, l60) -> bool:
    """Soft momentum-decay exit. Currently (S10 + L60) < 0 — the game's existing
    rule, preserved unchanged. Threshold to be backtest-tuned in Phase 2."""
    return ((s10 or 0) + (l60 or 0)) < 0


# ── Winner protection (soft layer only) ────────────────────────────────────────

def sma_from_closes(closes, period: int = 50):
    """Simple moving average of the last `period` closes, or None if insufficient."""
    if not closes or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def winner_protected(in_profit, price, sma50) -> bool:
    """A soft exit on a position that is in profit AND above its 50-day MA is a
    'flower' — downgrade to REVIEW rather than SELL. Never applies to the hard stop."""
    if not in_profit:
        return False
    if price is None or sma50 is None or sma50 <= 0:
        return False
    return price >= sma50


# ── The unified decision ───────────────────────────────────────────────────────

def exit_decision(price, cost, stop_loss, s10, l60, sma50=None, in_profit=None):
    """Return (action, reason) where action is 'SELL' | 'REVIEW' | 'HOLD'.

    price      current market price (required for any exit)
    cost       entry/cost basis (used to derive in_profit if not given)
    stop_loss  hard stop price (ATR- or percent-based); 0/None disables the floor
    s10, l60   momentum scores
    sma50      50-day SMA for winner-protection (optional)
    in_profit  override; derived from price > cost if None
    """
    if in_profit is None and price is not None and cost:
        in_profit = price > cost

    # 1. HARD FLOOR — capital preservation, always wins.
    if stop_loss and price is not None and price > 0 and price <= stop_loss:
        return "SELL", f"ATR stop breached (price {price} <= stop {stop_loss})"

    # 2. SOFT SIGNAL — momentum decay, with winner-protection.
    if soft_exit(s10, l60):
        combined = round((s10 or 0) + (l60 or 0), 1)
        if winner_protected(in_profit, price, sma50):
            return "REVIEW", f"soft exit (S10+L60={combined}) but winner above 50-DMA — review, don't dump"
        return "SELL", f"momentum decay (S10+L60={combined})"

    # 3. HOLD
    return "HOLD", ""
