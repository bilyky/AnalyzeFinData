"""
Verify stop and target calculations using Symbol_full OHLCV historical data.

Stop   = min low of the last STOP_DAYS trading days before the test date * 0.99
Target = highest high over the preceding TARGET_LOOKBACK days
         (only if above current close; else 0 = no target)

Filters compared:
  none     - no filter (baseline)
  trend    - close > SMA(20)
  dir3     - close > close[3d ago]
  dir5     - close > close[5d ago]
  dir7     - close > close[7d ago]
  t+d3     - trend + dir3 combined
  t+d5     - trend + dir5 combined
  t+d7     - trend + dir7 combined

Usage:
    python verify_stops.py [lookback_days] [lookforward_days]
    python verify_stops.py               # defaults: 200d lookback, 10d forward
    python verify_stops.py 300 15
"""

import json
import os
import sys

OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")

STOP_DAYS       = 3
SMA_DAYS        = 20
TARGET_LOOKBACK = 10   # days for resistance high — winner from previous run
MIN_HISTORY     = max(STOP_DAYS, SMA_DAYS, TARGET_LOOKBACK, 7)

# ~40 symbols across sectors, market caps, and volatility profiles
SYMBOLS = [
    # Mega-cap tech
    "AAPL", "AMZN", "MSFT", "GOOGL", "META", "NVDA",
    # Semiconductors
    "AMD", "AVGO", "QCOM", "LRCX", "MU", "INTC",
    # Software / networking
    "ADBE", "ANET", "CRWD", "SNPS", "CDNS",
    # Healthcare / biotech
    "AMGN", "VRTX", "LLY", "MRK", "GILD", "MDT", "JNJ", "PFE",
    # Financials
    "JPM", "GS", "BAC", "V", "MS", "SCHW", "AJG", "AMP",
    # Consumer
    "COST", "HD", "NKE", "SBUX", "TJX", "TGT",
    # Energy
    "CVX", "COP", "OXY",
    # Industrials
    "CAT", "DE", "GE", "EMR",
    # High-vol / speculative
    "TSLA", "AAL", "ABNB", "PLTR", "SNAP",
]

FILTERS = ["none", "trend", "dir3", "dir5", "dir7", "t+d3", "t+d5", "t+d7"]


def load_ohlcv(symbol: str) -> dict | None:
    path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        d = json.load(f)
    return d.get("Time Series (Daily)") or None


def sma(all_dates: list, ts: dict, end_idx: int, n: int) -> float:
    window = all_dates[max(0, end_idx - n): end_idx]
    if len(window) < n // 2:
        return 0.0
    return sum(float(ts[d].get('4. close', 0)) for d in window) / len(window)


def compute_stop_target(idx: int, date_str: str, all_dates: list,
                        ts: dict) -> tuple:
    close = float(ts[date_str].get('4. close', 0))
    if close <= 0:
        return 0.0, 0.0

    stop_window = all_dates[max(0, idx - STOP_DAYS): idx]
    local_low   = min((float(ts[d].get('3. low', 0)) for d in stop_window), default=0)
    raw_stop    = round(local_low * 0.99, 2) if local_low else 0.0
    stop        = raw_stop if raw_stop < close else 0.0

    tgt_window  = all_dates[max(0, idx - TARGET_LOOKBACK): idx]
    resistance  = max((float(ts[d].get('2. high', 0)) for d in tgt_window), default=0)
    target      = round(resistance, 2) if resistance > close else 0.0

    return stop, target


def check_outcome(start_idx: int, all_dates: list, ts: dict,
                  stop: float, target: float, lookforward: int) -> str:
    if stop <= 0 and target <= 0:
        return "NO_LEVELS"
    for fd in all_dates[start_idx: start_idx + lookforward]:
        low  = float(ts[fd].get('3. low',  0))
        high = float(ts[fd].get('2. high', 0))
        hit_stop   = stop   > 0 and low  <= stop
        hit_target = target > 0 and high >= target
        if hit_stop and hit_target:
            return "BOTH"
        if hit_target:
            return "TARGET_HIT"
        if hit_stop:
            return "STOP_HIT"
    return "NEITHER"


def passes(fname: str, idx: int, date_str: str, all_dates: list, ts: dict) -> bool:
    close = float(ts[date_str].get('4. close', 0))
    need_trend = fname in ("trend", "t+d3", "t+d5", "t+d7")
    need_dir   = {"dir3": 3, "dir5": 5, "dir7": 7,
                  "t+d3": 3, "t+d5": 5, "t+d7": 7}.get(fname, 0)

    if need_trend:
        avg = sma(all_dates, ts, idx, SMA_DAYS)
        if avg <= 0 or close <= avg:
            return False

    if need_dir:
        if idx < need_dir:
            return False
        prev = float(ts[all_dates[idx - need_dir]].get('4. close', 0))
        if close <= prev:
            return False

    return True


def win_cell(results: dict) -> str:
    total = sum(results.values())
    if total == 0:
        return f"{'n=0':>18}"
    hit = results['TARGET_HIT'] + results['STOP_HIT']
    win = (100 * results['TARGET_HIT'] // hit) if hit else 0
    t   = 100 * results['TARGET_HIT'] // total
    s   = 100 * results['STOP_HIT']   // total
    return f"n={total:3d} win={win:2d}% T={t:2d}%S={s:2d}%"


def run_symbol(symbol: str, ts: dict, lookback_days: int,
               lookforward_days: int) -> dict:
    """Return {filter_name: result_dict} for this symbol."""
    all_dates  = sorted(ts.keys())
    test_dates = all_dates[MIN_HISTORY: -lookforward_days][-lookback_days:]

    # Pre-compute per-date data once
    rows = []
    for date_str in test_dates:
        idx = all_dates.index(date_str)
        stop, target = compute_stop_target(idx, date_str, all_dates, ts)
        outcome = check_outcome(idx + 1, all_dates, ts, stop, target, lookforward_days)
        rows.append((idx, date_str, outcome))

    out = {}
    for fname in FILTERS:
        bucket = {"TARGET_HIT": 0, "STOP_HIT": 0, "NEITHER": 0,
                  "BOTH": 0, "NO_LEVELS": 0}
        for idx, date_str, outcome in rows:
            if passes(fname, idx, date_str, all_dates, ts):
                bucket[outcome] = bucket.get(outcome, 0) + 1
        out[fname] = bucket
    return out


def win_pct(b: dict) -> int | None:
    hit = b['TARGET_HIT'] + b['STOP_HIT']
    n   = sum(b.values())
    if n < 20 or hit == 0:
        return None
    return 100 * b['TARGET_HIT'] // hit


def print_results(all_results: dict, lookback_days: int, lookforward_days: int):
    col_w = 22
    print(f"\n{'='*100}")
    print(f"  target_lookback={TARGET_LOOKBACK}d  stop=min({STOP_DAYS}d lows)*0.99  "
          f"sma={SMA_DAYS}d  lookback={lookback_days}d  lookforward={lookforward_days}d")
    print(f"  Format: n=setups  win=TARGET/(TARGET+STOP)  T=target%  S=stop%")
    print(f"{'='*100}\n")

    hdr = f"  {'':8}"
    for fname in FILTERS:
        hdr += f"  {fname:>{col_w}}"
    print(hdr)
    print(f"  {'-'*8}" + f"  {'-'*col_w}" * len(FILTERS))

    for symbol, res in all_results.items():
        row = f"  {symbol:<8}"
        for fname in FILTERS:
            row += f"  {win_cell(res[fname]):>{col_w}}"
        print(row)

    print()
    print(f"  {'='*8}" + f"  {'='*col_w}" * len(FILTERS))

    # Collect per-filter win% lists
    filter_wins = {f: [] for f in FILTERS}
    for res in all_results.values():
        for fname in FILTERS:
            w = win_pct(res[fname])
            if w is not None:
                filter_wins[fname].append(w)

    def med(lst):
        if not lst:
            return 0
        s = sorted(lst)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m-1] + s[m]) // 2

    # Average
    print(f"  {'avg':8}", end="")
    for fname in FILTERS:
        ws = filter_wins[fname]
        v = sum(ws) // len(ws) if ws else 0
        print(f"  {f'avg={v}%':>{col_w}}", end="")
    print()

    # Median
    print(f"  {'median':8}", end="")
    for fname in FILTERS:
        v = med(filter_wins[fname])
        print(f"  {f'med={v}%':>{col_w}}", end="")
    print()

    # % of symbols above 60% win
    print(f"  {'>60% win':8}", end="")
    for fname in FILTERS:
        ws = filter_wins[fname]
        n_above = sum(1 for w in ws if w >= 60)
        pct = 100 * n_above // len(ws) if ws else 0
        print(f"  {f'{n_above}/{len(ws)} syms ({pct}%)':>{col_w}}", end="")
    print()

    # Lift over baseline (t+d3 focus)
    print(f"\n  Lift of t+d3 over 'none' per symbol (symbols with n>=20 in both):")
    lifts = []
    for sym, res in all_results.items():
        base = win_pct(res['none'])
        filt = win_pct(res['t+d3'])
        if base is not None and filt is not None:
            lift = filt - base
            lifts.append((sym, base, filt, lift))
    lifts.sort(key=lambda x: -x[3])
    for sym, base, filt, lift in lifts:
        bar = "+" * (lift // 2) if lift > 0 else "-" * ((-lift) // 2)
        sign = "+" if lift >= 0 else ""
        print(f"    {sym:<8}  {base:2d}% -> {filt:2d}%  ({sign}{lift:+d}pp)  {bar}")
    avg_lift = sum(x[3] for x in lifts) // len(lifts) if lifts else 0
    print(f"\n    Average lift: {avg_lift:+d}pp  |  "
          f"Positive: {sum(1 for x in lifts if x[3]>0)}/{len(lifts)} symbols")
    print()


if __name__ == "__main__":
    lookback    = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    lookforward = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    all_results = {}
    missing = []
    for sym in SYMBOLS:
        ts = load_ohlcv(sym)
        if ts is None:
            missing.append(sym)
            continue
        all_results[sym] = run_symbol(sym, ts, lookback, lookforward)

    if missing:
        print(f"  No OHLCV data for: {', '.join(missing)}")

    print_results(all_results, lookback, lookforward)
