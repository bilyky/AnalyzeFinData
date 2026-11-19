"""Behaviour reflection: how the paper game's own buys and sells worked out.

The weekly retrospective (retrospective_analyzer.py) looks at missed winners and factor
penalties. This module looks at the game's own actions, from the ledger history in
Data/ai_portfolio_game.json, so a change in behaviour shows up week to week:

a. sells by exit type per month, and what the stock did 10 sessions after the sale vs SPY;
b. entry quality: each new BUY's 10-session result vs SPY, per month;
c. score whipsaw: buys whose S10+L60 (decision log) fell below 0 within 3 logged days;
d. turnover: buys and sells per month, median days held;
e. pyramid after bank: a Rule B scale-out followed by a scale-in on the same symbol within
   5 sessions, and the P&L of those positions.

Read-only: nothing here changes a trading rule. ``reflect`` is pure (no file access); the
caller passes a ``closes_for(symbol) -> (days, closes)`` loader. ``load_closes`` is that
loader for the real cache (split-adjusted, placeholder bars dropped — aether.pick_eval).
Forward results run from the close on the trade date (or the last bar before it) to the
close ``horizon`` bars later, for the stock and for SPY.
"""
import datetime
import statistics
from collections import defaultdict

from aether import pick_eval

HORIZON = 10          # sessions after a buy or a sale
WHIPSAW_DAYS = 3      # logged days after a buy
PYRAMID_WINDOW = 5    # sessions from a scale-out to a scale-in

EXIT_TYPES = ("stop", "momentum_decay", "rule_b", "rotation", "other", "no_reason")
_EXIT_LABEL = {"stop": "stop", "momentum_decay": "mom decay", "rule_b": "Rule B bank",
               "rotation": "rotation", "other": "other", "no_reason": "no reason"}


def exit_type(details) -> str:
    """Exit type of a SELL from its ledger ``details`` text."""
    d = str(details or "").strip().lower()
    if not d:
        return "no_reason"
    if "scale-out" in d or "bank-as-you-go" in d:
        return "rule_b"
    if "stop breached" in d or "stop-loss" in d or "stop loss" in d:
        return "stop"
    if "momentum decay" in d:
        return "momentum_decay"
    if "momentum rotation" in d:
        return "rotation"
    return "other"


def _is_scale_in(tx) -> bool:
    return str(tx.get("type", "")).startswith("BUY_S") or "pyramiding scale-in" in str(tx.get("details") or "").lower()


def _bar_index(days, day):
    """Index of the last bar on or before ``day`` (None when there is none)."""
    idx = None
    for k, d in enumerate(days):
        if d > day:
            break
        idx = k
    return idx


def forward_return(series, day, horizon=HORIZON):
    """% change from the close on/before ``day`` to the close ``horizon`` bars later, or None."""
    days, closes = series
    i = _bar_index(days, day)
    if i is None or i + horizon >= len(days) or not closes[i]:
        return None
    return (closes[i + horizon] / closes[i] - 1) * 100


def _sessions_between(days, d1, d2):
    i, j = _bar_index(days, d1), _bar_index(days, d2)
    return None if i is None or j is None else j - i


def _median(xs):
    return statistics.median(xs) if xs else None


def _vs_spy(tx, closes_for, spy, horizon):
    """(stock %, SPY %) ``horizon`` sessions after the trade date, or None when too new / no bars."""
    stock = forward_return(closes_for(tx["symbol"]), tx["date"], horizon)
    bench = forward_return(spy, tx["date"], horizon)
    if stock is None or bench is None:
        return None
    return stock, bench


def _sells(history, closes_for, spy, horizon):
    rows = defaultdict(lambda: {"n": 0, "pnl": 0.0, "fwd": [], "excess": []})
    skipped = 0
    for tx in history:
        if tx.get("type") != "SELL":
            continue
        r = rows[(tx["date"][:7], exit_type(tx.get("details")))]
        r["n"] += 1
        r["pnl"] += float(tx.get("pnl") or 0.0)
        fv = _vs_spy(tx, closes_for, spy, horizon)
        if fv is None:
            skipped += 1
            continue
        r["fwd"].append(fv[0])
        r["excess"].append(fv[0] - fv[1])
    out = []
    for (month, kind), r in sorted(rows.items(), key=lambda kv: (kv[0][0], EXIT_TYPES.index(kv[0][1]))):
        out.append({"month": month, "type": kind, "n": r["n"], "pnl": round(r["pnl"], 2), "measured": len(r["fwd"]),
                    "median_after": _median(r["fwd"]), "median_vs_spy": _median(r["excess"]),
                    "rose": sum(1 for x in r["fwd"] if x > 0)})
    return out, skipped


def _entries(history, closes_for, spy, horizon):
    by_month = defaultdict(list)
    skipped = 0
    for tx in history:
        if tx.get("type") != "BUY":
            continue
        fv = _vs_spy(tx, closes_for, spy, horizon)
        if fv is None:
            skipped += 1
            continue
        by_month[tx["date"][:7]].append(fv)
    out = []
    for month in sorted(by_month):
        res = by_month[month]
        excess = [s - b for s, b in res]
        out.append({"month": month, "measured": len(res), "median": _median([s for s, _ in res]),
                    "median_vs_spy": _median(excess), "mean_vs_spy": statistics.fmean(excess),
                    "beat_spy": sum(1 for x in excess if x > 0)})
    return out, skipped


def _whipsaw(history, decisions):
    by_sym = defaultdict(dict)                         # symbol -> {date: s10+l60} (last entry of a day wins)
    for e in decisions or []:
        try:
            by_sym[e["symbol"]][e["date"]] = float(e.get("s10") or 0) + float(e.get("l60") or 0)
        except (KeyError, TypeError, ValueError):
            continue
    checked = flipped = no_log = 0
    for tx in history:
        if tx.get("type") != "BUY":
            continue
        logged = sorted(d for d in by_sym.get(tx["symbol"], {}) if d > tx["date"])[:WHIPSAW_DAYS]
        if not logged:
            no_log += 1
            continue
        checked += 1
        if any(by_sym[tx["symbol"]][d] < 0 for d in logged):
            flipped += 1
    return {"checked": checked, "flipped": flipped, "no_log": no_log}


def _turnover(history):
    months = defaultdict(lambda: {"buys": 0, "scale_ins": 0, "sells": 0})
    open_qty, opened, held = {}, {}, []
    for tx in history:
        t, sym = str(tx.get("type", "")), tx.get("symbol")
        if not sym or not (t.startswith("BUY") or t == "SELL"):
            continue
        m = months[tx["date"][:7]]
        qty = float(tx.get("qty") or 0)
        if t.startswith("BUY"):
            m["scale_ins" if _is_scale_in(tx) else "buys"] += 1
            if open_qty.get(sym, 0) <= 0:
                opened[sym] = tx["date"]
            open_qty[sym] = open_qty.get(sym, 0) + qty
        else:
            m["sells"] += 1
            open_qty[sym] = open_qty.get(sym, 0) - qty
            if open_qty[sym] <= 1e-9 and sym in opened:
                held.append(_days(opened.pop(sym), tx["date"]))
                open_qty[sym] = 0
    return {"months": [dict(month=k, **v) for k, v in sorted(months.items())], "closed": len(held),
            "median_days_held": _median(held)}


def _days(d1, d2):
    return (datetime.date.fromisoformat(d2) - datetime.date.fromisoformat(d1)).days


def _pyramid_after_bank(history, spy_days):
    """One case per scale-in that follows a Rule B scale-out of the same open position within
    PYRAMID_WINDOW sessions (paired with the latest such scale-out), with the realized P&L of
    the sells after the scale-in up to the full exit."""
    cases = []
    for j, buy in enumerate(history):
        if not str(buy.get("type", "")).startswith("BUY") or not _is_scale_in(buy):
            continue
        sym = buy["symbol"]
        bank = None
        for i in range(j - 1, -1, -1):
            tx = history[i]
            if tx.get("symbol") != sym:
                continue
            if tx.get("type") == "SELL":
                if exit_type(tx.get("details")) == "rule_b":
                    bank = tx
                break                                   # a full exit before it: a new position
            if str(tx.get("type", "")).startswith("BUY"):
                break                                   # the opening buy or an earlier scale-in
        if bank is None:
            continue
        gap = _sessions_between(spy_days, bank["date"], buy["date"])
        if gap is None or gap > PYRAMID_WINDOW:
            continue
        pnl, closed = 0.0, False
        for s in history[j + 1:]:
            if s.get("symbol") != sym or s.get("type") != "SELL":
                continue
            pnl += float(s.get("pnl") or 0.0)
            if exit_type(s.get("details")) != "rule_b":
                closed = True
                break
        cases.append({"symbol": sym, "bank_date": bank["date"], "bank_price": bank.get("price"),
                      "scale_in_date": buy["date"], "scale_in_price": buy.get("price"), "sessions": gap,
                      "pnl_after": round(pnl, 2), "closed": closed})
    return cases


def reflect(history, closes_for, spy, decisions=None, horizon=HORIZON) -> dict:
    """Behaviour summary of the ledger ``history``.

    ``closes_for(symbol) -> (days, closes)``; ``spy`` is SPY's ``(days, closes)``; ``decisions``
    are decision-log entries (date, symbol, s10, l60). Pure: reads no files.
    """
    hist = sorted((t for t in history or [] if isinstance(t, dict) and t.get("date") and t.get("symbol")),
                  key=lambda t: t["date"])         # stable: same-day order stays as logged
    sells, sells_skipped = _sells(hist, closes_for, spy, horizon)
    entries, entries_skipped = _entries(hist, closes_for, spy, horizon)
    return {"horizon": horizon, "sells": sells, "sells_skipped": sells_skipped,
            "entries": entries, "entries_skipped": entries_skipped,
            "whipsaw": _whipsaw(hist, decisions), "turnover": _turnover(hist),
            "pyramid_after_bank": _pyramid_after_bank(hist, spy[0])}


def load_closes(symbol, cache=None):
    """Split-adjusted real closes ``(days, closes)`` from the OHLCV cache (aether.pick_eval)."""
    return pick_eval._closes(symbol, {} if cache is None else cache)


def _pct(x):
    return "n/a" if x is None else f"{x:+.1f}%"


def render(r) -> list:
    """Plain-text lines for the weekly report."""
    h = r["horizon"]
    out = ["BEHAVIOUR REFLECTION (the game's own buys and sells; read-only)",
           f"  Forward results: close on the trade date to the close {h} sessions later, vs SPY.",
           "", f"  a) Sells by exit type per month ({h} sessions after the sale):"]
    for s in r["sells"]:
        line = f"    {s['month']} {_EXIT_LABEL[s['type']]:<11} n={s['n']:<3} P&L {s['pnl']:+,.0f}"
        if s["measured"]:
            line += (f" | after: med {_pct(s['median_after'])}, vs SPY {_pct(s['median_vs_spy'])},"
                     f" rose {s['rose']}/{s['measured']}")
        out.append(line)
    if not r["sells"]:
        out.append("    no sells")
    out.append(f"    skipped (under {h} sessions of bars): {r['sells_skipped']}")
    out += ["", f"  b) Entry quality: new BUYs, {h} sessions later vs SPY:"]
    for e in r["entries"]:
        out.append(f"    {e['month']} n={e['measured']:<3} med {_pct(e['median'])} | vs SPY med {_pct(e['median_vs_spy'])},"
                   f" mean {_pct(e['mean_vs_spy'])}, beat SPY {e['beat_spy']}/{e['measured']}")
    if not r["entries"]:
        out.append("    no measured buys")
    out.append(f"    skipped (under {h} sessions of bars): {r['entries_skipped']}")
    w = r["whipsaw"]
    share = f"{w['flipped'] / w['checked'] * 100:.0f}%" if w["checked"] else "n/a"
    out += ["", f"  c) Score whipsaw: S10+L60 below 0 within {WHIPSAW_DAYS} logged days after a buy:",
            f"    {w['flipped']}/{w['checked']} buys ({share}); no decision-log days after the buy: {w['no_log']}"]
    t = r["turnover"]
    out += ["", "  d) Turnover per month (buys / scale-ins / sells):"]
    out += [f"    {m['month']} {m['buys']} / {m['scale_ins']} / {m['sells']}" for m in t["months"]]
    med = "n/a" if t["median_days_held"] is None else f"{t['median_days_held']:g}"
    out.append(f"    closed positions: {t['closed']}, median days held: {med}")
    cases = r["pyramid_after_bank"]
    out += ["", f"  e) Pyramid after bank (Rule B scale-out, then a scale-in within {PYRAMID_WINDOW} sessions): {len(cases)}"]
    for c in cases:
        state = "closed" if c["closed"] else "still open"
        when = "same day" if c["sessions"] == 0 else f"{c['sessions']} session{'' if c['sessions'] == 1 else 's'} later"
        out.append(f"    {c['symbol']}: banked {c['bank_date']} @ {c['bank_price']}, scaled in @ {c['scale_in_price']}"
                   f" {when}; P&L after {c['pnl_after']:+,.0f} ({state})")
    if cases:
        out.append(f"    total P&L after the scale-in: {sum(c['pnl_after'] for c in cases):+,.0f}")
    return out
