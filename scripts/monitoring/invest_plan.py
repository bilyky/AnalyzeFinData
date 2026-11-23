"""
invest_plan.py — the daily check of a short, dated investment plan (recommend-only).

A plan is a private JSON file in Data/position_watch/ (git-ignored). Shape (placeholder
values, not a real plan):

  {"name": "example plan", "total": 10000, "return_by": "2030-04-30",
   "safe": {"amount": 4000, "where": "money market"},
   "steps": [{"date": "2030-03-04", "amount": 2500, "buy": "SPY"}, ...],
   "rules": {"wait_if_market_day": -0.02, "index_stop": -0.08, "index_stop_from": "average",
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
  - each fill's gain/loss and its stop. Index funds (owner 2026-11-20): index_stop (8%) under the average
    cost over all its fills, or 1% under the confirmed support below the average cost (taken from bars up to
    its last buy) when that is lower: support, but never closer than 8% and never further than
    "index_stop_max" (default 30%). The average uses all fills (rules "index_stop_from": "average", the default; "lot" uses each fill's
    own price), so a new buy moves the stop. Other names at
    their own "stop" price (an ATR stop the owner records);
  - the days left to the sell window and to the latest sell date. A fill marked "hold": "long" (owner
    2026-11-21: money held into the next year) counts in its symbol's average stop but never covers a dated
    step and is left out of the sell window;
  - per index fund held, the win/loss odds of today's setup from the fund's own history (descriptive): days in
    the same band of distance from the 50-day average, 20+ sessions apart, with the share up after 60
    sessions, the median return, and how often -8% came before +8% within 60.

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
from aether import risk_utils  # noqa: E402

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


def average_costs(fills: list) -> dict:
    """{symbol: share-weighted average buy price} over all of a symbol's fills."""
    tot, sh = {}, {}
    for f in fills:
        tot[f["symbol"]] = tot.get(f["symbol"], 0.0) + f["shares"] * f["price"]
        sh[f["symbol"]] = sh.get(f["symbol"], 0.0) + f["shares"]
    return {s: tot[s] / sh[s] for s in tot if sh[s] > 0}


ODDS_BUCKETS = (-0.08, -0.04, 0.0, 0.04)      # distance from the 50-day average: <=-8%, -8..-4, -4..0, 0..+4, >+4
ODDS_HORIZON = 60
ODDS_MOVE = 0.08
ODDS_GAP = 20


def _bucket(x: float) -> int:
    return sum(x > e for e in ODDS_BUCKETS)


def _bucket_label(k: int) -> str:
    edges = [None, *ODDS_BUCKETS, None]
    lo, hi = edges[k], edges[k + 1]
    if lo is None:
        return f"{-hi:.0%}+ under the 50-day average"
    if hi is None:
        return f"{lo:.0%}+ over the 50-day average"
    return f"{lo:+.0%} to {hi:+.0%} vs the 50-day average"


def odds(closes: list, i: int | None = None) -> dict | None:
    """Win/loss base rate for today's setup (owner 2026-11-21), descriptive, from the symbol's own history:
    past days in the same bucket of distance from the 50-day average, at least ODDS_GAP sessions apart, whose
    ODDS_HORIZON-session outcome was complete by day i (no look-ahead). Returns the share up after the horizon,
    the median return, and which came first within it: -8% or +8%."""
    i = len(closes) - 1 if i is None else i
    if i < 50:
        return None
    sma = lambda j: sum(closes[j - 49:j + 1]) / 50
    k = _bucket(closes[i] / sma(i) - 1)
    ev, last = [], -10 ** 9
    for j in range(49, i - ODDS_HORIZON + 1):
        if _bucket(closes[j] / sma(j) - 1) == k and j - last >= ODDS_GAP:
            ev.append(j)
            last = j
    if not ev:
        return {"bucket": _bucket_label(k), "n": 0}
    r = sorted(closes[j + ODDS_HORIZON] / closes[j] - 1 for j in ev)
    down = up = 0
    for j in ev:
        for m in range(j + 1, j + ODDS_HORIZON + 1):
            if closes[m] <= closes[j] * (1 - ODDS_MOVE):
                down += 1
                break
            if closes[m] >= closes[j] * (1 + ODDS_MOVE):
                up += 1
                break
    n = len(ev)
    return {"bucket": _bucket_label(k), "n": n, "p_up": sum(x > 0 for x in r) / n, "median": r[n // 2],
            "p_down_first": down / n, "p_up_first": up / n, "since": None}


def entry_support(fills: list, symbol: str, bars) -> float | None:
    """The confirmed swing-low support under the symbol's average cost, from bars up to its LAST buy, so the
    level stays put as new bars arrive (a support taken from today's bars would keep sliding under a falling
    price and never trigger). Same detector as the game's stops (risk_utils.detect_support)."""
    if not bars:
        return None
    last_buy = max(f["date"] for f in fills if f["symbol"] == symbol)
    upto = [i for i, d in enumerate(bars["days"]) if d <= last_buy]
    if not upto:
        return None
    return risk_utils.detect_support(average_costs(fills)[symbol], bars["l"][:upto[-1] + 1])


INDEX_STOP_MAX = -0.30   # owner 2026-11-20: a support stop goes no further than 30% under the average (Chaikin's 30%)


def fill_status(fills: list, prices: dict, index_stop: float, basis: str = "average",
                supports: dict | None = None, index_stop_max: float = INDEX_STOP_MAX) -> list:
    """Each fill's gain/loss and stop. Index funds (owner 2026-11-20): index_stop (8%) under the AVERAGE cost over
    all fills, so a new buy moves it (basis="lot" uses each fill's own price), or 1% under the confirmed support
    below the average cost (the game's support stop, support x 0.99) when that is lower: support, but never
    closer than 8% and never further than index_stop_max (30%). Other names use their own recorded "stop"."""
    avg = average_costs(fills)
    supports = supports or {}
    out = []
    for f in fills:
        px = prices.get(f["symbol"])
        cost = f["shares"] * f["price"]
        src = None
        if f["symbol"] in INDEX_FUNDS:
            ref = avg[f["symbol"]] if basis == "average" else f["price"]
            pct_stop = ref * (1 + index_stop)
            sup = supports.get(f["symbol"])
            floor = ref * (1 + index_stop_max)
            if sup and round(sup * 0.99, 2) < pct_stop:      # support, but never closer than index_stop
                stop, src = (round(sup * 0.99, 2), "support") if round(sup * 0.99, 2) >= floor else (floor, "cap")
            else:
                stop, src = pct_stop, "pct"
        else:
            stop = f.get("stop")
        out.append({**f, "last": px, "pnl": None if px is None else f["shares"] * px - cost,
                    "pnl_pct": None if px is None else px / f["price"] - 1, "stop": stop, "stop_source": src,
                    "avg_cost": avg.get(f["symbol"]),
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
    behind_spy = sorted(s for s in {f["symbol"] for f in fills}
                        if bars.get(s) and last_day and bars[s]["days"][-1] < last_day)
    if behind_spy:                   # 11-20: RSP's cache ended 11-17 while SPY's ended 11-19, and nothing said so
        doubts.append(f"price bars for {', '.join(behind_spy)} end before SPY's ({last_day}): refresh first")
    plan_fills = [f for f in fills if f.get("hold") != "long"]      # long-term lots never cover a dated step
    steps = step_status(plan, today, plan_fills)
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
    basis = rules.get("index_stop_from", "average")
    supports = {sym: entry_support(fills, sym, bars.get(sym))
                for sym in {f["symbol"] for f in fills} if sym in INDEX_FUNDS}
    fstat = fill_status(fills, prices, rules.get("index_stop", -0.08), basis, supports,
                        rules.get("index_stop_max", INDEX_STOP_MAX))
    sold = set()
    for f in fstat:
        if not f["stopped"]:
            continue
        if f["symbol"] in INDEX_FUNDS and basis == "average":
            if f["symbol"] in sold:
                continue                     # one stop for the whole symbol: sell every lot once
            sold.add(f["symbol"])
            n = sum(x["shares"] for x in fstat if x["symbol"] == f["symbol"])
            why = (f"1% under support {supports[f['symbol']]:.2f}" if f["stop_source"] == "support" else
                   f"{-rules.get('index_stop_max', INDEX_STOP_MAX):.0%} cap under the average cost {f['avg_cost']:.2f}"
                   if f["stop_source"] == "cap" else
                   f"{-rules.get('index_stop', -0.08):.0%} under the average cost {f['avg_cost']:.2f}")
            actions.append(f"SELL {f['symbol']} (all {n:g} sh): {f['last']:.2f} is at/below its stop {f['stop']:.2f} ({why})")
        else:
            actions.append(f"SELL {f['symbol']}: {f['last']:.2f} is at/below its stop {f['stop']:.2f}")
    sell_from = rules.get("sell_from")
    latest = rules.get("latest_sell") or plan.get("return_by")
    long_lots = [f for f in fills if f.get("hold") == "long"]
    if sell_from and today >= datetime.date.fromisoformat(sell_from) and plan_fills:
        keep = (f"; keep the {len(long_lots)} long-term lot(s) ({', '.join(sorted({f['symbol'] for f in long_lots}))})"
                if long_lots else "")
        actions.append(f"SELL WINDOW: sell the plan's lots with limit orders by {rules.get('sell_to')} "
                       f"(latest {latest}){keep}")
    elif sell_from:
        notes.append(f"{(datetime.date.fromisoformat(sell_from) - today).days} days to the sell window "
                     f"({sell_from}); latest sell {latest}")
    odds_by = {}
    for sym in sorted({f["symbol"] for f in fills if f["symbol"] in INDEX_FUNDS}):
        o = odds(bars[sym]["c"]) if bars.get(sym) else None
        if o:
            o["since"] = bars[sym]["days"][0]
            odds_by[sym] = o
            if o["n"]:
                notes.append(f"{sym} odds (history since {o['since']}, {o['bucket']}, n={o['n']}, descriptive): "
                             f"up after {ODDS_HORIZON} sessions {o['p_up']:.0%}, median {o['median']:+.1%}; "
                             f"within {ODDS_HORIZON}: -{ODDS_MOVE:.0%} first {o['p_down_first']:.0%}, "
                             f"+{ODDS_MOVE:.0%} first {o['p_up_first']:.0%}")
    if doubts:                       # never act when not sure: every BUY/SELL waits for fresh data
        actions = [f"WAIT (not sure): {a}" for a in actions if not a.startswith("WAIT")] or actions
    total_cost = sum(f["shares"] * f["price"] for f in fstat)
    total_pnl = sum(f["pnl"] for f in fstat if f["pnl"] is not None)
    return {"date": today.isoformat(), "last_bar": last_day, "sessions_behind": behind, "spy_day": spy_ret,
            "rsp_timing": rsp_timing, "steps": steps, "fills": fstat, "invested": total_cost, "pnl": total_pnl,
            "doubts": doubts, "actions": actions or ["Nothing to do today."], "notes": notes, "odds": odds_by}


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
