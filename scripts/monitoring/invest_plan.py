"""
invest_plan.py — the daily check of a short, dated investment plan (recommend-only).

A plan is a private JSON file in Data/position_watch/ (git-ignored). Shape (placeholder
values, not a real plan):

  {"name": "example plan", "total": 10000, "return_by": "2030-04-30",
   "safe": {"amount": 4000, "where": "money market"},
   "steps": [{"date": "2030-03-04", "amount": 2500, "buy": "SPY"}, ...],
   "rules": {"wait_if_market_day": -0.02, "index_stop": -0.08,
             "sell_from": "2030-04-15", "sell_to": "2030-04-22", "latest_sell": "2030-04-29"},
   "fills": [{"date": "2030-03-04", "symbol": "SPY", "shares": 5, "price": 500.0, "stop": null}]}

Each day it says, from the cached daily bars:
  - which step is due (or overdue). A step is covered only by fills dated on or after it,
    taken in order, to within $1 or 1% of the step (fractional shares leave a few dollars);
    a partly covered step shows the amount left; a step dated on a market holiday is "due
    next session";
  - "wait a day" when the market (SPY) fell by wait_if_market_day or more on the last
    session (the owner's rule after a big drop);
  - whether RSP is stretched (position-watch's replacement_timing: more than 5% above its
    50-day average, or RSI >= 70), so an "RSP" step buys SPY or waits instead;
  - each fill's gain/loss and its stop: index funds at index_stop from cost, other names at
    their own "stop" price (an ATR stop the owner records);
  - the days left to the sell window and to the latest sell date.

Never act when not sure (position-watch rules v2): if SPY's last bar is older than the last
completed session, or a holding has no bars, it says so and turns every BUY/SELL into WAIT
until the cache is refreshed.

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

import console_safe  # noqa: E402
import position_watch as pw  # noqa: E402
from aether import market_calendar  # noqa: E402

INDEX_FUNDS = {"SPY", "RSP"}
SLACK = 1.0          # dollars of rounding slack when fills cover a step
SLACK_PCT = 0.01     # ...or 1% of the step, whichever is larger: fractional shares leave a few dollars


def _out(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


def load_plan(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def market_day(spy: dict) -> tuple:
    """(last bar date, SPY's return on the last session)."""
    c = spy["c"]
    return spy["days"][-1], (c[-1] / c[-2] - 1 if len(c) >= 2 and c[-2] else None)


def sessions_behind(last_bar: str, today: datetime.date) -> int:
    """How many completed sessions are missing after last_bar (0 = up to date)."""
    last_done = market_calendar.last_session_on_or_before(today - datetime.timedelta(days=1))
    d, n = datetime.date.fromisoformat(last_bar), 0
    while d < last_done:
        d += datetime.timedelta(days=1)
        n += market_calendar.is_session(d)
    return n


def step_status(plan: dict, today: datetime.date, fills: list) -> list:
    """Each step: done, due today, due next session (dated on a holiday), overdue, or later.
    Fills cover steps in date order, and only steps dated on or before the fill."""
    pool = sorted(([f["date"], f["shares"] * f["price"]] for f in fills), key=lambda x: x[0])
    out = []
    for s in sorted(plan.get("steps", []), key=lambda x: x["date"]):
        need = s["amount"]
        slack = max(SLACK, SLACK_PCT * s["amount"])
        for p in pool:
            if p[0] >= s["date"] and p[1] > 0 and need > slack:
                take = min(p[1], need)
                p[1] -= take
                need -= take
        d = datetime.date.fromisoformat(s["date"])
        if need <= slack:
            state = "done"
        elif d == today:
            state = "due today" if market_calendar.is_session(d) else "due next session"
        elif d < today:
            state = "overdue"
        else:
            state = "later"
        out.append({**s, "state": state, "left": 0.0 if state == "done" else need})
    return out


def fill_status(fills: list, prices: dict, index_stop: float) -> list:
    out = []
    for f in fills:
        px = prices.get(f["symbol"])
        cost = f["shares"] * f["price"]
        stop = f["price"] * (1 + index_stop) if f["symbol"] in INDEX_FUNDS else f.get("stop")
        out.append({**f, "last": px, "pnl": None if px is None else f["shares"] * px - cost,
                    "pnl_pct": None if px is None else px / f["price"] - 1, "stop": stop,
                    "stopped": bool(px is not None and stop and px <= stop)})
    return out


def check(plan: dict, today: datetime.date, bars: dict) -> dict:
    """bars: {symbol: position-watch bars or None} for SPY, RSP and every filled symbol."""
    rules = plan.get("rules", {})
    last_day, spy_ret = market_day(bars["SPY"])
    rsp = bars.get("RSP")
    rsp_timing = pw.replacement_timing(rsp, len(rsp["days"]) - 1) if rsp else None
    fills = plan.get("fills", [])
    prices = {s: b["c"][-1] for s, b in bars.items() if b}
    doubts = []
    behind = sessions_behind(last_day, today)
    if behind:
        doubts.append(f"price cache is {behind} session(s) old (last bar {last_day}): refresh first")
    missing = sorted({f["symbol"] for f in fills if not bars.get(f["symbol"])})
    if missing:
        doubts.append(f"no price bars for {', '.join(missing)}: its stop can't be checked")
    steps = step_status(plan, today, fills)
    actions, notes = [], []
    wait = spy_ret is not None and spy_ret <= rules.get("wait_if_market_day", -0.02)
    for s in steps:
        if s["state"] == "due next session":
            notes.append(f"step {s['date']} falls on a market holiday: buy at the next session")
        elif s["state"] in ("due today", "overdue"):
            left = s["left"]
            amt = f"${left:,.0f}" if left >= s["amount"] else f"${left:,.0f} left of ${s['amount']:,.0f}"
            if wait:
                actions.append(f"WAIT a day: SPY fell {spy_ret:+.1%} on {last_day}; step {s['date']} "
                               f"({amt} {s['buy']}) moves to the next session")
            elif "RSP" in s["buy"] and rsp_timing and not rsp_timing["entry_ok"]:
                actions.append(f"BUY step {s['date']}: {amt}; RSP is stretched "
                               f"(RSI {rsp_timing['rsi14']:.0f}, {rsp_timing['stretch_50']:+.1%} vs 50-day): "
                               f"use SPY now or hold the RSP part for a later step")
            else:
                actions.append(f"BUY step {s['date']}: {amt} {s['buy']} (limit order)")
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
    if doubts:                       # never act when not sure: every BUY/SELL waits for fresh data
        actions = [f"WAIT (not sure): {a}" for a in actions if not a.startswith("WAIT")] or actions
    total_cost = sum(f["shares"] * f["price"] for f in fstat)
    total_pnl = sum(f["pnl"] for f in fstat if f["pnl"] is not None)
    return {"date": today.isoformat(), "last_bar": last_day, "sessions_behind": behind, "spy_day": spy_ret,
            "rsp_timing": rsp_timing, "steps": steps, "fills": fstat, "invested": total_cost, "pnl": total_pnl,
            "doubts": doubts, "actions": actions or ["Nothing to do today."], "notes": notes}


def main(argv=None) -> int:
    console_safe.install()          # step labels are free text: a non-cp1252 character must not crash
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
    spy = "-" if r["spy_day"] is None else f"{r['spy_day']:+.1%}"
    _out(f"{plan.get('name', 'plan')}: {r['date']} (bars to {r['last_bar']}, SPY last session {spy}) - "
         f"invested ${r['invested']:,.0f}, P/L ${r['pnl']:+,.0f}")
    for d in r["doubts"]:
        _out(f"  DOUBT: {d}")
    for s in r["steps"]:
        _out(f"  step {s['date']}  ${s['amount']:>7,.0f}  {s['buy']:<40s} {s['state']}")
    for f in r["fills"]:
        last = "-" if f["last"] is None else f"{f['last']:.2f}"
        pct = "" if f["pnl_pct"] is None else f" ({f['pnl_pct']:+.1%})"
        stop = "-" if not f["stop"] else f"{f['stop']:.2f}"
        _out(f"  holding {f['symbol']:<5s} {f['shares']} sh @ {f['price']:.2f} -> {last}{pct}  stop {stop}"
             f"{'  STOPPED' if f['stopped'] else ''}")
    _out("  TODAY:")
    for x in r["actions"]:
        _out(f"   - {x}")
    for x in r["notes"]:
        _out(f"   . {x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
