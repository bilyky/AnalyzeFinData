"""
Compare get_prev_max_price (recursive chain) vs OHLCV 20-day high lookback.
Chain method is skipped for symbols with > CHAIN_FILE_LIMIT cached files
(glob scan per recursion level makes it O(N^2) in file count).
"""
import datetime
import json
import os
import sys
import time
import glob as _glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import powergauge

DATE             = datetime.datetime(2026, 2, 26)
LOOKBACK         = 20       # trading days for OHLCV high
MAX_SYMS         = 30
CHAIN_FILE_LIMIT = 30       # skip chain method if symbol has more cached files

SYM_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")

SYMBOLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "symbols_to_check.txt")
with open(SYMBOLS_FILE) as f:
    all_syms = [line.strip().split()[-1] for line in f if line.strip()]

candidates = []
for sym in all_syms:
    if not os.path.exists(os.path.join(SYM_DIR, f"{sym}_{DATE.date()}.json")):
        continue
    if not os.path.exists(os.path.join(OHLCV_DIR, f"{sym}_daily.json")):
        continue
    n = len(_glob.glob(os.path.join(SYM_DIR, f"{sym}_*.json")))
    candidates.append((n, sym))
    if len(candidates) >= MAX_SYMS * 3:
        break

candidates.sort()
candidates = candidates[:MAX_SYMS]

sys.setrecursionlimit(500)

print(f"Testing {len(candidates)} symbols  date={DATE.date()}  OHLCV lookback={LOOKBACK}d\n")
print(f"{'Symbol':<8} {'Price':>8} {'NFiles':>7} {'Chain':>9} {'OHLCV':>9} {'Diff%':>7} {'ChainMs':>8} {'OhlcvMs':>7}")
print("-" * 72)

total_chain_ms = 0.0
total_ohlcv_ms = 0.0
compared = 0

for n_files, sym in candidates:
    with open(os.path.join(SYM_DIR, f"{sym}_{DATE.date()}.json")) as f:
        data_jsn = json.load(f)
    with open(os.path.join(OHLCV_DIR, f"{sym}_daily.json")) as f:
        ohlcv_ts = json.load(f).get("Time Series (Daily)") or {}

    pg = powergauge.PowerGauge(sym, DATE.date())
    pg.init_from_json(data_jsn, check_schema=False)
    pg.find_prev_pf()

    # --- Chain method (only for small caches) ---
    chain_target = None
    chain_ms = 0.0
    if n_files <= CHAIN_FILE_LIMIT and pg.prevPG:
        t0 = time.perf_counter()
        try:
            chain_target = pg.prevPG.get_prev_max_price(pg.price)
        except RecursionError:
            chain_target = None
        chain_ms = (time.perf_counter() - t0) * 1000
        total_chain_ms += chain_ms

    # --- OHLCV method ---
    t0 = time.perf_counter()
    all_dates = sorted(ohlcv_ts.keys())
    date_str = str(DATE.date())
    past = [d for d in all_dates if d <= date_str]
    ohlcv_target = 0.0
    if past:
        idx = all_dates.index(past[-1])
        window = all_dates[max(0, idx - LOOKBACK): idx]
        hi = max((float(ohlcv_ts[d].get('2. high', 0)) for d in window), default=0)
        ohlcv_target = round(hi, 2) if hi > pg.price else 0.0
    ohlcv_ms = (time.perf_counter() - t0) * 1000
    total_ohlcv_ms += ohlcv_ms

    if chain_target is not None:
        diff_pct = (ohlcv_target - chain_target) / chain_target * 100 if chain_target else float('nan')
        chain_str = f"{chain_target:9.2f}"
        diff_str  = f"{diff_pct:+7.1f}%"
        compared += 1
    else:
        chain_str = "  (skipped)" if n_files > CHAIN_FILE_LIMIT else "  RecursErr"
        diff_str  = "     n/a"

    print(f"{sym:<8} {pg.price:>8.2f} {n_files:>7} {chain_str} {ohlcv_target:>9.2f} {diff_str} {chain_ms:>7.1f}ms {ohlcv_ms:>6.1f}ms")

print("-" * 72)
print(f"Chain vs OHLCV compared for {compared}/{len(candidates)} symbols")
print(f"Chain total {total_chain_ms:.0f}ms  OHLCV total {total_ohlcv_ms:.0f}ms")
