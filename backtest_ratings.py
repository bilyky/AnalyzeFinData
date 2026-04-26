"""
Backtest buying_ratio against forward price returns using:
  - Chaikin API cache (Data/Symbol/) for PGR, indicators, price
  - OHLCV (Data/Symbol_full/) for setup_ok, risk/reward, seasonality, forward returns

For each (symbol, date) in the cache, computes buying_ratio and the
5/10/20-day forward return, then reports average return by rating bucket.

Usage:
    python backtest_ratings.py [min_year]
    python backtest_ratings.py          # default: 2023 onwards
    python backtest_ratings.py 2024     # 2024+ only
"""

import json
import os
import sys
import glob
from collections import defaultdict

SYM_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")

STOP_DAYS      = 3
TARGET_LOOKBACK = 10
SMA_DAYS       = 20
FWD_WINDOWS    = [5, 10, 20]  # trading days

# Buying ratio component maps (mirrors _buying_ratio in powergauge.py)
PGR_MAP  = {1: -4.0, 2: -2.0, 3: 0.0, 4: 2.0, 5: 4.0}
LT_MAP   = {'Strong': -1.0, 'Neutral': 0.0, 'Weak': 1.0}
MF_MAP   = {'Strong': 0.75, 'Neutral': 0.0, 'Weak': -0.75}
OB_MAP   = {'Optimal': 0.5, 'Early': 0.25, 'Neutral': 0.0, 'Wait': -0.5}
IND_MAP  = {'Strong': 0.5, 'Weak': -0.5}

BUCKETS = [
    ("br <= -3",  lambda b: b <= -3),
    ("-3 to  0",  lambda b: -3 < b <= 0),
    (" 0 to  3",  lambda b: 0  < b <= 3),
    (" 3 to  6",  lambda b: 3  < b <= 6),
    ("br >=  6",  lambda b: b  > 6),
]


def load_ohlcv(symbol):
    path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f).get("Time Series (Daily)") or None


def precompute_seasonality(ohlcv_ts):
    """Returns {month: score} for all 12 months."""
    month_ends = {}
    for d in sorted(ohlcv_ts.keys()):
        y, m = int(d[:4]), int(d[5:7])
        month_ends[(y, m)] = d

    result = {}
    for mo in range(1, 13):
        returns = []
        for (y, m), end_date in month_ends.items():
            if m != mo:
                continue
            prev_m = m - 1 if m > 1 else 12
            prev_y = y if m > 1 else y - 1
            prev_end = month_ends.get((prev_y, prev_m))
            if not prev_end:
                continue
            c_end  = float(ohlcv_ts[end_date].get('4. close', 0))
            c_prev = float(ohlcv_ts[prev_end].get('4. close', 0))
            if c_prev > 0:
                returns.append((c_end - c_prev) / c_prev * 100)
        if len(returns) < 3:
            result[mo] = 0.0
            continue
        avg = sum(returns) / len(returns)
        if avg > 2.0:   result[mo] =  1.0
        elif avg > 1.0: result[mo] =  0.5
        elif avg > -1.0:result[mo] =  0.0
        elif avg > -2.0:result[mo] = -0.5
        else:           result[mo] = -1.0
    return result


def extract_pgr_corr(data):
    pgr_list = data.get('pgr') or []
    cl = data.get('checklist_stocks') or {}
    if len(pgr_list) > 5 and 'Corrected PGR Value' in pgr_list[5]:
        v = pgr_list[5]['Corrected PGR Value']
    else:
        v = cl.get('pgrRating', 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def compute_br(data, prev_data, price, idx, all_dates, ohlcv_ts, seasonality_map):
    cl = data.get('checklist_stocks') or {}
    pgr_corr      = extract_pgr_corr(data)
    prev_pgr_corr = extract_pgr_corr(prev_data) if prev_data else pgr_corr
    pgr_delta     = pgr_corr - prev_pgr_corr
    lt_trend      = str(cl.get('ltTrend', '') or '').strip()
    money_flow    = str(cl.get('moneyFlow', '') or '').strip()
    over_bt_sl    = str(cl.get('overboughtOversold', '') or '').strip()
    industry      = str(cl.get('industry', '') or '').strip()
    month         = int(all_dates[idx][5:7])

    # setup_ok: price > SMA20 AND price > close[3d ago]
    sma_w = all_dates[max(0, idx - SMA_DAYS): idx]
    if len(sma_w) >= SMA_DAYS // 2:
        sma20 = sum(float(ohlcv_ts[d].get('4. close', 0)) for d in sma_w) / len(sma_w)
        trend_ok = price > sma20 > 0
    else:
        trend_ok = False
    if idx >= 3:
        price_3d = float(ohlcv_ts[all_dates[idx - 3]].get('4. close', 0))
        dir_ok = price > price_3d > 0
    else:
        dir_ok = False
    setup_ok = trend_ok and dir_ok

    # risk/reward from OHLCV
    stop_w = all_dates[max(0, idx - STOP_DAYS): idx]
    local_low = min((float(ohlcv_ts[d].get('3. low', 0)) for d in stop_w), default=0)
    stop = round(local_low * 0.99, 2) if local_low else 0
    if stop >= price:
        stop = 0
    tgt_w = all_dates[max(0, idx - TARGET_LOOKBACK): idx]
    resistance = max((float(ohlcv_ts[d].get('2. high', 0)) for d in tgt_w), default=0)
    target = resistance if resistance > price else 0
    if stop > 0 and target > 0:
        rr = round((target - price) / (price - stop), 2)
    else:
        rr = 0.0

    # score
    score = 0.0
    score += PGR_MAP.get(pgr_corr, 0.0)
    if rr >= 3.0:   score += 2.0
    elif rr >= 2.0: score += 1.5
    elif rr >= 1.0: score += 1.0
    elif rr >= 0.5: score += 0.5
    elif rr > 0:    score += 0.0
    else:           score -= 1.0
    score += LT_MAP.get(lt_trend, 0.0)
    score += MF_MAP.get(money_flow, 0.0)
    score += OB_MAP.get(over_bt_sl, 0.0)
    score += IND_MAP.get(industry, 0.0)
    score += 0.25 if pgr_delta > 0 else (-0.25 if pgr_delta < 0 else 0.0)
    score += seasonality_map.get(month, 0.0)

    return round(max(-10.0, min(10.0, score)), 1)


def process_symbol(symbol, min_year, ohlcv_ts, all_dates):
    """Returns list of (br, fwd_5, fwd_10, fwd_20) tuples."""
    seasonality_map = precompute_seasonality(ohlcv_ts)
    ohlcv_date_set = set(all_dates)

    # Load all Chaikin cache files for this symbol, sorted by date
    pattern = os.path.join(SYM_DIR, f"{symbol}_*.json")
    cache_files = sorted(glob.glob(pattern))

    # Build date → (data, path) map
    date_data = {}
    for path in cache_files:
        base = os.path.basename(path).replace('.json', '')
        date_str = base[len(symbol) + 1:]
        if date_str < str(min_year):
            continue
        if date_str not in ohlcv_date_set:
            continue
        date_data[date_str] = path

    if not date_data:
        return []

    sorted_cache_dates = sorted(date_data.keys())
    results = []

    prev_data = None
    prev_date = None

    for date_str in sorted_cache_dates:
        try:
            with open(date_data[date_str]) as f:
                data = json.load(f)
        except Exception:
            prev_data, prev_date = None, date_str
            continue

        if data.get('status') == 'invalid symbol':
            prev_data, prev_date = data, date_str
            continue

        # Price from JSON
        meta = (data.get('metaInfo') or [{}])[0]
        cl   = data.get('checklist_stocks') or {}
        try:
            price = float(meta.get('Last') or cl.get('lastPrice') or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            prev_data, prev_date = data, date_str
            continue

        # Verify price against OHLCV (sanity check, skip large mismatches)
        if date_str in ohlcv_ts:
            ohlcv_close = float(ohlcv_ts[date_str].get('4. close', 0))
            if ohlcv_close > 0 and abs(price - ohlcv_close) / ohlcv_close > 0.1:
                prev_data, prev_date = data, date_str
                continue

        try:
            idx = all_dates.index(date_str)
        except ValueError:
            prev_data, prev_date = data, date_str
            continue

        if idx < max(SMA_DAYS, TARGET_LOOKBACK, 3):
            prev_data, prev_date = data, date_str
            continue

        br = compute_br(data, prev_data, price, idx, all_dates, ohlcv_ts, seasonality_map)

        # Forward returns from OHLCV
        fwd = []
        for w in FWD_WINDOWS:
            future_idx = idx + w
            if future_idx < len(all_dates):
                fwd_close = float(ohlcv_ts[all_dates[future_idx]].get('4. close', 0))
                fwd.append((fwd_close - price) / price * 100 if fwd_close > 0 else None)
            else:
                fwd.append(None)

        if fwd[1] is not None:   # require 10d forward return at minimum
            results.append((br, fwd[0], fwd[1], fwd[2]))

        prev_data, prev_date = data, date_str

    return results


def run(min_year=2023):
    print(f"\nBacktesting buying_ratio >= {min_year} ...")

    # Find symbols with both Chaikin cache and OHLCV
    ohlcv_files = {os.path.basename(f).replace('_daily.json', '')
                   for f in glob.glob(os.path.join(OHLCV_DIR, '*_daily.json'))}
    cache_syms  = {os.path.basename(f).rsplit('_', 1)[0]
                   for f in glob.glob(os.path.join(SYM_DIR, '*.json'))}
    symbols = sorted(ohlcv_files & cache_syms)
    print(f"  Symbols with both Chaikin + OHLCV: {len(symbols)}")

    # Collect all results
    bucket_data = {label: {w: [] for w in FWD_WINDOWS} for label, _ in BUCKETS}
    total_obs = 0

    for i, sym in enumerate(symbols, 1):
        ohlcv_ts = load_ohlcv(sym)
        if not ohlcv_ts:
            continue
        all_dates = sorted(ohlcv_ts.keys())
        rows = process_symbol(sym, str(min_year), ohlcv_ts, all_dates)
        for br, f5, f10, f20 in rows:
            for label, test in BUCKETS:
                if test(br):
                    if f5  is not None: bucket_data[label][5].append(f5)
                    if f10 is not None: bucket_data[label][10].append(f10)
                    if f20 is not None: bucket_data[label][20].append(f20)
                    total_obs += 1
                    break
        if i % 50 == 0:
            print(f"  ... {i}/{len(symbols)} symbols processed")

    print(f"\n  Total observations: {total_obs:,}\n")

    # Report
    header = f"  {'Bucket':<12}  {'Count':>6}  " + \
             "  ".join(f"{'Avg '+str(w)+'d':>8}  {'Med'+str(w)+'d':>7}  {'Win%'+str(w)+'d':>8}" for w in FWD_WINDOWS)
    print(header)
    print("  " + "-" * (len(header) - 2))

    all_wins = {w: [] for w in FWD_WINDOWS}
    for label, _ in BUCKETS:
        d = bucket_data[label]
        n = len(d[10])
        row = f"  {label:<12}  {n:>6}"
        for w in FWD_WINDOWS:
            vals = d[w]
            if vals:
                avg = sum(vals) / len(vals)
                svals = sorted(vals)
                med = svals[len(svals) // 2]
                win = 100 * sum(1 for v in vals if v > 0) / len(vals)
                row += f"  {avg:>+7.2f}%  {med:>+6.2f}%  {win:>7.1f}%"
                all_wins[w].append((label, win))
            else:
                row += f"  {'N/A':>8}  {'N/A':>7}  {'N/A':>8}"
        print(row)

    # Monotonicity check on win% (more robust than avg return)
    print(f"\n  Monotonicity check on Win% (more robust to outliers):")
    for w in FWD_WINDOWS:
        wins = [a for _, a in all_wins[w]]
        is_mono = all(wins[i] <= wins[i+1] for i in range(len(wins)-1))
        print(f"    {w:2d}d window: {'PASS' if is_mono else 'FAIL'}  "
              f"{' < '.join(f'{a:.1f}' for a in wins)}")


if __name__ == "__main__":
    min_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    run(min_year)
