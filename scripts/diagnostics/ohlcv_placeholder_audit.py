"""
OHLCV placeholder audit — READ-ONLY. Measures how many Chaikin close-only placeholder
bars (bar_provenance.is_provisional: `provisional` flag or volume 0) sit in
Data/Symbol_full/*_daily.json, and how much they distort the raw series.

Placeholders are written by powergauge._append_ohlcv_entry and are meant to be
overwritten by rapidapi._fetch_and_merge, which only replaces the 3 newest dates the API
returns. Anything the repair misses is "stranded": interior placeholders (a real bar
follows) and weekend-dated ones (the API never returns those dates). Consumers used to drop
only TRAILING placeholders, so stranded ones reached ATR and patterns. Consumers now filter
every placeholder (bar_provenance.real_dates), so the ATR ratio below measures the DATA
damage — what a consumer that trusts the raw series would see — not what live code sees.

Reports:
  - file freshness (newest bar / newest file mtime) — is this box's data current?
  - placeholder counts since --since: trailing / interior / weekend, by month
  - share of placeholders in each symbol's last 30 bars
  - ATR(14) on the raw series (trailing placeholders dropped, as consumers did before
    the stranded-placeholder fix) vs ATR(14) on real bars only (ratio < 1 = stranded
    placeholders shrink ATR; stop distance = multiple x ATR)
  - the symbols with the most distorted ATR
  - stop staleness: symbols whose stop falls back to 8% off price because the newest bar
    is older than risk_utils.STALE_STOP_DAYS — measured from the newest bar of any kind
    (before the stranded-placeholder fix) and from the newest REAL bar (after it). Run
    this before deploying that fix: "after" is how many stops would switch to 8%.

Writes nothing unless --json PATH is given (then only that file).

Usage (from the repo root, with the project venv):
    python scripts/diagnostics/ohlcv_placeholder_audit.py
    python scripts/diagnostics/ohlcv_placeholder_audit.py --data-dir D:\\path\\to\\Data --json audit.json
"""
import argparse
import datetime
import glob
import json
import os
import sys
from collections import Counter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)

import numpy as np

from aether.risk_utils import STALE_STOP_DAYS
from bar_provenance import is_provisional
from patterns import _wilder_atr, ohlcv_to_array

RECENT_BARS = 30


def _out(line: str = "") -> None:
    """Report line — intentional CLI output, unprefixed."""
    sys.stdout.write(line + "\n")


def raw_array(ts: dict, lookback: int = 60):
    """OHLCV array over the raw series, dropping only TRAILING placeholders — the view
    consumers had before they filtered every placeholder. Built here, NOT via
    patterns.ohlcv_to_array, which now filters all of them (and would make the
    raw-vs-real comparison trivially 1.0)."""
    dates = sorted(ts)
    while dates and is_provisional(ts[dates[-1]]):
        dates.pop()
    if len(dates) < 10:
        return None
    return np.array([[float(ts[d].get(k, 0) or 0) for k in
                      ("1. open", "2. high", "3. low", "4. close", "5. volume")]
                     for d in dates[-lookback:]])


def audit_series(ts: dict, since: str) -> dict:
    """Placeholder stats + live-vs-real ATR for one daily series."""
    dates = sorted(ts)
    trailing_from = len(dates)
    while trailing_from and is_provisional(ts[dates[trailing_from - 1]]):
        trailing_from -= 1
    out = {"bars": 0, "trailing": 0, "interior": 0, "weekend": 0, "by_month": Counter(),
           "recent_share": None, "atr_ratio": None, "last_date": dates[-1] if dates else None,
           "last_real": max((d for d in dates if not is_provisional(ts[d])), default=None)}
    for i, d in enumerate(dates):
        if d < since:
            continue
        out["bars"] += 1
        if not is_provisional(ts[d]):
            continue
        out["by_month"][d[:7]] += 1
        out["trailing" if i >= trailing_from else "interior"] += 1
        out["weekend"] += datetime.date.fromisoformat(d).weekday() >= 5
    if len(dates) >= RECENT_BARS:
        out["recent_share"] = sum(is_provisional(ts[d]) for d in dates[-RECENT_BARS:]) / RECENT_BARS
        live = raw_array(ts, lookback=60)
        real = ohlcv_to_array({d: ts[d] for d in dates if not is_provisional(ts[d])},
                              dates[-1], lookback=60)
        if live is not None and real is not None:
            a_live, a_real = _wilder_atr(live)[-1], _wilder_atr(real)[-1]
            if a_real > 0:
                out["atr_ratio"] = float(a_live / a_real)
    return out


def _pct(xs, q):
    return float(np.percentile(xs, q)) if xs else None


def _age(day: str | None, today: datetime.date) -> int | None:
    return (today - datetime.date.fromisoformat(day)).days if day else None


def stale_stops(per_sym: dict, today: datetime.date, limit: int = STALE_STOP_DAYS) -> dict:
    """Symbols whose stop falls back to 8% off price (newest bar older than `limit` days):
    by the newest bar of any kind (before) vs the newest real bar (after the fix)."""
    def stale(day):
        age = _age(day, today)
        return age is None or age > limit
    before = {s for s, v in per_sym.items() if stale(v["last_date"])}
    after = {s for s, v in per_sym.items() if stale(v["last_real"])}
    return {"limit_days": limit, "as_of": today.isoformat(), "before": len(before),
            "after": len(after), "newly_stale": sorted(after - before)}


def run(data_dir: str, since: str, months: int, top: int,
        today: datetime.date | None = None) -> dict:
    files = sorted(glob.glob(os.path.join(data_dir, "Symbol_full", "*_daily.json")))
    if not files:
        raise SystemExit(f"no *_daily.json under {os.path.join(data_dir, 'Symbol_full')}")
    per_sym, unreadable = {}, []
    newest_mtime = 0.0
    for f in files:
        sym = os.path.basename(f)[:-len("_daily.json")]
        newest_mtime = max(newest_mtime, os.path.getmtime(f))
        try:
            with open(f, encoding="utf-8") as fh:
                ts = json.load(fh).get("Time Series (Daily)") or {}
        except (OSError, json.JSONDecodeError):
            unreadable.append(sym)
            continue
        if ts:
            per_sym[sym] = audit_series(ts, since)

    tot = {k: sum(s[k] for s in per_sym.values()) for k in ("bars", "trailing", "interior", "weekend")}
    months_c = Counter()
    for s in per_sym.values():
        months_c.update(s["by_month"])
    placeholders = tot["trailing"] + tot["interior"]
    shares = [s["recent_share"] for s in per_sym.values() if s["recent_share"] is not None]
    ratios = [s["atr_ratio"] for s in per_sym.values() if s["atr_ratio"] is not None]
    last_dates = Counter(s["last_date"] for s in per_sym.values())
    # Worst = most distorted ATR (stranded placeholders), not delisted names whose series is
    # all trailing placeholders — consumers already drop those.
    worst = sorted(((k, v) for k, v in per_sym.items() if v["atr_ratio"] is not None),
                   key=lambda kv: kv[1]["atr_ratio"])[:top]

    _out(f"\nOHLCV placeholder audit — {os.path.join(data_dir, 'Symbol_full')}")
    _out(f"  run at {datetime.datetime.now():%Y-%m-%d %H:%M}, newest file mtime "
         f"{datetime.datetime.fromtimestamp(newest_mtime):%Y-%m-%d %H:%M}")
    _out(f"  symbols {len(per_sym)} (unreadable {len(unreadable)}); most common last bar: "
         + ", ".join(f"{d} x{n}" for d, n in last_dates.most_common(3)))
    _out(f"\n  bars since {since}: {tot['bars']}, placeholders {placeholders} "
         f"({placeholders / tot['bars']:.2%})" if tot["bars"] else f"\n  no bars since {since}")
    _out(f"    trailing {tot['trailing']}   interior (stranded) {tot['interior']}   "
         f"weekend-dated {tot['weekend']}   symbols with interior "
         f"{sum(1 for s in per_sym.values() if s['interior'])}")
    _out(f"  by month (last {months}): "
         + ", ".join(f"{m} {months_c[m]}" for m in sorted(months_c)[-months:]))
    if shares:
        _out(f"\n  placeholder share of last {RECENT_BARS} bars: median {_pct(shares, 50):.0%}, "
             f"p90 {_pct(shares, 90):.0%}")
    if ratios:
        _out(f"  ATR14 raw series / ATR14 real-bars-only: median {_pct(ratios, 50):.2f}, "
             f"p10 {_pct(ratios, 10):.2f}, p90 {_pct(ratios, 90):.2f}   (1.00 = no distortion)")
    st = stale_stops(per_sym, today or datetime.date.today())
    _out(f"\n  stops on the 8% fallback (newest bar > {st['limit_days']} d old, as of {st['as_of']}):")
    _out(f"    before the stranded-placeholder fix (any bar): {st['before']}   "
         f"after it (real bars only): {st['after']}   newly stale: {len(st['newly_stale'])}")
    if st["newly_stale"]:
        _out("    newly stale: " + ", ".join(st["newly_stale"][:25])
             + (" ..." if len(st["newly_stale"]) > 25 else ""))

    _out(f"\n  worst {top} by ATR distortion:")
    for sym, s in worst:
        ratio = "-" if s["atr_ratio"] is None else f"{s['atr_ratio']:.2f}"
        _out(f"    {sym:<8} recent {(s['recent_share'] or 0):>4.0%}  interior {s['interior']:>4}  "
             f"weekend {s['weekend']:>3}  ATR ratio {ratio}  last {s['last_date']}")

    return {
        "data_dir": data_dir, "since": since, "symbols": len(per_sym), "unreadable": unreadable,
        "newest_file_mtime": datetime.datetime.fromtimestamp(newest_mtime).isoformat(timespec="minutes"),
        "last_bar_dates": dict(last_dates.most_common(5)), "totals": tot,
        "placeholder_pct": round(placeholders / tot["bars"] * 100, 3) if tot["bars"] else None,
        "by_month": dict(sorted(months_c.items())),
        "recent_share": {"median": _pct(shares, 50), "p90": _pct(shares, 90)},
        "atr_ratio": {"median": _pct(ratios, 50), "p10": _pct(ratios, 10), "p90": _pct(ratios, 90)},
        "stale_stops": st,
        "worst": {sym: {k: v for k, v in s.items() if k != "by_month"} for sym, s in worst},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only OHLCV placeholder audit")
    ap.add_argument("--data-dir", default=os.path.join(BASE_DIR, "Data"))
    ap.add_argument("--since", default="2023-01-01")
    ap.add_argument("--months", type=int, default=8)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", default=None, help="also write the summary to this path")
    a = ap.parse_args()
    summary = run(a.data_dir, a.since, a.months, a.top)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1)
        _out(f"\n  wrote {a.json}")
