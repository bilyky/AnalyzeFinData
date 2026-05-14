"""
Factor analysis backtest.

For each factor in the BR score, measures:
  - win rate per factor bucket (10d close > entry price)
  - mean 10d return per bucket
  - spread (max - min win rate across buckets) = predictive value

High spread = factor is informative.
Low spread  = factor adds little, candidate for removal.

Usage:
    python backtest.py              # 2023-2025 data
    python backtest.py 2024 2025    # specific years
"""

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

DATA_DIR  = os.path.join(os.path.dirname(__file__), "Data")
HIST_DIR  = os.path.join(DATA_DIR, "History")
OHLCV_DIR = os.path.join(DATA_DIR, "Symbol_full")

# History CSV column indices (0-based)
COL_SYMBOL    = 1
COL_PGR       = 4   # current PGR string (Bu/Be/N/Bu+ etc)
COL_IND_STR   = 5   # industry_strength
COL_STOP      = 6   # price * 0.95
COL_PRICE     = 7
COL_TARGET    = 8
COL_RR        = 9   # risk_ratio
COL_PGR_DELTA = 16
COL_LT_TREND  = 19
COL_MONEY_FL  = 20
COL_OB_OS     = 21

# Map PGR string → numeric 1-5
PGR_NUM = {'Be-': 1, 'Be': 2, 'N': 3, 'Bu': 4, 'Bu+': 5}

def pgr_to_num(s: str) -> int | None:
    s = s.strip()
    # Handle compound like "N/Bu" → take the rightmost (current)
    if '/' in s:
        s = s.split('/')[-1].strip()
    return PGR_NUM.get(s)


def load_ohlcv(symbol: str) -> dict:
    """Return {date_str: {'close': float, 'volume': float}} for a symbol."""
    path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f).get("Time Series (Daily)", {})
    return {
        d: {'close': float(v["4. close"]), 'volume': float(v.get("5. volume", 0))}
        for d, v in raw.items()
    }


def trading_days_after(ohlcv: dict, entry_date: str, n: int = 10) -> float | None:
    """Return close price n trading days after entry_date, or None."""
    dates = sorted(ohlcv.keys())
    try:
        idx = dates.index(entry_date)
    except ValueError:
        past = [d for d in dates if d <= entry_date]
        if not past:
            return None
        idx = dates.index(past[-1])
    target_idx = idx + n
    if target_idx >= len(dates):
        return None
    return ohlcv[dates[target_idx]]['close']


def rel_volume_bucket(ohlcv: dict, entry_date: str, lookback: int = 20) -> str | None:
    """Relative volume at entry: entry-day vol / avg vol over prior `lookback` days."""
    dates = sorted(ohlcv.keys())
    past = [d for d in dates if d <= entry_date]
    if len(past) < lookback + 1:
        return None
    idx = len(past) - 1
    entry_vol = ohlcv[past[idx]]['volume']
    avg_vol = sum(ohlcv[past[i]]['volume'] for i in range(idx - lookback, idx)) / lookback
    if avg_vol <= 0:
        return None
    rv = entry_vol / avg_vol
    if rv >= 2.0:   return "Very High (2x+)"
    if rv >= 1.5:   return "High (1.5-2x)"
    if rv >= 0.75:  return "Normal (0.75-1.5x)"
    return "Low (<0.75x)"


SYM_DIR = os.path.join(DATA_DIR, "Symbol")


def load_pgr_subcategories(symbol: str, date_str: str) -> dict | None:
    """Load Financials/Earnings/Technicals/Experts scores (1-5) from Symbol JSON."""
    path = os.path.join(SYM_DIR, f"{symbol}_{date_str}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            pgr_list = json.load(f).get("pgr", [])
        cats = {}
        for item in pgr_list:
            for cat in ("Financials", "Earnings", "Technicals", "Experts"):
                if cat in item:
                    val = item[cat][0].get("Value")
                    if val is not None:
                        cats[cat] = int(val)
        return cats if len(cats) == 4 else None
    except Exception:
        return None


def load_history(years: list[int]) -> list[dict]:
    """Load all History CSV rows for the given years."""
    rows = []
    for year in years:
        year_dir = os.path.join(HIST_DIR, str(year))
        if not os.path.isdir(year_dir):
            continue
        for fname in sorted(os.listdir(year_dir)):
            if not fname.startswith("symbols_to_check_") or not fname.endswith(".csv"):
                continue
            date_str = fname.replace("symbols_to_check_", "").replace(".csv", "")
            fpath = os.path.join(year_dir, fname)
            with open(fpath, newline="", encoding="utf-8", errors="replace") as f:
                for row in csv.reader(f):
                    if len(row) < 22:
                        continue
                    try:
                        price = float(row[COL_PRICE])
                        rr    = float(row[COL_RR])
                    except ValueError:
                        continue
                    if price <= 0:
                        continue
                    rows.append({
                        "date":      date_str,
                        "symbol":    row[COL_SYMBOL].strip(),
                        "pgr":       pgr_to_num(row[COL_PGR]),
                        "ind_str":   row[COL_IND_STR].strip(),
                        "rr":        rr,
                        "pgr_delta": int(row[COL_PGR_DELTA]) if row[COL_PGR_DELTA].lstrip("-").isdigit() else 0,
                        "lt_trend":  row[COL_LT_TREND].strip(),
                        "money_fl":  row[COL_MONEY_FL].strip(),
                        "ob_os":     row[COL_OB_OS].strip(),
                        "price":     price,
                    })
    return rows


def rr_bucket(rr: float) -> str:
    if rr >= 3.0:  return "rr>=3"
    if rr >= 2.0:  return "rr 2-3"
    if rr >= 1.0:  return "rr 1-2"
    if rr >= 0.5:  return "rr 0.5-1"
    if rr > 0:     return "rr 0-0.5"
    return "rr<=0"


# Values that indicate missing/invalid Chaikin data — excluded from analysis
_INVALID = {"N/A", "NA", ""}


def pgr_bucket(v) -> str | None:
    if v is None: return None
    v = int(v)
    if v <= 2: return "Bear(1-2)"
    if v == 3: return "Neutral(3)"
    return "Bull(4-5)"


FACTORS = {
    "PGR overall":      lambda r: pgr_bucket(r["pgr"]),
    "PGR Technicals":   lambda r: pgr_bucket(r.get("pgr_tech")),
    "PGR Experts":      lambda r: pgr_bucket(r.get("pgr_exp")),
    "PGR Earnings":     lambda r: pgr_bucket(r.get("pgr_earn")),
    "PGR Financials":   lambda r: pgr_bucket(r.get("pgr_fin")),
    "LT Trend":         lambda r: r["lt_trend"] if r["lt_trend"] not in _INVALID else None,
    "Money Flow":       lambda r: r["money_fl"] if r["money_fl"] not in _INVALID else None,
    "OB/OS":            lambda r: r["ob_os"]    if r["ob_os"]    not in _INVALID else None,
    "Industry Str":     lambda r: r["ind_str"]  if r["ind_str"]  not in _INVALID else None,
    "PGR Delta":        lambda r: "changed" if r["pgr_delta"] != 0 else "no change",
    "R/R bucket":       lambda r: rr_bucket(r["rr"]),
    "Rel Volume":       lambda r: r.get("rel_vol"),
}

HORIZONS = {
    "10d (short)": "ret_10",
    "30d (mid)":   "ret_30",
    "60d (long)":  "ret_60",
}


def analyze(obs: list[dict]):
    """Print factor spreads across 10d / 30d / 60d horizons."""

    for horizon_label, ret_key in HORIZONS.items():
        h_obs = [r for r in obs if r.get(ret_key) is not None]
        if not h_obs:
            continue

        baseline_total = len(h_obs)
        baseline_wr    = sum(1 for r in h_obs if r[ret_key] > 0) / baseline_total
        baseline_ret   = sum(r[ret_key] for r in h_obs) / baseline_total

        print(f"\n{'='*70}")
        print(f"HORIZON: {horizon_label}  n={baseline_total:,}  baseline win={baseline_wr:.1%}  mean={baseline_ret:+.2%}")
        print(f"{'='*70}")

        spreads = {}
        for factor_name, key_fn in FACTORS.items():
            buckets = defaultdict(list)
            for r in h_obs:
                k = key_fn(r)
                if k:
                    buckets[k].append(r)

            results = []
            for bucket, rlist in buckets.items():
                n    = len(rlist)
                wr   = sum(1 for r in rlist if r[ret_key] > 0) / n
                mean = sum(r[ret_key] for r in rlist) / n
                results.append((bucket, n, wr, mean))

            results.sort(key=lambda x: -x[2])
            wrs = [x[2] for x in results]
            spread = max(wrs) - min(wrs) if wrs else 0
            spreads[factor_name] = (spread, results)

        # Print factors sorted by spread descending
        print(f"\n  {'Factor':<15}  {'Spread':>7}  Buckets (best -> worst)")
        print(f"  {'-'*65}")
        for factor_name, (spread, results) in sorted(spreads.items(), key=lambda x: -x[1][0]):
            low_flag = "  ***" if spread < 0.03 else ""
            bucket_summary = "  |  ".join(
                f"{b}: {wr:.1%} ({wr-baseline_wr:+.1%})"
                for b, n, wr, mean in results[:4]
            )
            print(f"  {factor_name:<15}  {spread:>6.1%}{low_flag:<5}  {bucket_summary}")

        print()


def main():
    years = [int(y) for y in sys.argv[1:]] if len(sys.argv) > 1 else [2023, 2024, 2025]
    print(f"Loading history for years: {years}...")
    rows = load_history(years)
    print(f"  {len(rows):,} raw observations loaded")

    # Load OHLCV and compute forward returns for all horizons
    # Also load PGR sub-categories from Symbol JSONs (every 3rd row for speed)
    print("Computing forward returns (10d / 30d / 60d) + PGR sub-categories...")
    ohlcv_cache = {}
    obs = []
    skipped = 0
    for i, r in enumerate(rows):
        sym = r["symbol"]
        if sym not in ohlcv_cache:
            ohlcv_cache[sym] = load_ohlcv(sym)
        ohlcv = ohlcv_cache[sym]
        if not ohlcv:
            skipped += 1
            continue

        entry = r["price"]
        rec = dict(r)
        has_any = False
        for days, key in [(10, "ret_10"), (30, "ret_30"), (60, "ret_60")]:
            fwd = trading_days_after(ohlcv, r["date"], n=days)
            if fwd is not None:
                rec[key] = (fwd - entry) / entry
                has_any = True
            else:
                rec[key] = None

        if not has_any:
            skipped += 1
            continue

        # Relative volume at entry date
        rec["rel_vol"] = rel_volume_bucket(ohlcv, r["date"])

        # Load PGR sub-categories for every 3rd row (balance coverage vs speed)
        if i % 3 == 0:
            cats = load_pgr_subcategories(sym, r["date"])
            if cats:
                rec["pgr_fin"]  = cats.get("Financials")
                rec["pgr_earn"] = cats.get("Earnings")
                rec["pgr_tech"] = cats.get("Technicals")
                rec["pgr_exp"]  = cats.get("Experts")

        obs.append(rec)

    print(f"  {len(obs):,} observations loaded ({skipped:,} skipped)")
    sub_count = sum(1 for r in obs if r.get("pgr_tech") is not None)
    print(f"  {sub_count:,} with PGR sub-category data\n")

    analyze(obs)


if __name__ == "__main__":
    main()
