"""
Range surfing of sideways, wide-range stocks (R&D #122, winner hunt G-C). Pre-registered 2026-11-24.

Question: a stock that has moved sideways in a wide, repeating range for months: does buying near
the bottom of the range and selling near the top, with a stop just under the range, give more
return for less risk than holding SPY? R&D #86 (buy every short-term dip) found no edge over SPY;
this study only trades stocks whose sideways range is measured first.

Owner decisions (2026-11-24): the goal is maximum return, deterministic decisions and minimum
risk; SPY is only the yardstick. Risk comes first: a strategy whose worst drop is deeper than
SPY's is out, however much it earns. The range settings below are the owner-approved defaults
("start and adjust if needed"); any later change is a new, disclosed run.

All rules below are fixed before the first run (this file is committed and pushed first).

THE RANGE (decided at day t's close, from bars up to and including t)
  channel   U / L = highest high / lowest low of the CHANNEL_WIN (60) sessions BEFORE t;
            H = U - L. Today's bar is not in the channel, so a breakdown today is not "the bottom".
  wide      H >= K x ATR(14) at t. K = 4 is the primary setting; K = 3 and K = 5 are reported
            for context only and cannot change the verdict.
  flat      the fitted 120-session move (|OLS slope of the closes t-119..t| x 119) is at most
            TREND_MAX (0.5) x H: a slow trend does not count as sideways.
  repeating at least MIN_TRIPS (2) full trips between the bands inside the channel window: a bar
            whose low is in the bottom ZONE (25%) of the channel is a "low" visit, a bar whose high
            is in the top 25% a "high" visit (a bar that touches both is ignored), and each switch
            between low and high visits is one trip.
  history   a symbol signals only after MIN_HISTORY (253) bars of its own.

THE TRADE
  entry     close at t inside the bottom 25% of the channel (L <= close <= L + 0.25 H): buy at
            t+1's open, unless that open is at or below the stop or at or above the target.
  levels    frozen at the signal: target = U - 0.25 H, stop = L - STOP_ATR (0.5) x ATR(t).
  exits     checked on each close after the fill, executed at the next open: close <= stop ->
            "stop"; else close >= target -> "target"; else MAX_HOLD (60) sessions held -> "time".
  sizing    at most SLOTS (10) positions; each buy is POS_FRAC (10%) of equity at the open,
            trimmed so a stop-out loses at most the live per-trade cap (CFG max_trade_risk_pct,
            1% of equity), and never more than the cash on hand.
  order     more signals than free slots: lowest in the channel first ((close - L) / H), then
            the symbol name (deterministic).
  costs     10 bps a side on the dollars traded. Idle cash earns nothing (as in every
            portfolio_backtest study; it flatters SPY a little).

UNIVERSE AND PERIODS
  Symbols with daily OHLCV and a Symbol cache (as R&D #64), minus instruments.is_excluded, SPY
  kept only as the yardstick. Calendar 2004-01-02 .. the last SPY bar; split 2015-01-01
  (train = 2004-2014 with 2008, test = 2015-2026). Judged on R&D #62's draws
  (portfolio_backtest.draw_paths: seed 62, a random 80% of the symbols, a start 0-60 sessions
  late), N_DRAWS = 50, each half run separately from fresh equity.

YARDSTICKS (per draw and half)
  spy        SPY bought at the first open and held.
  ew         equal dollars in every universe symbol that trades on the first day, held: the
             survivorship check (today's symbol list flatters any stock strategy over 20 years).
  spy_match  SPY held at the surf portfolio's own exposure each day (the rest in cash), no
             costs: "would the money in use have done better in SPY?" (the idle-cash question).

DECISION RULE (surf_k4 only; PASS = 0.8 of the draws, on BOTH halves)
  risk gate   max drawdown no deeper than SPY's in >= PASS of draws on both halves, AND the
              median over draws of the 95th-percentile trade loss (as % of equity at entry) is
              at most 1.0 on both halves (gaps through the stop can exceed the 1% cap).
  FAIL_RISK            the risk gate fails.
  BETTER_THAN_SPY      gate passes AND CAGR above SPY's AND above ew's in >= PASS of draws on
                       both halves.
  SAFER_LOWER_RETURN   gate passes AND CAGR / |max drawdown| above SPY's in >= PASS of draws on
                       both halves (but not BETTER_THAN_SPY).
  INCONCLUSIVE         anything else.
  Idle-cash label, reported beside it: CASH_USE_PASS if CAGR beats spy_match in >= PASS of draws
  on both halves; CASH_USE_FAIL if it does in <= 0.2 on both; else CASH_USE_INCONCLUSIVE.

WHAT EACH VERDICT DOES
  BETTER_THAN_SPY -> propose it for a PROD shadow period (rule shadow log, T0.4); no wiring.
  SAFER_LOWER_RETURN or CASH_USE_PASS -> propose it as an idle-cash sleeve candidate for the same
  shadow; no wiring. FAIL_RISK / INCONCLUSIVE / CASH_USE_* not passing -> not wired; recorded
  in plans/roadmap.md with the numbers. Any pass is one test among the winner-hunt studies: a lone
  pass is checked again on new data before anything is built.

REPORTED, NOT JUDGED
  K = 3 and K = 5; trades, win rate, average hold, exits by reason, exposure; the share of trades
  that lose 15% or more (the "fewer big drops" check cannot be judged here: today's rules need
  the Chaikin score panel, which starts in 2023); on the full-universe path, the daily return
  difference surf_k4 - SPY with a Newey-West t (lag 5) and the mean daily return with SPY above
  vs below its 200-day average.

NOT MEASURED / BIASES
  Today's symbol list (survivorship: stocks that broke down and died are missing; the ew yardstick
  is there for this); daily bars only (an intraday stop would fill differently); no dividends on
  either side; no slippage beyond the 10 bps; draws overlap heavily, so a win rate is a
  sensitivity measure, not a probability (R&D #62).

Usage:
    python scripts/backtesting/range_surf_study.py [--draws 50] [--smoke]
"""
import argparse
import datetime
import glob
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import backtest_ratings as _br
import factor_portfolio as fp
import portfolio_backtest as pb
from aether import instruments
from aether.config import CFG
from setup_s10_gate_study import _t_hac

CHANNEL_WIN = 60
TREND_WIN = 120
K_PRIMARY = 4.0
K_CONTEXT = (3.0, 5.0)
MIN_TRIPS = 2
ZONE = 0.25
TREND_MAX = 0.5
STOP_ATR = 0.5
MAX_HOLD = 60
SLOTS = 10
POS_FRAC = 0.10
MIN_HISTORY = 253
COST_BPS = 10.0
START = "2004-01-02"
SPLIT = "2015-01-01"
N_DRAWS = 50
PASS = 0.8
FAIL = 0.2
TAIL_MAX_PCT = 1.0
BIG_DROP_PCT = -15.0
VARIANTS = {"surf_k4": K_PRIMARY, "surf_k3": K_CONTEXT[0], "surf_k5": K_CONTEXT[1]}
OUT_DIR = os.path.join(BASE_DIR, "Data")


# ── The range ─────────────────────────────────────────────────────────────────

def count_trips(highs, lows, upper, lower) -> int:
    """Switches between low-zone and high-zone visits over the given bars (a bar in both is ignored)."""
    h = upper - lower
    lo_line, hi_line = lower + ZONE * h, upper - ZONE * h
    state, trips = None, 0
    for hi, lo in zip(highs, lows, strict=True):
        is_low, is_high = lo <= lo_line, hi >= hi_line
        if is_low == is_high:
            continue
        now = "low" if is_low else "high"
        if state is not None and now != state:
            trips += 1
        state = now
    return trips


def _rolling_fit_move(c: np.ndarray, win: int) -> np.ndarray:
    """|OLS slope| x (win - 1) of the closes in the window ending at each index (nan before)."""
    n = len(c)
    out = np.full(n, np.nan)
    if n < win:
        return out
    j = np.arange(n, dtype=float)
    cy = np.concatenate([[0.0], np.cumsum(c)])
    cjy = np.concatenate([[0.0], np.cumsum(j * c)])
    x_sum = win * (win - 1) / 2.0
    xx_sum = (win - 1) * win * (2 * win - 1) / 6.0
    den = win * xx_sum - x_sum ** 2
    ends = np.arange(win - 1, n)
    starts = ends - win + 1
    sy = cy[ends + 1] - cy[starts]
    sxy = (cjy[ends + 1] - cjy[starts]) - starts * sy
    out[ends] = np.abs((win * sxy - x_sum * sy) / den) * (win - 1)
    return out


def range_signals(b: dict, k_min: float = min(K_CONTEXT + (K_PRIMARY,))) -> dict:
    """{bar index: signal} for every day the range and the entry rule hold at width >= k_min.
    A signal carries ratio (H / ATR, so each variant filters on its own K), pos, target and stop."""
    c = np.asarray(b["c"], dtype=float)
    h = np.asarray(b["h"], dtype=float)
    lo = np.asarray(b["l"], dtype=float)
    atr = np.asarray(b["atr"], dtype=float)
    n = len(c)
    first = max(CHANNEL_WIN, TREND_WIN - 1, MIN_HISTORY - 1)
    if n <= first:
        return {}
    win_h = np.lib.stride_tricks.sliding_window_view(h, CHANNEL_WIN).max(axis=1)
    win_l = np.lib.stride_tricks.sliding_window_view(lo, CHANNEL_WIN).min(axis=1)
    move = _rolling_fit_move(c, TREND_WIN)
    out = {}
    for i in range(first, n):
        upper, lower = win_h[i - CHANNEL_WIN], win_l[i - CHANNEL_WIN]     # the 60 bars before i
        height = upper - lower
        if height <= 0 or atr[i] <= 0 or not np.isfinite(move[i]):
            continue
        ratio = height / atr[i]
        if ratio < k_min or move[i] > TREND_MAX * height:
            continue
        if not (lower <= c[i] <= lower + ZONE * height):
            continue
        if count_trips(h[i - CHANNEL_WIN:i], lo[i - CHANNEL_WIN:i], upper, lower) < MIN_TRIPS:
            continue
        out[i] = {"ratio": float(ratio), "pos": float((c[i] - lower) / height),
                  "target": float(upper - ZONE * height), "stop": float(lower - STOP_ATR * atr[i])}
    return out


def signals_by_day(bars: dict, symbols) -> dict:
    """{day: [(sym, signal), ...]} over the given symbols."""
    by_day = {}
    for s in symbols:
        b = bars[s]
        dates = sorted(b["idx"], key=b["idx"].get)
        for i, sig in range_signals(b).items():
            by_day.setdefault(dates[i], []).append((s, sig))
    return by_day


# ── The portfolio ─────────────────────────────────────────────────────────────

def simulate_surf(bars, calendar, universe, by_day, k, *, cost_bps=COST_BPS, initial=pb.INITIAL_EQUITY,
                  risk_pct=None) -> dict:
    """Day loop: fills at the open, marks and exits at the close, new signals at the close.
    Returns portfolio_backtest.metrics()-compatible output plus per-trade loss vs equity."""
    c = cost_bps / 10_000.0
    risk_pct = CFG.system_max_trade_risk_pct if risk_pct is None else risk_pct
    uni = set(universe)
    cash, pos, last = initial, {}, {}
    pending_buys, pending_sells = [], {}
    curve, trades, traded = [], [], 0.0
    for day in calendar:
        for s in list(pending_sells):                                       # sells at the open
            b, i = pb._bar(bars, s, day)
            if b is None:
                continue
            p = pos.pop(s)
            px = b["o"][i]
            cash += p["qty"] * px * (1 - c)
            traded += p["qty"] * px
            ret = (px * (1 - c)) / (p["entry"] * (1 + c)) - 1
            trades.append({"sym": s, "ret_pct": ret * 100, "reason": pending_sells.pop(s), "held": p["held"],
                           "loss_pct_equity": min(0.0, p["qty"] * (px * (1 - c) - p["entry"] * (1 + c)))
                           / p["equity_at_entry"] * 100})
        if pending_buys:
            opens = {}
            for s in pos:
                b, i = pb._bar(bars, s, day)
                opens[s] = b["o"][i] if b is not None else last.get(s, pos[s]["entry"])
            equity = cash + sum(p["qty"] * opens[s] for s, p in pos.items())
            for s, sig in pending_buys:                                      # buys at the open
                if len(pos) >= SLOTS or s in pos:
                    continue
                b, i = pb._bar(bars, s, day)
                if b is None:
                    continue
                px = b["o"][i]
                if px <= sig["stop"] or px >= sig["target"]:
                    continue
                value = min(POS_FRAC * equity, risk_pct * equity / (px - sig["stop"]) * px, cash / (1 + c))
                if value <= 1e-6:
                    continue
                qty = value / px
                cash -= value * (1 + c)
                traded += value
                pos[s] = {"qty": qty, "entry": px, "stop": sig["stop"], "target": sig["target"], "held": 0,
                          "equity_at_entry": equity}
            pending_buys = []
        for s, p in pos.items():                                             # marks and exits at the close
            b, i = pb._bar(bars, s, day)
            if b is None:
                continue
            last[s] = b["c"][i]
            p["held"] += 1
            if s in pending_sells:
                continue
            if b["c"][i] <= p["stop"]:
                pending_sells[s] = "stop"
            elif b["c"][i] >= p["target"]:
                pending_sells[s] = "target"
            elif p["held"] >= MAX_HOLD:
                pending_sells[s] = "time"
        invested = sum(p["qty"] * last.get(s, p["entry"]) for s, p in pos.items())
        curve.append((day, cash + invested, invested))
        cands = [(s, sig) for s, sig in by_day.get(day, ()) if s in uni and sig["ratio"] >= k and s not in pos]
        pending_buys = sorted(cands, key=lambda x: (x[1]["pos"], x[0]))
    return {"curve": curve, "trades": trades, "traded_usd": traded, "open_positions": sorted(pos)}


def ew_hold(closes: np.ndarray, opens0: np.ndarray, cost_bps=COST_BPS, initial=pb.INITIAL_EQUITY) -> np.ndarray:
    """Equal dollars in each row bought at its first-day open, held; closes forward-filled (rows x days).
    The same accounting as portfolio_backtest.buy_and_hold, vectorised."""
    qty = initial / len(opens0) / (opens0 * (1 + cost_bps / 10_000.0))
    return qty @ closes


def spy_matched(curve, spy_close: dict) -> list:
    """SPY held at the portfolio's exposure of the previous close (rest in cash), no costs."""
    out, eq = [], curve[0][1]
    for (d0, e0, i0), (d1, _e1, _i1) in zip(curve, curve[1:]):
        if not out:
            out.append((d0, eq, 0.0))
        expo = i0 / e0 if e0 else 0.0
        eq *= 1 + expo * (spy_close[d1] / spy_close[d0] - 1)
        out.append((d1, eq, expo * eq))
    return out


def tail_loss(trades) -> float | None:
    """95th-percentile trade loss as % of equity at entry (positive number), None without trades."""
    losses = [-t["loss_pct_equity"] for t in trades]
    return float(np.percentile(losses, 95)) if losses else None


def summarize(result) -> dict:
    m = pb.metrics(result)
    if not m:
        return {}
    trades = result["trades"]
    m["tail_loss_p95_pct"] = None if tail_loss(trades) is None else round(tail_loss(trades), 3)
    m["big_drop_share"] = round(sum(t["ret_pct"] <= BIG_DROP_PCT for t in trades) / len(trades), 4) if trades else None
    m["avg_hold"] = round(float(np.mean([t["held"] for t in trades])), 1) if trades else None
    m["exits"] = {r: sum(t["reason"] == r for t in trades) for r in ("stop", "target", "time")}
    return m


# ── The verdict ───────────────────────────────────────────────────────────────

def _calmar(m) -> float:
    dd = m.get("max_drawdown_pct")
    if dd is None or m.get("cagr_pct") is None:
        return float("nan")
    return m["cagr_pct"] / abs(dd) if dd < 0 else float("inf")


def win_rates(per_draw: list, v: str = "surf_k4") -> dict:
    """Share of draws (per half) where `v` beats each yardstick on each pre-registered measure."""
    out = {}
    for half in ("train", "test"):
        rows = [d[half] for d in per_draw if d[half].get(v) and d[half].get("spy")]
        n = len(rows)

        def share(fn, rows=rows, n=n):
            return round(sum(1 for r in rows if fn(r)) / n, 3) if n else None
        out[half] = {
            "n": n,
            "dd_not_deeper_than_spy": share(lambda r: r[v]["max_drawdown_pct"] >= r["spy"]["max_drawdown_pct"]),
            "cagr_above_spy": share(lambda r: r[v]["cagr_pct"] > r["spy"]["cagr_pct"]),
            "cagr_above_ew": share(lambda r: r.get("ew") and r[v]["cagr_pct"] > r["ew"]["cagr_pct"]),
            "calmar_above_spy": share(lambda r: _calmar(r[v]) > _calmar(r["spy"])),
            "cagr_above_spy_match": share(lambda r: r.get("spy_match")
                                          and r[v]["cagr_pct"] > r["spy_match"]["cagr_pct"]),
            "tail_loss_p95_median": (round(float(np.median(t)), 3)
                                     if (t := [r[v]["tail_loss_p95_pct"] for r in rows
                                               if r[v].get("tail_loss_p95_pct") is not None]) else None),
        }
    return out


def verdict(w: dict) -> dict:
    """The pre-registered labels from win_rates() of surf_k4 (see the module docstring)."""
    halves = [w[h] for h in ("train", "test")]

    def both(key, bar=PASS):
        return all(h[key] is not None and h[key] >= bar for h in halves)
    tail_ok = all(h["tail_loss_p95_median"] is not None and h["tail_loss_p95_median"] <= TAIL_MAX_PCT
                  for h in halves)
    gate = both("dd_not_deeper_than_spy") and tail_ok
    if not gate:
        label = "FAIL_RISK"
    elif both("cagr_above_spy") and both("cagr_above_ew"):
        label = "BETTER_THAN_SPY"
    elif both("calmar_above_spy"):
        label = "SAFER_LOWER_RETURN"
    else:
        label = "INCONCLUSIVE"
    if both("cagr_above_spy_match"):
        cash = "CASH_USE_PASS"
    elif all(h["cagr_above_spy_match"] is not None and h["cagr_above_spy_match"] <= FAIL for h in halves):
        cash = "CASH_USE_FAIL"
    else:
        cash = "CASH_USE_INCONCLUSIVE"
    return {"label": label, "cash_use": cash, "risk_gate": gate, "tail_ok": tail_ok}


# ── Driver ────────────────────────────────────────────────────────────────────

def load_universe(smoke=False):
    ohlcv = {os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(_br.OHLCV_DIR, "*_daily.json"))}
    cached = {d for d in os.listdir(_br.SYM_DIR) if os.path.isdir(os.path.join(_br.SYM_DIR, d))}
    symbols = sorted(ohlcv & cached | {"SPY"})
    if smoke:
        symbols = sorted(set(symbols[:40]) | {"SPY"})
    bars = {s: b for s in symbols if (ts := _br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    universe = [s for s in symbols if s in bars and s != "SPY" and not instruments.is_excluded(s)]
    return bars, universe


def close_matrix(bars, symbols, calendar):
    """Forward-filled closes (rows = symbols, cols = calendar) and each row's first open (nan if absent)."""
    m = np.full((len(symbols), len(calendar)), np.nan)
    for r, s in enumerate(symbols):
        b = bars[s]
        for j, d in enumerate(calendar):
            i = b["idx"].get(d)
            if i is not None:
                m[r, j] = b["c"][i]
    for j in range(1, len(calendar)):
        gap = np.isnan(m[:, j])
        m[gap, j] = m[gap, j - 1]
    return m


def run(draws=N_DRAWS, smoke=False) -> dict:
    bars, universe = load_universe(smoke)
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if d >= START]
    spy_close = {d: bars["SPY"]["c"][bars["SPY"]["idx"][d]] for d in calendar}
    by_day = signals_by_day(bars, universe)
    col = {d: j for j, d in enumerate(calendar)}
    cm = close_matrix(bars, universe, calendar)
    row = {s: r for r, s in enumerate(universe)}
    pb._out(f"\nRange surf (R&D #122): {len(universe)} symbols, {calendar[0]} -> {calendar[-1]}, split {SPLIT}, "
            f"{draws} draws, {sum(len(v) for v in by_day.values())} signal rows")

    def table(syms, days):
        uni = [s for s in universe if s in syms]
        out = {}
        for v, k in VARIANTS.items():
            res = simulate_surf(bars, days, uni, by_day, k)
            out[v] = summarize(res)
            if v == "surf_k4":
                out["spy_match"] = pb.metrics({"curve": spy_matched(res["curve"], spy_close), "trades": [],
                                               "traded_usd": 0.0})
        out["spy"] = pb.metrics(pb.buy_and_hold(bars, days, ["SPY"], COST_BPS))
        held = [s for s in uni if pb._bar(bars, s, days[0])[0] is not None]
        if held:
            rows = [row[s] for s in held]
            opens0 = np.array([bars[s]["o"][bars[s]["idx"][days[0]]] for s in held])
            eq = ew_hold(cm[rows][:, col[days[0]]:col[days[-1]] + 1], opens0)
            out["ew"] = pb.metrics({"curve": [(d, float(e), float(e)) for d, e in zip(days, eq, strict=True)],
                                    "trades": [], "traded_usd": 0.0})
        return out

    per_draw = []
    for n, path in enumerate(pb.draw_paths(["SPY", *universe], calendar, draws, split=SPLIT), 1):
        full = [d for d in calendar if d >= path["start"]]
        per_draw.append({"train": table(path["symbols"], [d for d in full if d < SPLIT]),
                         "test": table(path["symbols"], [d for d in full if d >= SPLIT])})
        if n % 10 == 0:
            pb._out(f"  {n}/{draws} draws")
    w = {v: win_rates(per_draw, v) for v in VARIANTS}
    v = verdict(w["surf_k4"])

    # Reported, not judged: the full-universe path.
    full_res = simulate_surf(bars, calendar, universe, by_day, K_PRIMARY)
    eq = np.array([e for _d, e, _i in full_res["curve"]])
    surf_r = eq[1:] / eq[:-1] - 1
    spy_r = np.array([spy_close[d1] / spy_close[d0] - 1 for d0, d1 in zip(calendar, calendar[1:])])
    diff = list(surf_r - spy_r)
    above = np.array([fp.spy_above_trend(bars["SPY"], d) for d in calendar[:-1]])
    path_report = {
        "surf_k4": summarize(full_res),
        "spy": pb.metrics(pb.buy_and_hold(bars, calendar, ["SPY"], COST_BPS)),
        "daily_diff_bps": round(float(np.mean(diff)) * 1e4, 3), "daily_diff_hac_t_lag5": _t_hac(diff, 5),
        "spy_above_200d": {"days": int(above.sum()), "surf_bps": round(float(surf_r[above].mean()) * 1e4, 3),
                           "spy_bps": round(float(spy_r[above].mean()) * 1e4, 3)},
        "spy_below_200d": {"days": int((~above).sum()), "surf_bps": round(float(surf_r[~above].mean()) * 1e4, 3),
                           "spy_bps": round(float(spy_r[~above].mean()) * 1e4, 3)},
    }

    for half in ("train", "test"):
        pb._out(f"\n  {half}: medians over draws        CAGR%   MaxDD%  Sharpe  expo%  trades  tail95%")
        names = [*VARIANTS, "spy_match", "spy", "ew"]
        for name in names:
            vals = [d[half][name] for d in per_draw if d[half].get(name)]
            med = lambda key, vals=vals: (round(float(np.median(x)), 2)
                                          if (x := [r[key] for r in vals if r.get(key) is not None]) else "-")
            pb._out(f"  {name:<12}{med('cagr_pct')!s:>22}{med('max_drawdown_pct')!s:>9}{med('sharpe')!s:>8}"
                    f"{med('avg_exposure_pct')!s:>7}{med('n_trades')!s:>8}{med('tail_loss_p95_pct')!s:>9}")
    for name, rates in w.items():
        pb._out(f"\n  {name} win rates: " + "; ".join(f"{h}: " + ", ".join(f"{k} {x}" for k, x in r.items())
                                                       for h, r in rates.items()))
    pb._out(f"\n  R&D #122 verdict (surf_k4): {v['label']}  |  idle cash: {v['cash_use']}")
    pb._out(f"  Full path: surf_k4 - SPY {path_report['daily_diff_bps']} bps/day, HAC t {path_report['daily_diff_hac_t_lag5']}")

    payload = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "as_of": calendar[-1],
               "start": calendar[0], "split": SPLIT, "n_draws": draws, "n_symbols": len(universe), "smoke": smoke,
               "verdict": v, "win_rates": w, "full_path": path_report,
               "medians": {h: {name: {k: (float(np.median(x)) if (x := [d[h][name][k] for d in per_draw
                                                                         if d[h].get(name) and d[h][name].get(k) is not None])
                                          else None)
                                      for k in ("cagr_pct", "max_drawdown_pct", "sharpe", "avg_exposure_pct", "n_trades")}
                               for name in [*VARIANTS, "spy_match", "spy", "ew"]} for h in ("train", "test")},
               "caveats": ["today's symbol list (survivorship); ew is the check",
                           "draws overlap heavily; win rates are sensitivity, not probability (R&D #62)",
                           "daily bars: stops fill at the next open, not intraday"]}
    if not smoke:
        path = os.path.join(OUT_DIR, f"range_surf_study_{datetime.date.today().isoformat()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, default=str)
        pb._out(f"\n  Wrote {path}")
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--draws", type=int, default=N_DRAWS)
    ap.add_argument("--smoke", action="store_true", help="40 symbols, 2 draws, no file: checks it runs, not a result")
    a = ap.parse_args()
    run(2 if a.smoke else a.draws, a.smoke)
