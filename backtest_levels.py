"""
Backtest for detect_support / detect_resistance level accuracy.

Walks a symbol's OHLCV history. At each past bar it computes the predicted stop
(confirmed swing-low support) and target (confirmed swing-high resistance) using
ONLY data up to that bar, then looks `horizon` bars forward and asks:

  - did the support hold (price never closed the gap below it)?
  - did price reach the target (resistance)?
  - which happened first — target or stop? (the R:R outcome / win rate)
  - how close was each predicted level to the actual forward extreme?

No look-ahead: the level at bar i is built from series[:i+1]; the outcome from
series[i+1 : i+1+horizon]. Pure functions take series directly (unit-testable);
backtest_symbol() loads from the local cache.

    python backtest_levels.py INTC
    python backtest_levels.py INTC AAPL --horizon 20 --step 5
"""
import statistics
import sys

import risk_utils


def _evaluate(support, resistance, fwd_lows, fwd_highs):
    """Outcome of one prediction against its forward window."""
    res = {"support": support, "resistance": resistance}
    stop_day = target_day = None
    if support is not None:
        stop_day = next((j for j, lo in enumerate(fwd_lows) if lo < support), None)
        res["support_held"] = stop_day is None
        res["min_fwd_low"] = min(fwd_lows) if fwd_lows else None
    if resistance is not None:
        target_day = next((j for j, hi in enumerate(fwd_highs) if hi >= resistance), None)
        res["target_hit"] = target_day is not None
        res["max_fwd_high"] = max(fwd_highs) if fwd_highs else None
    if support is not None and resistance is not None:
        if target_day is not None and (stop_day is None or target_day <= stop_day):
            res["outcome"] = "target_first"
        elif stop_day is not None:
            res["outcome"] = "stop_first"
        else:
            res["outcome"] = "neither"
    return res


def backtest_series(highs, lows, closes, horizon=20, step=5, start_after=200,
                    k=risk_utils.PIVOT_K, lookback=risk_utils.PIVOT_LOOKBACK):
    """Run the walk-forward evaluation over in-memory series; return per-sample records."""
    n = min(len(highs), len(lows), len(closes))
    records = []
    for i in range(max(start_after, k), n - horizon, step):
        price = closes[i]
        if not price or price <= 0:
            continue
        support = risk_utils.detect_support(price, lows[:i + 1], k=k, lookback=lookback)
        resistance = risk_utils.detect_resistance(price, highs[:i + 1], k=k, lookback=lookback)
        if support is None and resistance is None:
            continue
        rec = _evaluate(support, resistance,
                        lows[i + 1:i + 1 + horizon], highs[i + 1:i + 1 + horizon])
        rec["price"] = price
        records.append(rec)
    return records


def aggregate(records):
    """Summarize per-sample records into accuracy metrics."""
    sup = [r for r in records if r.get("support") is not None]
    res = [r for r in records if r.get("resistance") is not None]
    both = [r for r in records if "outcome" in r]

    def med(xs):
        return round(statistics.median(xs), 2) if xs else None

    out = {"samples": len(records)}
    if sup:
        # gap% = how far the forward low sat relative to the predicted support
        # (>0 support never reached, ~0 tested precisely, <0 support broke).
        gaps = [(r["min_fwd_low"] - r["support"]) / r["price"] * 100 for r in sup]
        out["support"] = {
            "n": len(sup),
            "hold_rate": round(sum(r["support_held"] for r in sup) / len(sup) * 100, 1),
            "tested_within_1pct": round(sum(1 for g in gaps if g <= 1.0) / len(gaps) * 100, 1),
            "median_gap_pct": med(gaps),
        }
    if res:
        gaps = [(r["max_fwd_high"] - r["resistance"]) / r["price"] * 100 for r in res]
        out["resistance"] = {
            "n": len(res),
            "hit_rate": round(sum(r["target_hit"] for r in res) / len(res) * 100, 1),
            "median_gap_pct": med(gaps),
        }
    if both:
        c = {"target_first": 0, "stop_first": 0, "neither": 0}
        for r in both:
            c[r["outcome"]] += 1
        decided = c["target_first"] + c["stop_first"]
        out["outcome"] = {
            **c,
            "win_rate": round(c["target_first"] / decided * 100, 1) if decided else None,
        }
    return out


def backtest_symbol(symbol, horizon=20, step=5, start_after=200):
    highs, lows, closes, _ = risk_utils._load_ohlcv_series(symbol)
    if not closes:
        return {"symbol": symbol, "error": "no OHLCV data"}
    recs = backtest_series(highs, lows, closes, horizon=horizon, step=step,
                           start_after=start_after)
    agg = aggregate(recs)
    agg["symbol"] = symbol
    return agg


def _print_report(agg):
    sym = agg.get("symbol", "?")
    if agg.get("error"):
        print(f"{sym}: {agg['error']}")
        return
    print(f"\n=== {sym} — {agg['samples']} predictions (horizon per sample) ===")
    s = agg.get("support")
    if s:
        print(f"  SUPPORT (stop):  n={s['n']}  held={s['hold_rate']}%  "
              f"tested<=1%={s['tested_within_1pct']}%  median gap={s['median_gap_pct']}%")
    r = agg.get("resistance")
    if r:
        print(f"  RESISTANCE (tgt): n={r['n']}  hit={r['hit_rate']}%  median gap={r['median_gap_pct']}%")
    o = agg.get("outcome")
    if o:
        print(f"  OUTCOME: target-first={o['target_first']} stop-first={o['stop_first']} "
              f"neither={o['neither']}  ->  win-rate={o['win_rate']}%")


if __name__ == "__main__":
    args = sys.argv[1:]
    horizon, step = 20, 5
    if "--horizon" in args:
        horizon = int(args[args.index("--horizon") + 1])
    if "--step" in args:
        step = int(args[args.index("--step") + 1])
    syms = [a.upper() for a in args if not a.startswith("--") and not a.isdigit()]
    if not syms:
        print("usage: python backtest_levels.py SYMBOL [SYMBOL ...] [--horizon 20] [--step 5]")
        sys.exit(1)
    for sym in syms:
        _print_report(backtest_symbol(sym, horizon=horizon, step=step))
