"""
Decompose buying_ratio components — test each one independently to find
which components are actually predictive of forward returns.

For each component, bucket observations by that component's value and
report avg 10-day forward return + win%.

Usage: python backtest_components.py [min_year]
"""

import json
import os
import sys
import glob
from collections import defaultdict

SYM_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")

STOP_DAYS       = 3
TARGET_LOOKBACK = 10
SMA_DAYS        = 20
FWD_W           = 10   # trading days for forward return

PGR_MAP = {1: -4.0, 2: -2.0, 3: 0.0, 4: 2.0, 5: 4.0}
LT_MAP  = {'Strong': 1.0, 'Neutral': 0.0, 'Weak': -1.0}
MF_MAP  = {'Strong': 0.75, 'Neutral': 0.0, 'Weak': -0.75}
OB_MAP  = {'Optimal': 0.5, 'Early': 0.25, 'Neutral': 0.0, 'Wait': -0.5}
IND_MAP = {'Strong': 0.5, 'Weak': -0.5}


def load_ohlcv(symbol):
    path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f).get("Time Series (Daily)") or None


def precompute_seasonality(ohlcv_ts):
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


def process_symbol(symbol, min_year, ohlcv_ts, all_dates):
    """Returns list of component dicts + fwd_10."""
    seasonality_map = precompute_seasonality(ohlcv_ts)
    ohlcv_date_set = set(all_dates)

    pattern = os.path.join(SYM_DIR, f"{symbol}_*.json")
    cache_files = sorted(glob.glob(pattern))

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

    for date_str in sorted_cache_dates:
        try:
            with open(date_data[date_str]) as f:
                data = json.load(f)
        except Exception:
            prev_data = None
            continue

        if data.get('status') == 'invalid symbol':
            prev_data = data
            continue

        meta = (data.get('metaInfo') or [{}])[0]
        cl   = data.get('checklist_stocks') or {}
        try:
            price = float(meta.get('Last') or cl.get('lastPrice') or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            prev_data = data
            continue

        if date_str in ohlcv_ts:
            ohlcv_close = float(ohlcv_ts[date_str].get('4. close', 0))
            if ohlcv_close > 0 and abs(price - ohlcv_close) / ohlcv_close > 0.1:
                prev_data = data
                continue

        try:
            idx = all_dates.index(date_str)
        except ValueError:
            prev_data = data
            continue

        if idx < max(SMA_DAYS, TARGET_LOOKBACK, 3):
            prev_data = data
            continue

        pgr_corr      = extract_pgr_corr(data)
        prev_pgr_corr = extract_pgr_corr(prev_data) if prev_data else pgr_corr
        pgr_delta     = pgr_corr - prev_pgr_corr
        lt_trend      = str(cl.get('ltTrend', '') or '').strip()
        money_flow    = str(cl.get('moneyFlow', '') or '').strip()
        over_bt_sl    = str(cl.get('overboughtOversold', '') or '').strip()
        industry      = str(cl.get('industry', '') or '').strip()
        month         = int(date_str[5:7])

        # setup_ok
        sma_w = all_dates[max(0, idx - SMA_DAYS): idx]
        sma20 = sum(float(ohlcv_ts[d].get('4. close', 0)) for d in sma_w) / len(sma_w) if len(sma_w) >= SMA_DAYS // 2 else 0
        trend_ok = price > sma20 > 0
        price_3d = float(ohlcv_ts[all_dates[idx - 3]].get('4. close', 0)) if idx >= 3 else 0
        dir_ok = price > price_3d > 0
        setup_ok = trend_ok and dir_ok

        # risk/reward
        stop_w    = all_dates[max(0, idx - STOP_DAYS): idx]
        local_low = min((float(ohlcv_ts[d].get('3. low', 0)) for d in stop_w), default=0)
        stop      = round(local_low * 0.99, 2) if local_low else 0
        if stop >= price: stop = 0
        tgt_w      = all_dates[max(0, idx - TARGET_LOOKBACK): idx]
        resistance = max((float(ohlcv_ts[d].get('2. high', 0)) for d in tgt_w), default=0)
        target     = resistance if resistance > price else 0
        rr = round((target - price) / (price - stop), 2) if stop > 0 and target > 0 else 0.0

        # forward return
        future_idx = idx + FWD_W
        if future_idx >= len(all_dates):
            prev_data = data
            continue
        fwd_close = float(ohlcv_ts[all_dates[future_idx]].get('4. close', 0))
        if fwd_close <= 0:
            prev_data = data
            continue
        fwd10 = (fwd_close - price) / price * 100

        results.append({
            'pgr':       pgr_corr,
            'pgr_delta': pgr_delta,
            'setup_ok':  setup_ok,
            'trend_ok':  trend_ok,
            'dir_ok':    dir_ok,
            'rr':        rr,
            'lt_trend':  lt_trend,
            'money_flow': money_flow,
            'over_bt_sl': over_bt_sl,
            'industry':  industry,
            'season':    seasonality_map.get(month, 0.0),
            'fwd10':     fwd10,
        })
        prev_data = data

    return results


def report(label, groups):
    """groups = {key: [fwd10, ...]}"""
    print(f"\n  {label}")
    print(f"  {'Value':<12} {'Count':>7} {'Avg10d':>8} {'Win%':>7}")
    print(f"  {'-'*40}")
    for key in sorted(groups.keys(), key=lambda x: (isinstance(x, str), x)):
        vals = groups[key]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        win = 100 * sum(1 for v in vals if v > 0) / len(vals)
        print(f"  {str(key):<12} {len(vals):>7,} {avg:>+7.2f}% {win:>6.1f}%")


def run(min_year=2023):
    print(f"\nComponent analysis >= {min_year} ...")

    ohlcv_files = {os.path.basename(f).replace('_daily.json', '')
                   for f in glob.glob(os.path.join(OHLCV_DIR, '*_daily.json'))}
    cache_syms  = {os.path.basename(f).rsplit('_', 1)[0]
                   for f in glob.glob(os.path.join(SYM_DIR, '*.json'))}
    symbols = sorted(ohlcv_files & cache_syms)
    print(f"  Symbols: {len(symbols)}")

    # Accumulators per component
    pgr_g      = defaultdict(list)
    delta_g    = {'>0': [], '=0': [], '<0': []}
    setup_g    = {True: [], False: []}
    trend_g    = {True: [], False: []}
    dir_g      = {True: [], False: []}
    rr_g       = {'>=3': [], '2-3': [], '1-2': [], '0.5-1': [], '<0.5': [], '0': []}
    lt_g       = defaultdict(list)
    mf_g       = defaultdict(list)
    ob_g       = defaultdict(list)
    ind_g      = defaultdict(list)
    season_g   = {1.0: [], 0.5: [], 0.0: [], -0.5: [], -1.0: []}

    total = 0
    for i, sym in enumerate(symbols, 1):
        ohlcv_ts = load_ohlcv(sym)
        if not ohlcv_ts:
            continue
        all_dates = sorted(ohlcv_ts.keys())
        rows = process_symbol(sym, str(min_year), ohlcv_ts, all_dates)
        for r in rows:
            f = r['fwd10']
            pgr_g[r['pgr']].append(f)
            delta_g['>0' if r['pgr_delta'] > 0 else ('<0' if r['pgr_delta'] < 0 else '=0')].append(f)
            setup_g[r['setup_ok']].append(f)
            trend_g[r['trend_ok']].append(f)
            dir_g[r['dir_ok']].append(f)
            rr = r['rr']
            if rr == 0:      rr_g['0'].append(f)
            elif rr < 0.5:   rr_g['<0.5'].append(f)
            elif rr < 1.0:   rr_g['0.5-1'].append(f)
            elif rr < 2.0:   rr_g['1-2'].append(f)
            elif rr < 3.0:   rr_g['2-3'].append(f)
            else:             rr_g['>=3'].append(f)
            lt_g[r['lt_trend'] or 'NA'].append(f)
            mf_g[r['money_flow'] or 'NA'].append(f)
            ob_g[r['over_bt_sl'] or 'NA'].append(f)
            ind_g[r['industry'] or 'NA'].append(f)
            s = r['season']
            season_g[s].append(f)
            total += 1
        if i % 50 == 0:
            print(f"  ... {i}/{len(symbols)}")

    print(f"\n  Total: {total:,} observations, {FWD_W}d forward return\n")
    print("=" * 50)

    report("PGR corrected (1-5)", pgr_g)
    report("PGR delta (vs prev day)", delta_g)
    report("setup_ok (SMA20 + dir3)", setup_g)
    report("trend_ok (SMA20 only)", trend_g)
    report("dir_ok (close > close[3d ago])", dir_g)
    report("risk/reward ratio", rr_g)
    report("lt_trend", lt_g)
    report("money_flow", mf_g)
    report("overbought/oversold", ob_g)
    report("industry strength", ind_g)
    report("seasonality score", season_g)


if __name__ == "__main__":
    min_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    run(min_year)
