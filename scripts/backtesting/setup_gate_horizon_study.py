"""
Setup Gate at 20 and 60 Days — does requiring Setup = 1 cost us the runs we miss?

Setup (risk_utils.setup_ok) = price > SMA20 of the prior closes AND price > close 3 bars ago.
It is a hard entry gate: the AI game only buys rows with Setup = 1. R&D #46 found the
Setup x S10 conjunction TOO_STRICT at 10 days. A 2026-10-29 missed-winner scan (runs of
+40% in 60 days, 2023-2026) found Setup = 1 at only 10.6% of run starts vs 35.2% of all
stocks on the same dates. Both missed patterns - VLO-like trend leaders after a pullback,
and beaten-down rebounds - start below the 20-day average, where Setup is 0.

Question: among candidates that pass everything else (S10 >= 2.0 and S10+L60 >= 5.0, the
BALANCED minimum; scores rebuilt by backtest_ratings.compute_br), do Setup = 0 names do
better or worse than Setup = 1 names over 20 and 60 trading days? Risk is measured too:
the gate may be protecting against falling knives.

Decision rule (fixed before running):
    Primary   per date, mean(SETUP0) - mean(SETUP1) of the 60-day forward return
              (winsorized 1/99% over all eligible obs), on dates with both groups; Newey-West
              (HAC) t across dates, lag 59.
    SETUP_COSTS   diff > 0 AND HAC t >= 1.96 AND the 20-day diff (HAC lag 19) >= 0 AND the
                  SETUP0 20-day worst-drawdown mean is no more than 2 points deeper than
                  SETUP1's -> propose relaxing Setup (its own PR, gated on the portfolio
                  backtest with stops). Never wire from this study.
    SETUP_HELPS   diff < 0 AND HAC t <= -1.96 -> keep the gate.
    INCONCLUSIVE  otherwise -> keep the gate.
Secondary (reported, not decisive): 20-day diff; which half of Setup fails (below SMA20
only, down over 3 days only, both); per year; the same comparison over ALL stocks (not
only eligible); share of 60-day losses of 30% or more per group.

Not measured: stops, sizing, costs. Biases: today's universe; unadjusted closes (entries
skipped when snapshot price and OHLCV close differ by >10%, returns winsorized).

Usage:
    python scripts/backtesting/setup_gate_horizon_study.py [--min-year 2023] [--data-dir PATH] [--out FILE]
"""
import argparse
import datetime
import glob
import json
import multiprocessing
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import backtest_ratings as br
import setup_s10_gate_study as sg

FWD = (20, 60)
S10_FLOOR = 2.0
MIN_COMBINED = 5.0
GATE_T = 1.96
MAX_EXTRA_DRAWDOWN = 2.0
WINSOR = (0.01, 0.99)


def setup_parts(price, idx, all_dates, ts, sma_period=20, dir_days=3):
    """(above_sma, up_over_dir_days), the two halves of risk_utils.setup_ok."""
    sma_w = all_dates[max(0, idx - sma_period): idx]
    above = (len(sma_w) >= sma_period
             and price > sum(float(ts[d]["4. close"]) for d in sma_w) / len(sma_w))
    up = idx >= dir_days and price > float(ts[all_dates[idx - dir_days]]["4. close"])
    return above, up


def eligible(s10, l60) -> bool:
    """Passes everything but Setup: the S10 floor and the BALANCED combined minimum."""
    return s10 >= S10_FLOOR and s10 + l60 >= MIN_COMBINED


def forward_stats(price, closes_after):
    """(fwd20 %, fwd60 % or None, worst20 %) from the closes after the entry day."""
    f20 = (closes_after[FWD[0] - 1] - price) / price * 100
    f60 = (closes_after[FWD[1] - 1] - price) / price * 100 if len(closes_after) >= FWD[1] else None
    worst20 = (min(closes_after[:FWD[0]]) - price) / price * 100
    return f20, f60, worst20


def _symbol_obs(job) -> list:
    """(date, eligible, setup, above_sma, up3, fwd20, fwd60|None, worst20) per obs.
    worst20 = lowest close over the next 20 sessions vs entry, in % (<= 0 when it dipped)."""
    sym, sym_dir, ohlcv_dir, min_year = job
    ts = sg._load(ohlcv_dir, sym)
    if not ts:
        return []
    dates = sorted(ts)
    pos = {d: i for i, d in enumerate(dates)}
    seas = br.precompute_seasonality(ts)
    rows, prev = [], None
    for path in sorted(glob.glob(os.path.join(sym_dir, sym, f"{sym}_*.json"))):
        day = Path(path).stem[len(sym) + 1:]
        if day < str(min_year) or day not in pos:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            prev = None
            continue
        if data.get("status") == "invalid symbol":
            prev = data
            continue
        meta = (data.get("metaInfo") or [{}])[0]
        cl = data.get("checklist_stocks") or {}
        try:
            price = float(meta.get("Last") or cl.get("lastPrice") or 0)
        except (TypeError, ValueError):
            price = 0.0
        i = pos[day]
        close = float(ts[day].get("4. close", 0))
        if price <= 0 or i < 20 or i + FWD[0] >= len(dates) or (close > 0 and abs(price - close) / close > 0.1):
            prev = data
            continue
        res = br.compute_br(data, prev, price, i, dates, ts, seas)
        s10, l60 = res[1], res[2]
        setup = bool(br.compute_setup_ok(price, i, dates, ts))
        above, up = setup_parts(price, i, dates, ts)
        c = [float(ts[dates[k]]["4. close"]) for k in range(i + 1, min(i + FWD[1] + 1, len(dates)))]
        f20, f60, worst20 = forward_stats(price, c)
        rows.append((day, eligible(s10, l60), setup, above, up, f20, f60, worst20))
        prev = data
    return rows


def observations(sym_dir, ohlcv_dir, min_year, workers=1):
    syms = sorted({Path(p).name.replace("_daily.json", "") for p in glob.glob(os.path.join(ohlcv_dir, "*_daily.json"))}
                  & {d for d in os.listdir(sym_dir) if os.path.isdir(os.path.join(sym_dir, d))})
    out = []
    with multiprocessing.Pool(max(1, workers)) as pool:
        for n, rows in enumerate(pool.imap_unordered(_symbol_obs, [(s, sym_dir, ohlcv_dir, min_year) for s in syms],
                                                     chunksize=4), 1):
            out.extend(rows)
            if n % 100 == 0 or n == len(syms):
                sys.stderr.write(f"  {n}/{len(syms)} symbols, {len(out)} obs\n")
    return out


def _quantile(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(q * (len(s) - 1))))] if s else None


def group_of(o) -> str:
    if o[2]:
        return "SETUP1"
    if not o[3] and not o[4]:
        return "FAIL_BOTH"
    return "FAIL_SMA" if not o[3] else "FAIL_DIR"


def per_day(obs, k, bounds, eligible_only=True, year=None) -> dict:
    """{date: {group: [returns]}} with SETUP0 = every Setup-0 obs, plus its FAIL_* parts."""
    out = {}
    for o in obs:
        if (eligible_only and not o[1]) or (year and not o[0].startswith(year)) or o[k] is None:
            continue
        r = max(bounds[0], min(bounds[1], o[k]))
        g = out.setdefault(o[0], {})
        name = group_of(o)
        g.setdefault(name, []).append(r)
        if name != "SETUP1":
            g.setdefault("SETUP0", []).append(r)
    return out


def clustered(pd_, a, b, lag) -> dict:
    days = sorted(d for d, g in pd_.items() if g.get(a) and g.get(b))
    diffs = [sg._mean(pd_[d][a]) - sg._mean(pd_[d][b]) for d in days]
    t_hac, t = sg._t_hac(diffs, lag), sg._t_one_sample(diffs)
    return {"days": len(diffs), "mean_diff": round(sg._mean(diffs), 3) if diffs else None,
            "t": round(t, 2) if t is not None else None, "t_hac": round(t_hac, 2) if t_hac is not None else None}


def verdict(p60: dict, p20: dict, worst: dict) -> str:
    m, t = p60.get("mean_diff"), p60.get("t_hac")
    if m is None or t is None:
        return "INCONCLUSIVE"
    if (m > 0 and t >= GATE_T and (p20.get("mean_diff") or 0) >= 0
            and worst.get("SETUP0", 0) >= worst.get("SETUP1", 0) - MAX_EXTRA_DRAWDOWN):
        return "SETUP_COSTS"
    if m < 0 and t <= -GATE_T:
        return "SETUP_HELPS"
    return "INCONCLUSIVE"


def group_stats(obs, eligible_only=True) -> dict:
    out = {}
    for name in ("SETUP1", "SETUP0", "FAIL_SMA", "FAIL_DIR", "FAIL_BOTH"):
        rs = [o for o in obs if (o[1] or not eligible_only)
              and (group_of(o) == name or (name == "SETUP0" and not o[2]))]
        f60 = [o[6] for o in rs if o[6] is not None]
        out[name] = {"n": len(rs), "median20": round(_quantile([o[5] for o in rs], 0.5), 2) if rs else None,
                     "median60": round(_quantile(f60, 0.5), 2) if f60 else None,
                     "worst20_mean": round(sg._mean([o[7] for o in rs]), 2) if rs else None,
                     "loss30_60d": round(sum(x <= -30 for x in f60) / len(f60), 4) if f60 else None}
    return out


def summarize(obs) -> dict:
    elig = [o for o in obs if o[1]]
    rep = {"counts": {"obs": len(obs), "eligible": len(elig), "eligible_setup1": sum(o[2] for o in elig)},
           "groups_eligible": group_stats(obs), "groups_all": group_stats(obs, eligible_only=False)}
    for k, w in ((5, 20), (6, 60)):
        xs = [o[k] for o in elig if o[k] is not None]
        b = (_quantile(xs, WINSOR[0]), _quantile(xs, WINSOR[1]))
        xa = [o[k] for o in obs if o[k] is not None]
        ba = (_quantile(xa, WINSOR[0]), _quantile(xa, WINSOR[1]))
        pd_ = per_day(obs, k, b)
        rep[f"fwd{w}"] = {
            "bounds": [round(b[0], 2), round(b[1], 2)],
            "SETUP0-SETUP1": clustered(pd_, "SETUP0", "SETUP1", w - 1),
            "FAIL_SMA-SETUP1": clustered(pd_, "FAIL_SMA", "SETUP1", w - 1),
            "FAIL_DIR-SETUP1": clustered(pd_, "FAIL_DIR", "SETUP1", w - 1),
            "FAIL_BOTH-SETUP1": clustered(pd_, "FAIL_BOTH", "SETUP1", w - 1),
            "ALL_SETUP0-SETUP1": clustered(per_day(obs, k, ba, eligible_only=False), "SETUP0", "SETUP1", w - 1),
        }
    xs = [o[6] for o in elig if o[6] is not None]
    b = (_quantile(xs, WINSOR[0]), _quantile(xs, WINSOR[1]))
    rep["fwd60_by_year"] = {y: clustered(per_day(obs, 6, b, year=y), "SETUP0", "SETUP1", 59)
                            for y in sorted({o[0][:4] for o in elig})}
    worst = {g: v["worst20_mean"] for g, v in rep["groups_eligible"].items()}
    rep["verdict"] = verdict(rep["fwd60"]["SETUP0-SETUP1"], rep["fwd20"]["SETUP0-SETUP1"], worst)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.dirname(_HERE)), "Data"))
    ap.add_argument("--out", default=None,
                    help="report path (default: <data-dir>/setup_gate_horizon_<run date>.json)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = ap.parse_args()
    obs = observations(os.path.join(args.data_dir, "Symbol"), os.path.join(args.data_dir, "Symbol_full"),
                       args.min_year, args.workers)
    mismatch = sum(o[2] != (o[3] and o[4]) for o in obs)
    rep = summarize(obs)
    rep["meta"] = {"min_year": args.min_year, "run_date": str(datetime.date.today()),
                   "first_obs": min(o[0] for o in obs), "last_obs": max(o[0] for o in obs),
                   "setup_parts_mismatch": mismatch}
    sg._out(json.dumps(rep, indent=1))
    out = args.out or os.path.join(args.data_dir, f"setup_gate_horizon_{rep['meta']['run_date']}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    sg._out(f"report -> {out}")


if __name__ == "__main__":
    main()
