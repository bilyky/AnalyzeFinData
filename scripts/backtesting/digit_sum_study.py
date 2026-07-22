"""
Digit-Sum Numerology Study — regenerates Data/digit_sum_study.json.

Tests whether the digit-sum of the open price predicts same-day direction,
and whether the digit-sum of the close predicts next-day direction.
Only signals with |z| >= 2.0 (95%+ confidence, N >= 50) are written.

Run monthly as new OHLCV data accumulates:
    python scripts/backtesting/digit_sum_study.py

Takes ~30 seconds for 500 symbols.
"""

import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DATA     = Path(__file__).resolve().parent.parent.parent / "Data"
OHLCV    = DATA / "Symbol_full"
OUT_FILE = DATA / "digit_sum_study.json"

MIN_N     = 50    # minimum observations per digit bucket
MIN_ABS_Z = 2.0  # 95%+ confidence threshold for inclusion
MIN_WIN_N = 30   # minimum N per 2-year window for temporal check

_WINDOWS = [("14-16","2014-01-01","2015-12-31"), ("16-18","2016-01-01","2017-12-31"),
            ("18-20","2018-01-01","2019-12-31"), ("20-22","2020-01-01","2021-12-31"),
            ("22-24","2022-01-01","2023-12-31"), ("24-26","2024-01-01","2026-12-31")]


def _digit_sum(price: float) -> int:
    s = str(int(abs(price)))
    while len(s) > 1:
        s = str(sum(int(c) for c in s))
    return int(s)


def _z(ups: int, n: int, base: float) -> float:
    if n < MIN_N:
        return 0.0
    se = math.sqrt(base * (1 - base) / n)
    return ((ups / n) - base) / se if se > 0 else 0.0


def analyze(path: Path) -> list[dict]:
    sym = path.stem.replace("_daily", "")
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return []

    dates = sorted(ts.keys())
    if len(dates) < 100:
        return []

    open_b  = defaultdict(lambda: [0, 0])
    close_b = defaultdict(lambda: [0, 0])

    for i, date in enumerate(dates):
        d = ts[date]
        try:
            op = float(d["1. open"])
            cl = float(d["4. close"])
        except (KeyError, ValueError):
            continue

        dg = _digit_sum(op)
        open_b[dg][0] += 1 if cl > op else 0
        open_b[dg][1] += 1

        if i + 1 < len(dates):
            dn = ts[dates[i + 1]]
            try:
                on = float(dn["1. open"])
                cn = float(dn["4. close"])
            except (KeyError, ValueError):
                continue
            dg2 = _digit_sum(cl)
            close_b[dg2][0] += 1 if cn > on else 0
            close_b[dg2][1] += 1

    tn = sum(v[1] for v in open_b.values())
    base_o = sum(v[0] for v in open_b.values()) / tn if tn else 0.5
    tn2 = sum(v[1] for v in close_b.values())
    base_c = sum(v[0] for v in close_b.values()) / tn2 if tn2 else 0.5

    rows = []
    for dg in range(10):
        for typ, bkt, base in [("OPEN", open_b, base_o), ("CLOSE", close_b, base_c)]:
            ups, n = bkt.get(dg, [0, 0])
            if n < MIN_N:
                continue
            z = _z(ups, n, base)
            rows.append({
                "symbol": sym, "type": typ, "digit": dg,
                "up_pct": round(ups / n, 4),
                "base":   round(base, 4),
                "n":      n,
                "z":      round(z, 3),
            })
    return rows


def _temporal_quality(sym: str, dg: int, overall_z: float, ts: dict) -> str:
    """Classify signal as consistent/partial/stale across 2-year rolling windows."""
    dates = sorted(ts.keys())
    all_up = all_n = 0
    for date in dates:
        d = ts[date]
        try: op = float(d["1. open"]); cl = float(d["4. close"])
        except: continue
        all_n += 1; all_up += 1 if cl > op else 0
    base = all_up / all_n if all_n else 0.5
    sign = 1 if overall_z > 0 else -1
    cons = 0
    for _, start, end in _WINDOWS:
        ups = n = 0
        for date in dates:
            if date < start or date > end: continue
            d = ts[date]
            try: op = float(d["1. open"]); cl = float(d["4. close"])
            except: continue
            if _digit_sum(op) == dg:
                n += 1; ups += 1 if cl > op else 0
        if n >= MIN_WIN_N:
            z = _z(ups, n, base)
            if abs(z) >= 1.0 and (z > 0) == (sign > 0):
                cons += 1
    if cons >= 4: return "consistent"
    if cons >= 2: return "partial"
    return "stale"


def run():
    files = sorted(OHLCV.glob("*_daily.json"))
    print(f"Processing {len(files)} symbols...")
    ts_map: dict = {}
    all_rows = []
    for path in files:
        sym = path.stem.replace("_daily", "")
        try:
            with open(path) as f:
                ts = json.load(f).get("Time Series (Daily)", {})
            ts_map[sym] = ts
        except Exception:
            continue
        all_rows.extend(analyze(path))

    sig = [r for r in all_rows if abs(r["z"]) >= MIN_ABS_Z]
    print(f"Total rows: {len(all_rows)} | 95%+ confidence: {len(sig)} — adding temporal quality...")

    for r in sig:
        ts = ts_map.get(r["symbol"], {})
        r["temporal"] = _temporal_quality(r["symbol"], r["digit"], r["z"], ts) if ts else "no_data"

    with open(OUT_FILE, "w") as f:
        json.dump(all_rows, f)
    from collections import Counter
    tq = Counter(r.get("temporal","none") for r in sig)
    print(f"Temporal: {dict(tq)}")
    print(f"Saved {len(all_rows)} rows to {OUT_FILE}")


if __name__ == "__main__":
    run()
