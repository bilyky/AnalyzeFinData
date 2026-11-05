"""
invest_plan.py — the daily check of a short, dated investment plan (recommend-only).

A plan is a private JSON file in Data/position_watch/ (git-ignored), e.g.
invest_plan.json:

  {"name": "year-end plan", "total": 30000, "return_by": "2026-12-28",
   "safe": {"amount": 15000, "where": "money market / T-bills maturing before 2026-12-31"},
   "steps": [{"date": "2026-11-06", "amount": 5000, "buy": "SPY"}, ...],
   "rules": {"wait_if_market_day": -0.02, "index_stop": -0.08,
             "sell_from": "2026-12-15", "sell_to": "2026-12-22", "latest_sell": "2026-12-28"},
   "fills": [{"date": "2026-11-06", "symbol": "SPY", "shares": 6.5, "price": 766.0, "stop": null}]}

Each day it says, from the cached daily bars:
  - which step is due (or overdue), and "wait a day" when the market (SPY) fell by
    wait_if_market_day or more on the last session (the owner's rule after a big drop);
  - whether RSP is stretched (position-watch's replacement_timing: more than 5% above its
    50-day average, or RSI >= 70), so an "RSP" step buys SPY or waits instead;
  - each fill's gain/loss and its stop: index funds at index_stop from cost, other names at
    their own "stop" price (an ATR stop the owner records);
  - the days left to the sell window and to the latest sell date.

It never places an order and writes nothing. Fills are recorded by hand in the plan file.

Usage:
    python scripts/monitoring/invest_plan.py                 # Data/position_watch/invest_plan.json
    python scripts/monitoring/invest_plan.py --plan other.json --json
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import position_watch as pw  # noqa: E402

INDEX_FUNDS = {"SPY", "RSP"}


def _out(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


def load_plan(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def market_day(spy: dict) -> tuple:
    """(last bar date, SPY's return on the last session)."""
    c = spy["c"]
    return spy["days"][-1], (c[-1] / c[-2] - 1 if len(c) >= 2 and c[-2] else None)


def step_status(plan: dict, today: datetime.date, fills: list) -> list:
    """Each step: done (a fill on or after its date covers it), due today, overdue, or later."""
    out, spent = [], sum(f["shares"] * f["price"] for f in fills)
    planned = 0.0
    for s in sorted(plan.get("steps", []), key=lambda x: x["date"]):
        planned += s["amount"]
        d = datetime.date.fromisoformat(s["date"])
        if spent >= planned - 1:                     # $1 slack for rounding
            state = "done"
        elif d == today:
            state = "due today"
        elif d < today:
            state = "overdue"
        else:
            state = "later"
        out.append({**s, "state": state})
    return out


def fill_status(fills: list, prices: dict, index_stop: float) -> list:
    out = []
    for f in fills:
        px = prices.get(f["symbol"])
        cost = f["shares"] * f["price"]
        if f["symbol"] in INDEX_FUNDS:
            stop = f["price"] * (1 + index_stop)
        else:
            stop = f.get("stop")
        out.append({**f, "last": px, "pnl": None if px is None else f["shares"] * px - cost,
                    "pnl_pct": None if px is None else px / f["price"] - 1, "stop": stop,
                    "stopped": bool(px is not None and stop and px <= stop)})
    return out


def check(plan: dict, today: datetime.date, bars: dict) -> dict:
    """bars: {symbol: position-watch bars} for SPY, RSP and every filled symbol."""
    rules = plan.get("rules", {})
    last_day, spy_ret = market_day(bars["SPY"])
    rsp = bars.get("RSP")
    rsp_timing = pw.replacement_timing(rsp, len(rsp["days"]) - 1) if rsp else None
    fills = plan.get("fills", [])
    prices = {s: b["c"][-1] for s, b in bars.items() if b}
    steps = step_status(plan, today, fills)
    actions, notes = [], []
    wait = spy_ret is not None and spy_ret <= rules.get("wait_if_market_day", -0.02)
    for s in steps:
        if s["state"] in ("due today", "overdue"):
            if wait:
                actions.append(f"WAIT a day: SPY fell {spy_ret:+.1%} on {last_day}; step {s['date']} "
                               f"(${s['amount']:,.0f} {s['buy']}) moves to the next session")
            elif "RSP" in s["buy"] and rsp_timing and not rsp_timing["entry_ok"]:
                actions.append(f"BUY step {s['date']}: ${s['amount']:,.0f}; RSP is stretched "
                               f"(RSI {rsp_timing['rsi14']:.0f}, {rsp_timing['stretch_50']:+.1%} vs 50-day): "
                               f"use SPY now or hold the RSP part for a later step")
            else:
                actions.append(f"BUY step {s['date']}: ${s['amount']:,.0f} {s['buy']} (limit order)")
    fstat = fill_status(fills, prices, rules.get("index_stop", -0.08))
    for f in fstat:
        if f["stopped"]:
            actions.append(f"SELL {f['symbol']}: {f['last']:.2f} is at/below its stop {f['stop']:.2f}")
    sell_from = rules.get("sell_from")
    latest = rules.get("latest_sell") or plan.get("return_by")
    if sell_from and today >= datetime.date.fromisoformat(sell_from) and fills:
        actions.append(f"SELL WINDOW: sell everything with limit orders by {rules.get('sell_to')} "
                       f"(latest {latest})")
    elif sell_from:
        notes.append(f"{(datetime.date.fromisoformat(sell_from) - today).days} days to the sell window "
                     f"({sell_from}); latest sell {latest}")
    total_cost = sum(f["shares"] * f["price"] for f in fstat)
    total_pnl = sum(f["pnl"] for f in fstat if f["pnl"] is not None)
    return {"date": today.isoformat(), "last_bar": last_day, "spy_day": spy_ret, "rsp_timing": rsp_timing,
            "steps": steps, "fills": fstat, "invested": total_cost, "pnl": total_pnl,
            "actions": actions or ["Nothing to do today."], "notes": notes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--plan", default=os.path.join(pw.watch_dir(), "invest_plan.json"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    plan = load_plan(a.plan)
    syms = {"SPY", "RSP"} | {f["symbol"] for f in plan.get("fills", [])}
    bars = {s: pw.load_bars(s) for s in syms}
    if not bars.get("SPY"):
        _out("no SPY bars: refresh the price cache first")
        return 1
    r = check(plan, datetime.date.today(), bars)
    if a.json:
        _out(json.dumps(r, indent=1, default=str))
        return 0
    _out(f"{plan.get('name', 'plan')}: {r['date']} (bars to {r['last_bar']}, SPY last session "
         f"{'-' if r['spy_day'] is None else f'{r['spy_day']:+.1%}'}) · invested ${r['invested']:,.0f} · "
         f"P/L ${r['pnl']:+,.0f}")
    for s in r["steps"]:
        _out(f"  step {s['date']}  ${s['amount']:>7,.0f}  {s['buy']:<40s} {s['state']}")
    for f in r["fills"]:
        last = "-" if f["last"] is None else f"{f['last']:.2f}"
        pct = "" if f["pnl_pct"] is None else f" ({f['pnl_pct']:+.1%})"
        _out(f"  holding {f['symbol']:<5s} {f['shares']} sh @ {f['price']:.2f} -> {last}{pct}"
             f"  stop {'-' if not f['stop'] else f'{f['stop']:.2f}'}{'  STOPPED' if f['stopped'] else ''}")
    _out("  TODAY:")
    for x in r["actions"]:
        _out(f"   - {x}")
    for x in r["notes"]:
        _out(f"   · {x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
