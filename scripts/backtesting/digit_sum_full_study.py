"""
Digit-Sum (Full Cents) Numerology Study — regenerates Data/digit_sum_full_study.json.

Variant of digit_sum_study.py that includes the cents digits.
Example: $247.35 → 2+4+7+3+5=21 → 2+1=3 (vs integer-only → 247→4).

Run monthly alongside digit_sum_study.py:
    python scripts/backtesting/digit_sum_full_study.py
"""

import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from scripts.backtesting._study_utils import window_analysis

DATA     = Path(__file__).resolve().parent.parent.parent / "Data"
OHLCV    = DATA / "Symbol_full"
OUT_FILE = DATA / "digit_sum_full_study.json"

MIN_N     = 50
MIN_ABS_Z = 2.0


def _digit_sum_full(price: float) -> int:
    s = f"{abs(price):.2f}".replace(".", "")
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

        dg = _digit_sum_full(op)
        open_b[dg][0] += 1 if cl > op else 0
        open_b[dg][1] += 1

        if i + 1 < len(dates):
            dn = ts[dates[i + 1]]
            try:
                on = float(dn["1. open"])
                cn = float(dn["4. close"])
            except (KeyError, ValueError):
                continue
            dg2 = _digit_sum_full(cl)
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




def run():
    files = sorted(OHLCV.glob("*_daily.json"))
    print(f"Processing {len(files)} symbols (full-cents digit-sum)...")
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
        if ts:
            r.update(window_analysis(r["digit"], r["z"], ts, _digit_sum_full, r["type"]))
        else:
            r["temporal"] = "no_data"

    with open(OUT_FILE, "w") as f:
        json.dump(all_rows, f)
    from collections import Counter
    tq = Counter(r.get("temporal","none") for r in sig)
    print(f"Temporal: {dict(tq)}")
    print(f"Saved {len(all_rows)} rows to {OUT_FILE}")


if __name__ == "__main__":
    run()
