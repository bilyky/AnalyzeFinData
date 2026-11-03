"""
Trend-Leader Study — do Chaikin "all-Strong" stocks beat the rest, and does our gate stack miss them?

PROD 2026-10: VLO went from ~$240 (06-17) to $444 (10-15) while Chaikin rated it Very Bullish
with Strong long-term trend, money flow and relative strength almost every day. It never made
our daily top-5: Short10 / Long60 penalise or don't reward strong trend and strong industry
(recovery plays score higher), the R/R gate's target is the 10-day high (a stock at new highs
has ~0 upside to show), and the S10 momentum floor of 2.0 cut it on most October days.

One stock is not evidence. This study asks, point-in-time over the cached snapshots:

    LEADER   corrected PGR == 5 (the project's extract_pgr_corr) AND ltTrend, moneyFlow and
             relativeStrength all "Strong" (Chaikin checklist, same-day snapshot)
    REST     every other valid observation on the same date
    ADMITTED Setup = 1 AND S10 >= 2.0 AND S10+L60 >= 5.0 AND R/R >= 2.0 — an approximation of
             today's buy gate stack (scores rebuilt by backtest_ratings.compute_br)

Outcome: forward close-to-close return over 20 and 60 trading days from the snapshot date's
close (the snapshot is taken before the open, from the prior close, so entry at that day's close
uses no future data). Closes are not split-adjusted; the entry is skipped when the snapshot
price and the OHLCV close disagree by >10% (as in the other studies), and returns are
winsorized at the 1st/99th percentile of all observations before averaging.

Decision rule (fixed before running):
    Primary   LEADER minus REST, 20-day, winsorized. Per date: mean(LEADER) - mean(REST), on
              dates where both exist; then a Newey-West (HAC) t across dates, lag 19 (daily
              samples of a 20-day return).
    LEADERS_BEAT  mean diff > 0 AND HAC t >= 1.96 AND the 60-day mean diff (HAC lag 59) > 0
                  -> propose a separate trend-leader entry path (its own target/stop), as its
                     own PR gated on a portfolio backtest. Never wire directly from this study.
    LEADERS_LAG   mean diff < 0 AND HAC t <= -1.96 -> the contrarian scoring is supported;
                  missing VLO-like names is the cost of a rule that pays on average.
    INCONCLUSIVE  otherwise -> no change.
Secondary (reported, not decisive; several tests, so one pass at |t| ~ 2 is weak evidence):
    LEADER_AT_HIGH minus REST (leaders within 2% of their 20-day closing high, where the R/R
    gate bites); LEADER minus ADMITTED; per-year primary; raw (unwinsorized) primary; median
    of per-date diffs.

Not measured: stops, position sizing, costs, slippage, drawdowns. Biases: today's universe
(names that left it are missing), today's Chaikin field definitions.

Usage:
    python scripts/backtesting/trend_leader_study.py [--min-year 2023] [--data-dir PATH] [--out FILE]
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
MIN_RR = 2.0
AT_HIGH = 0.98
GATE_T = 1.96
WINSOR = (0.01, 0.99)


def is_leader(data: dict) -> bool:
    cl = data.get("checklist_stocks") or {}
    return (br.extract_pgr_corr(data) == 5
            and all(str(cl.get(k) or "").strip() == "Strong"
                    for k in ("ltTrend", "moneyFlow", "relativeStrength")))


def _rr(price, idx, all_dates, ts) -> float:
    """R/R as the workbook computes it (backtest_ratings.compute_br): 10-day high target,
    local-low stop."""
    stop_w = all_dates[max(0, idx - br.STOP_DAYS): idx]
    low = min((float(ts[d].get("3. low", 0)) for d in stop_w), default=0)
    stop = round(low * 0.99, 2) if low else 0
    if stop >= price:
        stop = 0
    tgt_w = all_dates[max(0, idx - br.TARGET_LOOKBACK): idx]
    res = max((float(ts[d].get("2. high", 0)) for d in tgt_w), default=0)
    target = res if res > price else 0
    return round((target - price) / (price - stop), 2) if stop > 0 and target > 0 else 0.0


def _symbol_obs(job) -> list:
    """(date, leader, at_high, admitted, fwd20, fwd60|None) for one symbol."""
    sym, sym_dir, ohlcv_dir, min_year = job
    ts = sg._load(ohlcv_dir, sym)
    if not ts:
        return []
    all_dates = sorted(ts)
    pos = {d: i for i, d in enumerate(all_dates)}
    seasonality = br.precompute_seasonality(ts)
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
        idx = pos[day]
        close = float(ts[day].get("4. close", 0))
        if (price <= 0 or idx < 20 or idx + FWD[0] >= len(all_dates)
                or (close > 0 and abs(price - close) / close > 0.1)):
            prev = data
            continue
        res = br.compute_br(data, prev, price, idx, all_dates, ts, seasonality)
        s10, l60 = res[1], res[2]
        admitted = (br.compute_setup_ok(price, idx, all_dates, ts) and s10 >= S10_FLOOR
                    and s10 + l60 >= MIN_COMBINED and _rr(price, idx, all_dates, ts) >= MIN_RR)
        hi20 = max(float(ts[d]["4. close"]) for d in all_dates[idx - 20: idx])
        fwd = [(float(ts[all_dates[idx + w]]["4. close"]) - price) / price * 100
               if idx + w < len(all_dates) else None for w in FWD]
        rows.append((day, is_leader(data), price >= AT_HIGH * hi20, admitted, fwd[0], fwd[1]))
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


def clip(x, lo, hi):
    return None if x is None else max(lo, min(hi, x))


def clustered(per_day: dict, a: str, b: str, lag: int) -> dict:
    """Per date mean(a) - mean(b) where both exist; HAC t across dates."""
    days = sorted(d for d, g in per_day.items() if g.get(a) and g.get(b))
    diffs = [sg._mean(per_day[d][a]) - sg._mean(per_day[d][b]) for d in days]
    t_hac = sg._t_hac(diffs, lag)
    t = sg._t_one_sample(diffs)
    return {"days": len(diffs),
            "mean_diff": round(sg._mean(diffs), 3) if diffs else None,
            "median_diff": round(_quantile(diffs, 0.5), 3) if diffs else None,
            "t": round(t, 2) if t is not None else None,
            "t_hac": round(t_hac, 2) if t_hac is not None else None}


def verdict(primary20: dict, primary60: dict) -> str:
    m, t = primary20.get("mean_diff"), primary20.get("t_hac")
    if m is None or t is None:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T and (primary60.get("mean_diff") or 0) > 0:
        return "LEADERS_BEAT"
    if m < 0 and t <= -GATE_T:
        return "LEADERS_LAG"
    return "INCONCLUSIVE"


def per_day_groups(obs, k: int, bounds=None, year=None) -> dict:
    """{date: {"LEADER": [...], "REST": [...], "LEADER_AT_HIGH": [...], "ADMITTED": [...]}}
    for fwd column k (4 = 20d, 5 = 60d), optionally winsorized to `bounds`."""
    out = {}
    for o in obs:
        if year and not o[0].startswith(str(year)):
            continue
        r = o[k]
        if r is None:
            continue
        if bounds:
            r = clip(r, *bounds)
        g = out.setdefault(o[0], {})
        g.setdefault("LEADER" if o[1] else "REST", []).append(r)
        if o[1] and o[2]:
            g.setdefault("LEADER_AT_HIGH", []).append(r)
        if o[3]:
            g.setdefault("ADMITTED", []).append(r)
    return out


def summarize(obs) -> dict:
    rep = {"counts": {"obs": len(obs), "leader_obs": sum(o[1] for o in obs),
                      "leader_at_high_obs": sum(o[1] and o[2] for o in obs),
                      "admitted_obs": sum(o[3] for o in obs),
                      "leaders_admitted": sum(o[1] and o[3] for o in obs)}}
    for k, w in ((4, 20), (5, 60)):
        xs = [o[k] for o in obs if o[k] is not None]
        b = (_quantile(xs, WINSOR[0]), _quantile(xs, WINSOR[1]))
        rep[f"fwd{w}_dist"] = {"n": len(xs), "mean": round(sg._mean(xs), 3), "median": round(_quantile(xs, 0.5), 3),
                              "min": round(min(xs), 1), "max": round(max(xs), 1),
                              "winsor_bounds": [round(b[0], 2), round(b[1], 2)]}
        pdw, pdr = per_day_groups(obs, k, b), per_day_groups(obs, k)
        lag = w - 1
        rep[f"fwd{w}"] = {
            "LEADER-REST": clustered(pdw, "LEADER", "REST", lag),
            "LEADER-REST_raw": clustered(pdr, "LEADER", "REST", lag),
            "LEADER_AT_HIGH-REST": clustered(pdw, "LEADER_AT_HIGH", "REST", lag),
            "LEADER-ADMITTED": clustered(pdw, "LEADER", "ADMITTED", lag),
            "ADMITTED-REST": clustered(pdw, "ADMITTED", "REST", lag),
        }
        if w == 20:
            years = sorted({o[0][:4] for o in obs})
            rep["fwd20_by_year"] = {y: clustered(per_day_groups(obs, 4, b, y), "LEADER", "REST", lag) for y in years}
    rep["verdict"] = verdict(rep["fwd20"]["LEADER-REST"], rep["fwd60"]["LEADER-REST"])
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.dirname(_HERE)), "Data"))
    ap.add_argument("--out", default=None,
                    help="report path (default: <data-dir>/trend_leader_<run date>.json)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = ap.parse_args()
    obs = observations(os.path.join(args.data_dir, "Symbol"), os.path.join(args.data_dir, "Symbol_full"),
                       args.min_year, args.workers)
    rep = summarize(obs)
    rep["meta"] = {"min_year": args.min_year, "run_date": str(datetime.date.today()),
                   "first_obs": min(o[0] for o in obs), "last_obs": max(o[0] for o in obs)}
    sg._out(json.dumps(rep, indent=1))
    out = args.out or os.path.join(args.data_dir, f"trend_leader_{rep['meta']['run_date']}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    sg._out(f"report -> {out}")


if __name__ == "__main__":
    main()
