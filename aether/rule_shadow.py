"""Buy rules in shadow (plan stage T0.4): record what a candidate rule would have done, without changing a decision.

A rule that passed its study is not wired straight into the game: it runs here first, on PROD, for two weeks or
more, and the weekly reflection compares its picks with what actually happened. The first rule in shadow is the
R&D #113 loss filter (``aether.buy_specs``): the Oracle already applies it; the game does not, so for every game
buy the shadow log says whether #113 would have held it back.

  * :func:`record` builds one entry (pure: the rule's verdict on the closes it is given);
  * :func:`log` is the only writer of ``ledgers.RULE_SHADOW_FILE`` (append-only JSON lines, best-effort, never
    raises, bounded like the decision log);
  * :func:`score` reads the entries back against later closes: forward return of the names the rule would have
    held back vs the ones it admitted. Descriptive; a decision to wire the rule needs the pre-registered bar.
"""
from __future__ import annotations

import json
from pathlib import Path

from aether import buy_specs, ledgers
from aether.logger import get_logger

_log = get_logger("rule_shadow")
MAX_LINES = 20000
HORIZONS = (5, 20)


def record(source: str, symbol: str, date: str, price: float, rr_waived: bool, closes: list,
           admitted: bool) -> dict:
    """One shadow entry: the live decision (``admitted``) and what the #113 loss filter says about it."""
    reasons = buy_specs.loss_filter_reasons(rr_waived, closes or [])
    return {"date": date, "source": source, "symbol": symbol, "price": price, "admitted": bool(admitted),
            "rule": "R&D #113", "would_hold_back": bool(reasons), "reasons": reasons,
            "flags": buy_specs.loss_flags(closes or []), "rr_waived": bool(rr_waived)}


def log(entries: list, path=None) -> None:
    """Append entries (best-effort: a write failure is a warning, never a failed run)."""
    if not entries:
        return
    try:
        p = Path(path or ledgers.RULE_SHADOW_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LINES:
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LINES:])
    except Exception as e:
        _log.warning(f"[rule_shadow] log write failed: {e}")


def read(path=None) -> list:
    p = Path(path or ledgers.RULE_SHADOW_FILE)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def score(entries: list, closes_by_symbol: dict, horizons=HORIZONS) -> dict:
    """Mean forward return by group ("would_hold_back" vs "admitted_by_rule") for each horizon, over entries whose
    window has passed. ``closes_by_symbol``: {symbol: {date: close}}. Descriptive only, with n."""
    out = {}
    for h in horizons:
        groups = {"would_hold_back": [], "admitted_by_rule": []}
        for e in entries:
            series = closes_by_symbol.get(e["symbol"]) or {}
            days = sorted(d for d in series if d >= e["date"])
            if len(days) <= h or days[0] != e["date"]:
                continue
            r = series[days[h]] / series[days[0]] - 1
            groups["would_hold_back" if e["would_hold_back"] else "admitted_by_rule"].append(r)
        out[h] = {g: {"n": len(v), "mean": (sum(v) / len(v)) if v else None} for g, v in groups.items()}
    return out
