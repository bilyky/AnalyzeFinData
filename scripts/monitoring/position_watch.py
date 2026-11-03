"""
Position watch: a daily, recommend-only monitor for one concentrated holding (default INTC).

Built for a slow, evidence-led trim: it never places an order, never opens a browser, and
never goes below a concentration floor. Each run:

  1. Measures the symbol: trend (SMA 20/50/200), drawdown from its 52-week high, 1/5/20/60-day
     returns, volume vs its 20-day average, today's gap, and the rubber-band detector.
  2. Separates "the sector moved" from "the symbol moved on its own": a beta to an equal-weight
     peer basket over the last BETA_WINDOW days, and the residual (idiosyncratic) return over
     1/5/20 days with a z-score.
  3. Scores each replacement's entry timing (stretch vs its 50-day average, RSI-14).
  4. Computes concentration against the user's total (E*TRADE accounts from the OceanView
     context pack, plus stock-plan shares and outside assets from a private config) and the
     dollars that can be sold before the floor.
  5. Gives a heuristic call (RULES_VERSION): TRIM a small tranche, HOLD, or WAIT, with reasons.
     A TRIM also suggests tax lots (losses outside the wash-sale windows first, then long-term
     lots with the smallest gain per share), if a private lots file is present.
  6. RETRO: scores earlier calls against what happened since (symbol vs replacement return)
     and appends today's call to a private journal.

The rules are heuristics, not a backtested strategy. The RETRO exists to test them on real
days and adjust them. All private inputs and outputs live under Data/position_watch/ (git-
ignored). Intel-policy note for INTC holders: no options, hedges or short sales; never trade
while aware of material non-public information.

Usage:
    python scripts/monitoring/position_watch.py [--symbol INTC] [--live] [--no-journal]
                                                [--lesson "text"]
"""
import argparse
import datetime
import json
import math
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

import numpy as np

from aether import patterns, paths
from aether.etrade.client import ETradeClient
from aether.logger import get_logger
from aether.oceanview_context import build_oceanview_context
from aether.risk_utils import split_adjust_ohlcv

_log = get_logger("position_watch")

RULES_VERSION = "v1"
BETA_WINDOW = 120
WEAK_Z = -1.0               # 20-day residual z below this = weak on its own
FLOOR_BUFFER = 0.02         # stop trimming 2 points above the floor
CRASH_DAY = -0.05           # no trim on a day the symbol fell more than 5%
STRETCH_MAX = 0.05          # replacement more than 5% above its 50-day average = stretched
RSI_MAX = 70.0
WASH_DAYS = 30
DEFAULTS = {
    "symbol": "INTC", "floor_pct": 0.30, "tranche_pct": 0.05,
    "peers": ["AMD", "NVDA", "QCOM", "MU", "AVGO", "TSM", "MRVL", "ON"],
    "replacements": ["SPY", "RSP"],
    "stock_plan_shares": 0, "outside_assets_usd": 0.0, "outside_symbol_shares": 0,
    "acquisition_dates": [], "lt_rate": 0.281, "st_rate": 0.371,
}


def _out(line: str = "") -> None:
    """Report line: intentional CLI output, unprefixed."""
    sys.stdout.write(line + "\n")


def watch_dir() -> str:
    return os.path.join(paths.data_dir(), "position_watch")


def load_config(path=None) -> dict:
    cfg = dict(DEFAULTS)
    path = path or os.path.join(watch_dir(), "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


# ── Prices ────────────────────────────────────────────────────────────────────

def bars_from_series(ts: dict) -> dict | None:
    """Real (non-placeholder) daily bars, split-adjusted, as parallel lists."""
    days = sorted(d for d in ts if not ts[d].get("provisional") and float(ts[d].get("5. volume") or 0) > 0)
    if len(days) < 30:
        return None
    raw = {k: [float(ts[d][k]) for d in days] for k in ("1. open", "2. high", "3. low", "4. close")}
    h, lo, c = split_adjust_ohlcv(raw["1. open"], raw["2. high"], raw["3. low"], raw["4. close"])
    o = [op * (cc / rc if rc else 1.0) for op, cc, rc in zip(raw["1. open"], c, raw["4. close"], strict=True)]
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "o": o, "h": list(h), "l": list(lo),
            "c": list(c), "v": [float(ts[d].get("5. volume") or 0) for d in days]}


def load_bars(symbol: str) -> dict | None:
    path = os.path.join(paths.ohlcv_dir(), f"{symbol}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return bars_from_series(json.load(f).get("Time Series (Daily)", {}))


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n if i + 1 >= n else None


def rsi(c, i, n=14):
    if i < n:
        return None
    d = np.diff(np.asarray(c[i - n:i + 1], dtype=float))
    up, dn = d[d > 0].sum() / n, -d[d < 0].sum() / n
    if up == 0 and dn == 0:
        return 50.0                                     # flat: neither overbought nor oversold
    return 100.0 if dn == 0 else float(100 - 100 / (1 + up / dn))


def symbol_metrics(b: dict, i: int) -> dict:
    c = b["c"]
    ret = lambda n: c[i] / c[i - n] - 1 if i >= n else None
    hi52 = max(c[max(0, i - 251):i + 1])
    m = {"date": b["days"][i], "close": c[i], "ret_1": ret(1), "ret_5": ret(5), "ret_20": ret(20),
         "ret_60": ret(60), "drawdown_52w": c[i] / hi52 - 1, "high_52w": hi52,
         "gap": b["o"][i] / c[i - 1] - 1 if i else None,
         "vol_ratio": b["v"][i] / (sum(b["v"][i - 20:i]) / 20) if i >= 20 and sum(b["v"][i - 20:i]) else None}
    for n in (20, 50, 200):
        s = _sma(c, i, n)
        m[f"sma{n}"] = s
        m[f"vs_sma{n}"] = c[i] / s - 1 if s else None
    return m


def basket_returns(peer_bars: list, days: list) -> list:
    """Equal-weight daily log returns of the peers over `days` (None where no peer traded)."""
    out = []
    for a, b in zip(days, days[1:]):
        rs = [math.log(p["c"][p["idx"][b]] / p["c"][p["idx"][a]]) for p in peer_bars
              if a in p["idx"] and b in p["idx"]]
        out.append(sum(rs) / len(rs) if rs else None)
    return out


def beta_residual(sym_rets: list, peer_rets: list, window: int = BETA_WINDOW) -> dict | None:
    """Beta of the symbol to the peer basket over the last `window` days, and the residual
    (idiosyncratic) return summed over the last 1/5/20 days, with a 20-day z-score."""
    pairs = [(s, p) for s, p in zip(sym_rets, peer_rets, strict=True) if s is not None and p is not None]
    if len(pairs) < 40:
        return None
    fit = pairs[-window:]
    s = np.array([x for x, _ in fit])
    p = np.array([y for _, y in fit])
    beta = float(np.cov(s, p, ddof=1)[0, 1] / np.var(p, ddof=1)) if np.var(p) > 0 else 0.0
    resid_all = np.array([x - beta * y for x, y in pairs])
    sd = float(np.std(resid_all[-window:], ddof=1))
    r = lambda n: float(resid_all[-n:].sum())
    return {"beta": beta, "resid_sd": sd, "resid_1": r(1), "resid_5": r(5), "resid_20": r(20),
            "z_20": r(20) / (sd * math.sqrt(20)) if sd > 0 else 0.0,
            "corr": float(np.corrcoef(s, p)[0, 1])}


def replacement_timing(b: dict, i: int) -> dict:
    s50 = _sma(b["c"], i, 50)
    stretch = b["c"][i] / s50 - 1 if s50 else None
    r = rsi(b["c"], i)
    ok = stretch is not None and stretch <= STRETCH_MAX and (r is None or r < RSI_MAX)
    return {"close": b["c"][i], "stretch_50": stretch, "rsi14": r,
            "ret_20": b["c"][i] / b["c"][i - 20] - 1 if i >= 20 else None, "entry_ok": ok}


# ── Concentration, decision, lots ─────────────────────────────────────────────

def concentration(symbol_value: float, total: float, floor_pct: float) -> dict:
    """Selling the symbol into a replacement leaves the total unchanged, so the floor in
    dollars is floor_pct * total, and headroom is what sits above it."""
    pct = symbol_value / total if total else None
    return {"value": symbol_value, "total": total, "pct": pct,
            "headroom_usd": max(0.0, symbol_value - floor_pct * total) if total else 0.0}


def decide(m: dict, rb: dict | None, rbr_tags: list, repl: dict, conc: dict, cfg: dict) -> dict:
    """Heuristic call (RULES_VERSION). TRIM only when the symbol is weak on its own, above
    the floor, not on a crash day, and a replacement's entry timing is OK."""
    reasons = []
    floor = cfg["floor_pct"]
    best = min((k for k in repl if repl[k]["entry_ok"]), key=lambda k: repl[k]["stretch_50"], default=None)
    if conc["pct"] is None:
        return {"action": "HOLD", "reasons": ["total unknown: no concentration check"], "tranche_usd": 0.0,
                "replacement": None}
    if conc["pct"] <= floor + FLOOR_BUFFER:
        return {"action": "HOLD", "reasons": [f"at the floor ({conc['pct']:.1%} vs {floor:.0%} + buffer)"],
                "tranche_usd": 0.0, "replacement": None}
    if any("RBR" in t for t in rbr_tags):
        return {"action": "HOLD", "reasons": [f"recovery pattern forming ({', '.join(rbr_tags)}): pause trims"],
                "tranche_usd": 0.0, "replacement": None}
    below50 = m["vs_sma50"] is not None and m["vs_sma50"] < 0
    weak_alone = bool(rb) and rb["z_20"] < WEAK_Z
    if not below50 or not weak_alone:
        if not below50:
            reasons.append("above its 50-day average")
        if rb and not weak_alone:
            reasons.append(f"not weak on its own (20-day residual z {rb['z_20']:+.2f})")
        return {"action": "HOLD", "reasons": reasons or ["no clear signal"], "tranche_usd": 0.0, "replacement": None}
    reasons.append(f"below its 50-day average ({m['vs_sma50']:+.1%}) and weak on its own "
                   f"(20-day residual {rb['resid_20']:+.1%}, z {rb['z_20']:+.2f})")
    if m["ret_1"] is not None and m["ret_1"] < CRASH_DAY:
        return {"action": "WAIT", "reasons": reasons + [f"crash day ({m['ret_1']:+.1%}): don't sell into it"],
                "tranche_usd": 0.0, "replacement": None}
    if best is None:
        return {"action": "WAIT", "reasons": reasons + ["every replacement is stretched: wait for a better entry"],
                "tranche_usd": 0.0, "replacement": None}
    tranche = min(cfg["tranche_pct"] * conc["value"], conc["headroom_usd"])
    return {"action": "TRIM", "reasons": reasons + [f"{best} entry OK (stretch {repl[best]['stretch_50']:+.1%})"],
            "tranche_usd": tranche, "replacement": best}


def in_wash_window(day: datetime.date, acquisition_dates: list) -> bool:
    return any(abs((day - datetime.date.fromisoformat(a)).days) <= WASH_DAYS for a in acquisition_dates)


def pick_lots(lots: list, usd: float, price: float, today: datetime.date, cfg: dict) -> dict:
    """Lots for a sale of ~usd: losses first (only outside the wash-sale windows), then
    long-term lots with the smallest gain per share, then short-term gain lots."""
    wash = in_wash_window(today, cfg["acquisition_dates"])
    is_lt = lambda lot: today >= datetime.date.fromisoformat(lot["acquired"]).replace(
        year=datetime.date.fromisoformat(lot["acquired"]).year + 1) + datetime.timedelta(days=1)
    losses = [x for x in lots if x["price"] > price and not wash]
    lt_g = sorted([x for x in lots if x["price"] <= price and is_lt(x)], key=lambda x: price - x["price"])
    st_g = sorted([x for x in lots if x["price"] <= price and not is_lt(x)], key=lambda x: price - x["price"])
    picked, left, tax = [], usd, 0.0
    for lot in losses + lt_g + st_g:
        if left <= 0:
            break
        q = min(lot["qty"], math.ceil(left / price))
        g = q * (price - lot["price"])
        tax += g * (cfg["lt_rate"] if is_lt(lot) else cfg["st_rate"])
        picked.append({**lot, "sell_qty": q, "gain": round(g, 2), "term": "LT" if is_lt(lot) else "ST"})
        left -= q * price
    return {"lots": picked, "shares": sum(x["sell_qty"] for x in picked), "est_tax": round(tax, 2),
            "wash_window": wash}


# ── Journal and RETRO ─────────────────────────────────────────────────────────

def read_journal(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def retro(journal: list, price_now: float, repl_now: dict) -> dict:
    """Score each past call: TRIM was right if the symbol lagged the replacement since;
    HOLD/WAIT were right if it kept up. Replacement = the one the call named, else the average."""
    scored = []
    for e in journal:
        keys = [e["replacement"]] if e.get("replacement") else list(e["replacements"])
        rr = [repl_now[k] / e["replacements"][k] - 1 for k in keys if k in repl_now and k in e["replacements"]]
        if not rr:
            continue
        sym_r = price_now / e["price"] - 1
        rep_r = sum(rr) / len(rr)
        good = sym_r < rep_r if e["action"] == "TRIM" else sym_r >= rep_r
        scored.append({"date": e["date"], "action": e["action"], "symbol_ret": sym_r, "replacement_ret": rep_r,
                       "good": good})
    return {"scored": scored, "hit_rate": sum(s["good"] for s in scored) / len(scored) if scored else None}


def append_journal(path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def add_lesson(path: str, text: str) -> bool:
    """Attach a lesson to the latest journal entry (the skill writes what was learned)."""
    rows = read_journal(path)
    if not rows:
        return False
    rows[-1].setdefault("lessons", []).append(text)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    return True


# ── Driver ────────────────────────────────────────────────────────────────────

def _pack_holdings(symbol: str):
    """Brokerage shares of the symbol and the accounts' net value, from the OceanView pack
    (offline read: never touches the broker, writes nothing)."""
    pack = build_oceanview_context(live=False)
    shares = sum(p["qty"] for p in pack["state"]["positions"] if p["symbol"] == symbol)
    net = sum(a.get("net_value") or 0 for a in pack["state"]["accounts"])
    return shares, net, pack["meta"]


def _live_price(symbol: str):
    """One read-only quote; None (and a warning) when it can't be had, so the report falls
    back to the last cached close instead of failing."""
    try:
        return ETradeClient("production", role="data").market.quotes([symbol]).get(symbol)
    except Exception as e:  # no token, no app keys, network: report on the cached close instead
        _log.warning("Live quote for %s unavailable (%s); using the last cached close.", symbol, e)
        return None


def run(symbol=None, live=False, journal=True, config_path=None) -> dict:
    cfg = load_config(config_path)
    symbol = (symbol or cfg["symbol"]).upper()
    b = load_bars(symbol)
    if b is None:
        raise SystemExit(f"no OHLCV for {symbol}")
    i = len(b["days"]) - 1
    m = symbol_metrics(b, i)
    peers = [p for p in (load_bars(s) for s in cfg["peers"]) if p]
    window = b["days"][max(0, i - BETA_WINDOW - 25):]
    sym_rets = [math.log(b["c"][b["idx"][y]] / b["c"][b["idx"][x]]) for x, y in zip(window, window[1:])]
    rb = beta_residual(sym_rets, basket_returns(peers, window)) if peers else None
    score, tags = patterns.rubber_band_reversal_score(
        {d: {"1. open": b["o"][k], "2. high": b["h"][k], "3. low": b["l"][k], "4. close": b["c"][k],
             "5. volume": b["v"][k]} for k, d in enumerate(b["days"])}, b["days"][i])
    repl = {}
    for s in cfg["replacements"]:
        rb_ = load_bars(s)
        if rb_:
            repl[s] = replacement_timing(rb_, len(rb_["days"]) - 1)
    price = m["close"]
    live_px = _live_price(symbol) if live else None
    if live_px:
        price = live_px
    shares, acct_net, meta = _pack_holdings(symbol)
    sym_value = (shares + cfg["stock_plan_shares"] + cfg["outside_symbol_shares"]) * price
    total = acct_net + cfg["stock_plan_shares"] * price + cfg["outside_assets_usd"]
    conc = concentration(sym_value, total, cfg["floor_pct"])
    call = decide(m, rb, tags, repl, conc, cfg)
    today = datetime.date.today()
    lots_path = os.path.join(watch_dir(), f"{symbol}_lots.json")
    lots = None
    if call["action"] == "TRIM" and os.path.exists(lots_path):
        with open(lots_path, encoding="utf-8") as f:
            lots = pick_lots(json.load(f), call["tranche_usd"], price, today, cfg)
    jpath = os.path.join(watch_dir(), f"{symbol}_journal.jsonl")
    past = read_journal(jpath)
    r = retro(past, price, {k: v["close"] for k, v in repl.items()})
    report = {"symbol": symbol, "rules": RULES_VERSION, "as_of_bar": m["date"], "price": price,
              "price_source": "live quote" if live_px else f"close {m['date']}", "pack_health": meta.get("health"),
              "metrics": m, "rbr": {"score": score, "tags": tags}, "vs_peers": rb, "replacements": repl,
              "concentration": conc, "call": call, "lots": lots, "retro": r,
              "wash_window_today": in_wash_window(today, cfg["acquisition_dates"])}
    if journal:
        append_journal(jpath, {"date": today.isoformat(), "bar": m["date"], "price": price, "action": call["action"],
                               "tranche_usd": call["tranche_usd"], "replacement": call["replacement"],
                               "replacements": {k: v["close"] for k, v in repl.items()},
                               "pct": conc["pct"], "z_20": rb["z_20"] if rb else None, "rules": RULES_VERSION})
    return report


def print_report(rep: dict) -> None:
    m, c, k = rep["metrics"], rep["concentration"], rep["call"]
    f = lambda x, p="+.1%": "-" if x is None else format(x, p)
    _out(f"\n{rep['symbol']} position watch ({rep['rules']}), {rep['price_source']}, pack {rep['pack_health']}")
    _out(f"  price {rep['price']:.2f} | 1d {f(m['ret_1'])} 5d {f(m['ret_5'])} 20d {f(m['ret_20'])} 60d {f(m['ret_60'])}"
         f" | from 52w high {f(m['drawdown_52w'])}")
    _out(f"  vs SMA20 {f(m['vs_sma20'])} SMA50 {f(m['vs_sma50'])} SMA200 {f(m['vs_sma200'])} | gap {f(m['gap'])}"
         f" | volume {f(m['vol_ratio'], '.2f')}x | RBR {rep['rbr']['score']} {rep['rbr']['tags']}")
    p = rep["vs_peers"]
    if p:
        _out(f"  vs peers: beta {p['beta']:.2f}, corr {p['corr']:.2f}; own move 1d {p['resid_1']:+.1%} "
             f"5d {p['resid_5']:+.1%} 20d {p['resid_20']:+.1%} (z {p['z_20']:+.2f})")
    for s, r in rep["replacements"].items():
        _out(f"  replacement {s}: {r['close']:.2f}, vs SMA50 {f(r['stretch_50'])}, RSI {f(r['rsi14'], '.0f')}, "
             f"entry {'OK' if r['entry_ok'] else 'stretched'}")
    _out(f"  concentration {f(c['pct'], '.1%')} of total; above the floor: ${c['headroom_usd']:,.0f}")
    _out(f"\n  CALL: {k['action']}" + (f" ~${k['tranche_usd']:,.0f} into {k['replacement']}" if k["action"] == "TRIM" else ""))
    for reason in k["reasons"]:
        _out(f"    - {reason}")
    if rep["lots"]:
        lt = rep["lots"]
        _out(f"  lots ({lt['shares']} sh, est. tax ${lt['est_tax']:,.0f}, wash window {lt['wash_window']}):")
        for x in lt["lots"]:
            _out(f"    {x['acquired']} {x['sell_qty']} sh @ {x['price']:.2f} ({x['term']}, gain {x['gain']:+,.0f})")
    if rep["wash_window_today"]:
        _out("  note: inside a wash-sale window (vest / ESPP within 30 days): no loss sales today")
    rr = rep["retro"]
    if rr["scored"]:
        last = rr["scored"][-1]
        _out(f"\n  RETRO: last call {last['action']} on {last['date']}: {rep['symbol']} {last['symbol_ret']:+.1%} vs "
             f"replacement {last['replacement_ret']:+.1%} -> {'good' if last['good'] else 'bad'}; "
             f"hit rate {rr['hit_rate']:.0%} over {len(rr['scored'])} calls")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--live", action="store_true", help="use a live E*TRADE quote (read-only) for today's price")
    ap.add_argument("--no-journal", action="store_true", help="don't append today's call to the journal")
    ap.add_argument("--lesson", default=None, help="attach a lesson to the latest journal entry and exit")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    a = ap.parse_args()
    cfg = load_config()
    sym = (a.symbol or cfg["symbol"]).upper()
    if a.lesson:
        ok = add_lesson(os.path.join(watch_dir(), f"{sym}_journal.jsonl"), a.lesson)
        _out("lesson saved" if ok else "no journal entry yet")
        sys.exit(0 if ok else 1)
    rep = run(sym, live=a.live, journal=not a.no_journal)
    _out(json.dumps(rep, indent=1, default=str)) if a.json else print_report(rep)
