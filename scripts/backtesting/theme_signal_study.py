"""
Theme-watch signal study (R&D #51): do the watchlist signals predict returns?

On month-end dates, rebuilds each signal point-in-time (SEC facts must have been
FILED by the date; OHLCV bars at or before the date) for every universe stock with
SEC data, using the same aether.ai_buildout.build_row / watch_score the live scan
uses. Then measures forward 20d and 60d returns in excess of SPY.

Comparisons (each as a per-date spread series, tested with a Newey-West t):
  - watch_score buckets: high (>= 3) vs low (<= 0)
  - each signal alone: revenue YoY, RPO YoY, 8-K 1.01, RS 60d, CMF 20
  - theme members vs the rest of the universe (ai_buildout, robot_vision)

Gate per comparison: |HAC t| >= 1.96 and >= 30 observations in each side.

Known biases, reported with the result:
  - survivorship: the universe is today's symbol list
  - theme membership is today's classification, applied to the past
  - 8-K counts only where the SEC 'recent' filings list reaches back far enough

    python scripts/backtesting/theme_signal_study.py              # 2018-01 .. 6 months ago
    python scripts/backtesting/theme_signal_study.py --start 2020-01 --no-fetch

Writes Data/theme_signal_study.json. Read-only over OHLCV; SEC data is cached in
Data/edgar_cache/ (history cached 30 days, so re-runs do not refetch).
"""
import argparse
import bisect
import datetime
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from aether import ai_buildout as ab
from aether import instruments, paths
from aether_logger import get_logger as _get_logger
from scripts.backtesting.setup_s10_gate_study import _t_hac

_log = _get_logger("theme_signal_study")

HORIZONS = (20, 60)
HISTORY_TTL_HOURS = 24 * 30
MIN_PER_SIDE_PER_DATE = 5
GATE_T = 1.96
GATE_N = 30
# Forward returns are winsorized at these pooled percentiles per horizon. The OHLCV
# cache is not split-adjusted, so a split shows up as a +1000% "return" (WW, DAVE)
# and would dominate any mean.
WINSOR_PCT = (0.01, 0.99)

# (name, row key, "high" test, "low" test). Stale / suspect figures are excluded
# the same way watch_score excludes them.
SIGNALS = [
    ("watch_score", "watch_score", lambda v: v >= 3, lambda v: v <= 0),
    ("revenue_yoy", "revenue_yoy", lambda v: v >= 25, lambda v: v < 0),
    ("rpo_yoy", "rpo_yoy", lambda v: v >= 25, lambda v: v < 0),
    ("agreements_90d", "agreements_90d", lambda v: v >= 1, lambda v: v == 0),
    ("rs_60d", "rs_60d", lambda v: v >= 10, lambda v: v < 0),
    ("cmf_20", "cmf_20", lambda v: v >= 0.05, lambda v: v <= -0.05),
]


def month_end_dates(start, end):
    """Last calendar day of each month from start (YYYY-MM) to end (YYYY-MM)."""
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
        out.append((nxt - datetime.timedelta(days=1)).isoformat())
        y, m = nxt.year, nxt.month
    return out


def fwd_excess(bars, dates, spy_close, i, h):
    """Return over the next h real bars from index i, minus SPY over the same dates."""
    if i + h >= len(bars):
        return None
    d0, d1 = dates[i], dates[i + h]
    if d0 not in spy_close or d1 not in spy_close:
        return None
    r = bars[i + h][3] / bars[i][3] - 1
    spy = spy_close[d1] / spy_close[d0] - 1
    return (r - spy) * 100


def agreements_covered(sub, as_of):
    """The SEC 'recent' list holds the latest ~1000 filings; only count 8-Ks for a date
    if that list reaches back past the 90-day window."""
    dates = (sub or {}).get("filings", {}).get("recent", {}).get("filingDate", [])
    if not dates:
        return False
    start = (datetime.date.fromisoformat(as_of) - datetime.timedelta(days=90)).isoformat()
    return min(dates) <= start


def build_observations(symbols, cik_map, rebal_dates, fetch=True, skipped=None):
    """One record per (symbol, date): theme flags, signal row, forward excess returns.
    Skipped symbols are recorded in `skipped` (reason -> [symbols]) when given."""
    skipped = {} if skipped is None else skipped
    spy_bars = ab._real_bars(ab._load_ohlcv("SPY") or {}, "9999-12-31")
    spy_dates = [b[0] for b in spy_bars]
    spy_close = {b[0]: b[3] for b in spy_bars}
    themes = {name: t for name, t in ab.THEMES.items()}
    obs = []
    for n, sym in enumerate(symbols, 1):
        if instruments.is_excluded(sym):
            skipped.setdefault("leveraged_inverse_crypto", []).append(sym)
            continue
        cik = cik_map.get(sym)
        ts = ab._load_ohlcv(sym)
        if not cik or not ts:
            skipped.setdefault("no_cik" if not cik else "no_ohlcv", []).append(sym)
            continue
        if fetch:
            sub = ab.submissions(cik, ttl_hours=HISTORY_TTL_HOURS)
            facts = ab.company_facts(cik, ttl_hours=HISTORY_TTL_HOURS)
        else:
            sub = ab._read_cache(f"sub_{cik}.json")
            facts = ab._read_cache(f"facts_{cik}.json")
        if not sub or not facts or not sub.get("sic"):
            # no SIC = fund / ETF; missing sub or facts = no XBRL or fetch failed
            reason = ("no_submissions" if not sub else "fund_no_sic" if not sub.get("sic")
                      else "no_facts")
            skipped.setdefault(reason, []).append(sym)
            continue
        bars = ab._real_bars(ts, "9999-12-31")
        dates = [b[0] for b in bars]
        member = {name: ab.bucket_for(sym, sub.get("sic"), name) for name in themes}
        for d in rebal_dates:
            i = bisect.bisect_right(dates, d) - 1
            if i < 60:
                continue
            j = bisect.bisect_right(spy_dates, d) - 1
            row = ab.build_row(sym, None, sub, facts, None, spy_bars[max(0, j - 61):j + 1],
                               dates[i], bars=bars[max(0, i - 61):i + 1])
            if not agreements_covered(sub, dates[i]):
                row["agreements_90d"] = None
            rec = {"symbol": sym, "date": d, "row": row,
                   "theme": {k: v is not None for k, v in member.items()}}
            for h in HORIZONS:
                rec[f"fwd{h}"] = fwd_excess(bars, dates, spy_close, i, h)
            obs.append(rec)
        if n % 50 == 0:
            _log.console(f"[study] {n}/{len(symbols)} symbols, {len(obs)} observations")
    return obs


def _usable(row, key):
    v = row.get(key)
    if v is None or key in (row.get("stale") or ()):
        return None
    if key == "rpo_yoy" and abs(v) > ab.RPO_SUSPECT_PCT:
        return None
    return v


def spread_test(obs, side_of, h):
    """side_of(rec) -> 'high' / 'low' / None. Per-date mean(high) - mean(low) series
    (dates with >= MIN_PER_SIDE_PER_DATE on each side), Newey-West t over dates."""
    by_date = {}
    n_hi = n_lo = 0
    sum_hi = sum_lo = 0.0
    for rec in obs:
        f = rec.get(f"fwd{h}")
        side = side_of(rec) if f is not None else None
        if side is None:
            continue
        by_date.setdefault(rec["date"], {"high": [], "low": []})[side].append(f)
    series = []
    for d in sorted(by_date):
        hi, lo = by_date[d]["high"], by_date[d]["low"]
        if len(hi) >= MIN_PER_SIDE_PER_DATE and len(lo) >= MIN_PER_SIDE_PER_DATE:
            series.append(sum(hi) / len(hi) - sum(lo) / len(lo))
            n_hi += len(hi)
            n_lo += len(lo)
            sum_hi += sum(hi)
            sum_lo += sum(lo)
    lag = max(0, math.ceil(h / 21) - 1)
    t = _t_hac(series, lag) if series else None
    res = {
        "dates": len(series), "n_high": n_hi, "n_low": n_lo,
        "mean_high": round(sum_hi / n_hi, 3) if n_hi else None,
        "mean_low": round(sum_lo / n_lo, 3) if n_lo else None,
        "spread": round(sum(series) / len(series), 3) if series else None,
        "hac_t": round(t, 2) if t is not None else None, "hac_lag": lag,
    }
    res["passes"] = bool(t is not None and abs(t) >= GATE_T
                         and n_hi >= GATE_N and n_lo >= GATE_N)
    return res


def winsorize(obs, pct=WINSOR_PCT):
    """Clip each horizon's forward returns to its pooled percentiles, in place.
    Returns {horizon: (low, high, n_clipped)}."""
    bounds = {}
    for h in HORIZONS:
        key = f"fwd{h}"
        vals = sorted(o[key] for o in obs if o[key] is not None)
        if not vals:
            continue
        lo = vals[int(pct[0] * (len(vals) - 1))]
        hi = vals[int(pct[1] * (len(vals) - 1))]
        clipped = 0
        for o in obs:
            v = o[key]
            if v is not None and (v < lo or v > hi):
                o[key] = min(max(v, lo), hi)
                clipped += 1
        bounds[f"{h}d"] = (round(lo, 2), round(hi, 2), clipped)
    return bounds


def analyze(obs):
    out = {"signals": {}, "themes": {}, "within_ai_buildout": {}}
    for name, key, hi, lo in SIGNALS:
        def side(rec, key=key, hi=hi, lo=lo):
            v = _usable(rec["row"], key)
            return None if v is None else "high" if hi(v) else "low" if lo(v) else None
        out["signals"][name] = {f"{h}d": spread_test(obs, side, h) for h in HORIZONS}
    for theme in ab.THEMES:
        def side(rec, theme=theme):
            return "high" if rec["theme"][theme] else "low"
        out["themes"][theme] = {f"{h}d": spread_test(obs, side, h) for h in HORIZONS}
    members = [r for r in obs if r["theme"]["ai_buildout"]]
    def side_ws(rec):
        v = rec["row"]["watch_score"]
        return "high" if v >= 3 else "low" if v <= 0 else None
    out["within_ai_buildout"]["watch_score"] = {
        f"{h}d": spread_test(members, side_ws, h) for h in HORIZONS}
    return out


def main():
    today = datetime.date.today()
    six_months_ago = (today.replace(day=1) - datetime.timedelta(days=183)).strftime("%Y-%m")
    ap = argparse.ArgumentParser(description="Theme-watch signal study (R&D #51)")
    ap.add_argument("--start", default="2018-01", help="first month YYYY-MM")
    ap.add_argument("--end", default=six_months_ago, help="last month YYYY-MM (needs 60d of bars after)")
    ap.add_argument("--no-fetch", action="store_true", help="use only cached SEC data")
    args = ap.parse_args()

    _log.console(f"[study] system date {today}; months {args.start}..{args.end}")
    cik_map = ab.ticker_cik_map()
    if not cik_map:
        raise SystemExit("SEC ticker map unavailable.")
    symbols = sorted(set(ab.load_universe()) | set().union(*(t["seed"] for t in ab.THEMES.values())))
    rebal = month_end_dates(args.start, args.end)
    skipped = {}
    obs = build_observations(symbols, cik_map, rebal, fetch=not args.no_fetch, skipped=skipped)
    _log.console("[study] skipped: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(skipped.items())))
    _log.console(f"[study] {len(obs)} observations, "
                 f"{len({o['symbol'] for o in obs})} symbols, {len(rebal)} month-ends")
    bounds = winsorize(obs)
    _log.console(f"[study] winsorized fwd returns (low, high, clipped): {bounds}")
    res = analyze(obs)
    res.update({
        "generated": today.isoformat(), "start": args.start, "end": args.end,
        "observations": len(obs), "symbols": len({o["symbol"] for o in obs}),
        "theme_members": {t: sorted({o["symbol"] for o in obs if o["theme"][t]})
                          for t in ab.THEMES},
        "skipped": {k: sorted(v) for k, v in skipped.items()},
        "winsor_bounds": bounds,
        "gate": {"abs_hac_t": GATE_T, "min_n_per_side": GATE_N},
        "biases": ["survivorship (today's universe)",
                   "unadjusted splits in the OHLCV cache (limited by winsorizing)",
                   "theme membership is today's classification",
                   "8-K counts only where SEC 'recent' filings reach back 90 days"],
    })
    out = Path(paths.data_dir()) / "theme_signal_study.json"
    out.write_text(json.dumps(res, indent=2), encoding="utf-8")

    for group in ("signals", "themes", "within_ai_buildout"):
        _log.console(f"[study] --- {group} ---")
        for name, by_h in res[group].items():
            for h, r in by_h.items():
                _log.console(
                    f"  {name:<15} {h:>3}  spread={r['spread']}  t={r['hac_t']}  "
                    f"hi={r['mean_high']} (n={r['n_high']})  lo={r['mean_low']} (n={r['n_low']})  "
                    f"dates={r['dates']}  {'PASS' if r['passes'] else 'fail'}")
    _log.console(f"[study] wrote {out}")


if __name__ == "__main__":
    main()
