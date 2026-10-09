"""
Rubber-Band Stop Study — after a gap below the stop, does a rebound limit beat selling at market?

Context: PR #181 changed R&D #8 so a breached stop is a stop-limit with its limit at the
market price (it fills at the market price). The owner asked: when we can predict with high
enough probability that the price will snap back ("rubber band"), should the limit sit at a
projected rebound instead? This study measures that before any rule is wired.

Event (no look-ahead): on day t a symbol OPENS below the stop the production code would hold
from the previous close — risk_utils.resolve_stop_detailed(close[t-1], bars up to t-1,
exclude_swing=instruments.is_excluded(sym)), the same call the buy path uses. Bars are the
project's split-adjusted, placeholder-free series (risk_utils._load_ohlcv_bars). After an
event the symbol is skipped for 10 bars (the position would be closed). Events whose previous
or next real bar is more than 5 calendar days away are dropped (a stale bar is not a gap).

Market alternative M = open[t] (the 07:00 PT run is ~30 min after the open; daily bars have
no 10:00 ET price, so the open is the proxy).

Rebound policies (limit L, fixed before running):
    STOP   L = the stop (full snap-back)
    HALF   L = open[t] + 0.5 * (stop - open[t]) (half the gap)
Fill rule (PRIMARY, conservative): the limit works from day t+1 only, because daily bars
cannot show whether day t's high came before or after the 07:00 PT order: open[t+1] >= L ->
fill at open[t+1]; elif high[t+1] >= L -> fill at L; else sell at close[t+1] (the cost of a
missed bounce). SAME-DAY variant (context only): day t's high also counts. It is not strictly
better: a fill at L on day t can be lower than waiting for a higher open on day t+1.
Outcome per event: fill / M - 1 (positive = the rebound limit did better than market).

Groups (pre-registered): ALL; MARKET (SPY opened <= -0.5% that day) vs IDIO (SPY > -0.5%);
SMALL gap (stop - open <= 1 ATR) vs LARGE. 2 policies x 5 groups = 10 tests.

Statistic: events on one date share a market move, so outcomes are averaged per date and
tested across dates with a Newey-West (HAC) t, lag 1 (the outcome spans 2 sessions).

Decision rule (stated before running), per policy x group:
    REBOUND_HELPS  HAC t >= 2.81 (Bonferroni for 10 tests at 5%) AND mean >= +0.25%
                   AND median > 0 AND n_dates >= 30  -> propose a rebound limit for that
                   group only (its own PR, gated on the risk rules)
    REBOUND_HURTS  HAC t <= -2.81 -> keep STP LMT @ market
    otherwise      INCONCLUSIVE   -> keep STP LMT @ market (never wire on inconclusive)

Usage:
    AETHER_CACHE_DIR=<main checkout>/Data python scripts/backtesting/rubber_band_stop_study.py \
        [--min-date 2023-01-01] [--workers 8] [--json PATH]
Reads the OHLCV cache only; writes nothing unless --json is given.
"""
import argparse
import datetime
import glob
import json
import multiprocessing
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
from aether import instruments, paths, risk_utils
from bar_provenance import real_dates
from setup_s10_gate_study import _mean, _t_hac

GATE_T = 2.81
MIN_EFFECT = 0.0025
MIN_DATES = 30
HAC_LAG = 1
SPY_MARKET_GAP = -0.005
COOLDOWN_BARS = 10
MAX_BAR_GAP_DAYS = 5
POLICIES = ("STOP", "HALF")
GROUPS = ("ALL", "MARKET", "IDIO", "SMALL", "LARGE")


def _out(line: str = "") -> None:
    sys.stdout.write(line + "\n")


def load_bars(symbol: str):
    """(dates, opens, highs, lows, closes): real bars only, split-adjusted with the production
    split_adjust_ohlcv. It returns (highs, lows, closes); detection uses only opens and closes,
    so passing the opens in the highs slot returns opens on the same adjusted scale."""
    path = os.path.join(paths.ohlcv_dir(), f"{symbol}_daily.json")
    try:
        with open(path, encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except (OSError, ValueError):
        return [], [], [], [], []
    dates = real_dates(ts)
    if len(dates) < 2:
        return [], [], [], [], []
    o = [float(ts[d].get("1. open", 0) or 0) for d in dates]
    h = [float(ts[d]["2. high"]) for d in dates]
    lo = [float(ts[d]["3. low"]) for d in dates]
    c = [float(ts[d]["4. close"]) for d in dates]
    h_adj, lo_adj, c_adj = risk_utils.split_adjust_ohlcv(o, h, lo, c)
    o_adj, _, _ = risk_utils.split_adjust_ohlcv(o, o, lo, c)
    return dates, o_adj, h_adj, lo_adj, c_adj


def _days_between(a: str, b: str) -> int:
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def find_events(symbol, dates, opens, highs, lows, closes, min_date, exclude_swing=False):
    """Gap-below-stop events for one symbol. Stop from bars up to t-1 only."""
    events = []
    t, n = 61, len(dates)
    while t < n - 1:
        if dates[t] < min_date:
            t += 1
            continue
        if (_days_between(dates[t - 1], dates[t]) > MAX_BAR_GAP_DAYS
                or _days_between(dates[t], dates[t + 1]) > MAX_BAR_GAP_DAYS):
            t += 1
            continue
        sd = risk_utils.resolve_stop_detailed(closes[t - 1], highs=highs[:t], lows=lows[:t],
                                              closes=closes[:t], exclude_swing=exclude_swing)
        stop = sd.get("stop")
        if stop and opens[t] > 0 and opens[t] < stop:
            atr = risk_utils._atr_from_series(highs[:t], lows[:t], closes[:t])
            events.append({
                "sym": symbol, "date": dates[t], "stop": stop, "source": sd.get("source"),
                "open": opens[t], "high": highs[t], "open1": opens[t + 1],
                "high1": highs[t + 1], "close1": closes[t + 1], "atr": atr,
            })
            t += COOLDOWN_BARS
            continue
        t += 1
    return events


def limit_price(policy: str, ev: dict) -> float:
    if policy == "STOP":
        return ev["stop"]
    if policy == "HALF":
        return ev["open"] + 0.5 * (ev["stop"] - ev["open"])
    raise ValueError(policy)


def rebound_fill(ev: dict, limit: float, same_day: bool = False) -> float:
    """Fill price of a sell limit placed after the open of day t (see the docstring)."""
    if same_day and ev["high"] >= limit:
        return limit
    if ev["open1"] >= limit:
        return ev["open1"]
    if ev["high1"] >= limit:
        return limit
    return ev["close1"]


def outcome(ev: dict, policy: str, same_day: bool = False) -> float:
    return rebound_fill(ev, limit_price(policy, ev), same_day) / ev["open"] - 1


def groups_of(ev: dict, spy_gap: dict) -> list:
    out = ["ALL"]
    g = spy_gap.get(ev["date"])
    if g is not None:
        out.append("MARKET" if g <= SPY_MARKET_GAP else "IDIO")
    if ev.get("atr"):
        out.append("SMALL" if (ev["stop"] - ev["open"]) <= ev["atr"] else "LARGE")
    return out


def verdict(t_hac, mean, median, n_dates) -> str:
    if t_hac is None or n_dates < MIN_DATES:
        return "INCONCLUSIVE"
    if t_hac >= GATE_T and mean >= MIN_EFFECT and median > 0:
        return "REBOUND_HELPS"
    if t_hac <= -GATE_T:
        return "REBOUND_HURTS"
    return "INCONCLUSIVE"


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def summarize(events, spy_gap, same_day=False) -> dict:
    res = {}
    for policy in POLICIES:
        for grp in GROUPS:
            per_date = defaultdict(list)
            raw, hits = [], 0
            for ev in events:
                if grp not in groups_of(ev, spy_gap):
                    continue
                x = outcome(ev, policy, same_day)
                per_date[ev["date"]].append(x)
                raw.append(x)
                lim = limit_price(policy, ev)
                hits += (same_day and ev["high"] >= lim) or ev["open1"] >= lim or ev["high1"] >= lim
            series = [_mean(per_date[d]) for d in sorted(per_date)]
            m = _mean(series)
            med = _median(raw)
            t = _t_hac(series, HAC_LAG) if series else None
            s = sorted(raw)
            res[f"{policy}/{grp}"] = {
                "n_events": len(raw), "n_dates": len(series),
                "mean_per_date": m, "median_event": med,
                "p5_event": s[int(0.05 * (len(s) - 1))] if s else None,
                "fill_rate": hits / len(raw) if raw else None,
                "t_hac": t, "verdict": verdict(t, m or 0.0, med or 0.0, len(series)),
            }
    return res


def robustness(events, spy_gap, key: str) -> dict:
    """For a policy/group that passed: per-year means and the HAC t after dropping the best
    dates, so one crash-and-rebound episode can't carry the verdict alone."""
    policy, grp = key.split("/")
    per_date = defaultdict(list)
    for ev in events:
        if grp in groups_of(ev, spy_gap):
            per_date[ev["date"]].append(outcome(ev, policy))
    ser = {d: _mean(v) for d, v in per_date.items()}
    by_year = defaultdict(list)
    for d, v in ser.items():
        by_year[d[:4]].append(v)
    out = {"by_year": {y: {"dates": len(v), "mean": _mean(v)} for y, v in sorted(by_year.items())},
           "drop_best": {}}
    ranked = sorted(ser, key=lambda d: -ser[d])
    for k in (0, 3, 5, 10):
        keep = sorted(ranked[k:])
        xs = [ser[d] for d in keep]
        out["drop_best"][k] = {"dates": len(xs), "mean": _mean(xs), "t_hac": _t_hac(xs, HAC_LAG)}
    return out


def _spy_gaps() -> dict:
    dates, o, _, _, c = load_bars("SPY")
    return {dates[i]: o[i] / c[i - 1] - 1 for i in range(1, len(dates)) if c[i - 1] > 0}


def _symbol_events(job):
    sym, min_date = job
    bars = load_bars(sym)
    if not bars[0]:
        return []
    return find_events(sym, *bars, min_date=min_date, exclude_swing=instruments.is_excluded(sym))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-date", default="2023-01-01")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--limit", type=int, default=0, help="first N symbols only (smoke test)")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    syms = sorted(os.path.basename(p)[:-len("_daily.json")]
                  for p in glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json")))
    if args.limit:
        syms = syms[:args.limit]
    with multiprocessing.Pool(args.workers) as pool:
        events = [e for evs in pool.map(_symbol_events, [(s, args.min_date) for s in syms]) for e in evs]
    spy_gap = _spy_gaps()
    primary = summarize(events, spy_gap)
    secondary = summarize(events, spy_gap, same_day=True)

    _out(f"Rubber-band stop study  run {datetime.date.today()}  symbols {len(syms)}  "
         f"events {len(events)}  from {args.min_date}")
    xs = sorted(e["open"] / e["stop"] - 1 for e in events)
    if xs:
        _out(f"gap below stop: median {_median(xs)*100:.2f}%  p5 {xs[int(0.05*(len(xs)-1))]*100:.2f}%  "
             f"min {xs[0]*100:.2f}%")
    _out("PRIMARY (limit works from day t+1)")
    for k, r in primary.items():
        _out(f"  {k:12s} n={r['n_events']:5d} dates={r['n_dates']:4d} fill={(r['fill_rate'] or 0)*100:5.1f}% "
             f"mean={(r['mean_per_date'] or 0)*100:+6.2f}% median={(r['median_event'] or 0)*100:+6.2f}% "
             f"p5={(r['p5_event'] or 0)*100:+6.2f}% t_hac={r['t_hac'] if r['t_hac'] is None else round(r['t_hac'], 2)} "
             f"-> {r['verdict']}")
    passed = [k for k, r in primary.items() if r["verdict"] == "REBOUND_HELPS"]
    rob = {k: robustness(events, spy_gap, k) for k in passed}
    for k, r in rob.items():
        _out(f"ROBUSTNESS {k}")
        for y, v in r["by_year"].items():
            _out(f"  {y}: dates={v['dates']:4d} mean={v['mean']*100:+6.2f}%")
        for n, v in r["drop_best"].items():
            t = v["t_hac"]
            _out(f"  drop best {n:2d} dates: dates={v['dates']:4d} mean={v['mean']*100:+6.2f}% "
                 f"t_hac={t if t is None else round(t, 2)}")
    _out("SAME-DAY variant (day t's high also counts) - context only")
    for k, r in secondary.items():
        _out(f"  {k:12s} fill={(r['fill_rate'] or 0)*100:5.1f}% mean={(r['mean_per_date'] or 0)*100:+6.2f}% "
             f"t_hac={r['t_hac'] if r['t_hac'] is None else round(r['t_hac'], 2)}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"run_date": str(datetime.date.today()), "min_date": args.min_date,
                       "symbols": len(syms), "events": len(events),
                       "rule": {"gate_t": GATE_T, "min_effect": MIN_EFFECT, "min_dates": MIN_DATES,
                                "hac_lag": HAC_LAG},
                       "primary": primary, "same_day": secondary, "robustness": rob}, f, indent=1)


if __name__ == "__main__":
    main()
