"""
Compare monthly vs week-of-month seasonality as predictors of 10d forward return.

Week-of-month buckets: 1=days 1-7, 2=days 8-15, 3=days 16-22, 4=days 23+
For each bucket we compute historical avg 10d return across all available years,
then assign the same ±1/±0.5/0/-0.5/-1 score as monthly seasonality.

Outputs:
  1. Monthly seasonality component win% (current model)
  2. Week-of-month seasonality component win% (candidate)
  3. Monthly × week heatmap (which combos are strongest/weakest)
  4. Overall BR bucket win% under week-of-month seasonality vs current

Usage: python backtest_seasonality.py [min_year]
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
FWD_W           = 10

PGR_MAP = {1: -4.0, 2: -2.0, 3: 0.0, 4: 2.0, 5: 4.0}
LT_MAP  = {'Strong': -1.0, 'Neutral': 0.0, 'Weak': 1.0}
MF_MAP  = {'Strong': 0.75, 'Neutral': 0.0, 'Weak': -0.75}
OB_MAP  = {'Optimal': 0.5, 'Early': 0.25, 'Neutral': 0.0, 'Wait': -0.5}
IND_MAP = {'Strong': 0.5, 'Weak': -0.5}

BUCKETS = [
    ("br <= -3",  lambda b: b <= -3),
    ("-3 to  0",  lambda b: -3 < b <= 0),
    (" 0 to  3",  lambda b: 0  < b <= 3),
    (" 3 to  6",  lambda b: 3  < b <= 6),
    ("br >=  6",  lambda b: b  > 6),
]

MONTH_NAMES = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def week_of_month(day: int) -> int:
    if day <= 7:  return 1
    if day <= 15: return 2
    if day <= 22: return 3
    return 4


def load_ohlcv(symbol):
    path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f).get("Time Series (Daily)") or None


def _score_from_avg(avg: float) -> float:
    if avg > 2.0:   return  1.0
    if avg > 1.0:   return  0.5
    if avg > -1.0:  return  0.0
    if avg > -2.0:  return -0.5
    return -1.0


def precompute_monthly(ohlcv_ts):
    """Returns {month: score} using month-end to month-end returns."""
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
        result[mo] = _score_from_avg(sum(returns) / len(returns)) if len(returns) >= 3 else 0.0
    return result


def precompute_weekly(ohlcv_ts):
    """Returns {(month, week): score} using 10-day forward returns anchored to each week-of-month."""
    all_dates = sorted(ohlcv_ts.keys())
    # Group dates by (year, month, week)
    wk_last = {}  # {(y, m, w): last_date_in_that_week}
    for d in all_dates:
        y, m, day = int(d[:4]), int(d[5:7]), int(d[8:10])
        w = week_of_month(day)
        wk_last[(y, m, w)] = d  # overwrite keeps last date of each week

    # For each (month, week) combo, collect 10d returns from all years
    returns_by_mw = defaultdict(list)
    date_idx = {d: i for i, d in enumerate(all_dates)}

    for (y, m, w), start_date in wk_last.items():
        idx = date_idx[start_date]
        future_idx = idx + FWD_W
        if future_idx >= len(all_dates):
            continue
        c_start  = float(ohlcv_ts[start_date].get('4. close', 0))
        c_future = float(ohlcv_ts[all_dates[future_idx]].get('4. close', 0))
        if c_start > 0 and c_future > 0:
            returns_by_mw[(m, w)].append((c_future - c_start) / c_start * 100)

    result = {}
    for m in range(1, 13):
        for w in range(1, 5):
            rets = returns_by_mw.get((m, w), [])
            result[(m, w)] = _score_from_avg(sum(rets) / len(rets)) if len(rets) >= 3 else 0.0
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
    ohlcv_date_set = set(all_dates)
    monthly_map = precompute_monthly(ohlcv_ts)
    weekly_map  = precompute_weekly(ohlcv_ts)

    pattern = os.path.join(SYM_DIR, f"{symbol}_*.json")
    cache_files = sorted(glob.glob(pattern))

    date_data = {}
    for path in cache_files:
        base = os.path.basename(path).replace('.json', '')
        date_str = base[len(symbol) + 1:]
        if date_str < str(min_year) or date_str not in ohlcv_date_set:
            continue
        date_data[date_str] = path

    if not date_data:
        return []

    results = []
    prev_data = None

    for date_str in sorted(date_data.keys()):
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
        day           = int(date_str[8:10])
        wk            = week_of_month(day)

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

        season_mo = monthly_map.get(month, 0.0)
        season_wk = weekly_map.get((month, wk), 0.0)

        # Base score (without any seasonality)
        base = 0.0
        base += PGR_MAP.get(pgr_corr, 0.0)
        rr_score = (2.0 if rr >= 3 else 1.5 if rr >= 2 else 1.0 if rr >= 1
                    else 0.5 if rr >= 0.5 else 0.0 if rr > 0 else -1.0)
        base += rr_score
        base += LT_MAP.get(lt_trend, 0.0)
        base += MF_MAP.get(money_flow, 0.0)
        base += OB_MAP.get(over_bt_sl, 0.0)
        base += IND_MAP.get(industry, 0.0)
        base += (0.25 if pgr_delta > 0 else -0.25 if pgr_delta < 0 else 0.0)

        br_monthly = round(max(-10.0, min(10.0, base + season_mo)), 1)
        br_weekly  = round(max(-10.0, min(10.0, base + season_wk)), 1)

        results.append({
            'month':      month,
            'week':       wk,
            'season_mo':  season_mo,
            'season_wk':  season_wk,
            'br_monthly': br_monthly,
            'br_weekly':  br_weekly,
            'fwd10':      fwd10,
        })
        prev_data = data

    return results


def report_component(label, groups):
    print(f"\n  {label}")
    print(f"  {'Value':<10} {'Count':>7} {'Avg10d':>8} {'Win%':>7}")
    print(f"  {'-'*38}")
    keys = sorted(groups.keys(), key=lambda x: (isinstance(x, str), x))
    for key in keys:
        vals = groups[key]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        win = 100 * sum(1 for v in vals if v > 0) / len(vals)
        print(f"  {str(key):<10} {len(vals):>7,} {avg:>+7.2f}% {win:>6.1f}%")


def report_buckets(label, bucket_vals):
    print(f"\n  {label}")
    print(f"  {'Bucket':<12} {'Count':>7} {'Avg10d':>8} {'Med10d':>7} {'Win%':>7}")
    print(f"  {'-'*48}")
    for lbl, _ in BUCKETS:
        vals = bucket_vals[lbl]
        if not vals:
            print(f"  {lbl:<12} {'N/A':>7}")
            continue
        avg = sum(vals) / len(vals)
        med = sorted(vals)[len(vals) // 2]
        win = 100 * sum(1 for v in vals if v > 0) / len(vals)
        print(f"  {lbl:<12} {len(vals):>7,} {avg:>+7.2f}% {med:>+6.2f}% {win:>6.1f}%")
    wins = [100 * sum(1 for v in bucket_vals[lbl] if v > 0) / len(bucket_vals[lbl])
            for lbl, _ in BUCKETS if bucket_vals[lbl]]
    mono = all(wins[i] <= wins[i+1] for i in range(len(wins)-1))
    print(f"  Win% monotonic: {'PASS' if mono else 'FAIL'}  {' < '.join(f'{w:.1f}' for w in wins)}")


def run(min_year=2023):
    print(f"\nSeasonality comparison >= {min_year} ...")

    ohlcv_files = {os.path.basename(f).replace('_daily.json', '')
                   for f in glob.glob(os.path.join(OHLCV_DIR, '*_daily.json'))}
    cache_syms  = {os.path.basename(f).rsplit('_', 1)[0]
                   for f in glob.glob(os.path.join(SYM_DIR, '*.json'))}
    symbols = sorted(ohlcv_files & cache_syms)
    print(f"  Symbols: {len(symbols)}")

    # Accumulators
    mo_score_g  = defaultdict(list)   # monthly score value -> fwd10
    wk_score_g  = defaultdict(list)   # weekly score value -> fwd10
    mo_raw_g    = defaultdict(list)   # month number -> fwd10
    mw_raw_g    = defaultdict(list)   # (month, week) -> fwd10
    wk_raw_g    = defaultdict(list)   # week number -> fwd10

    bkt_monthly = {lbl: [] for lbl, _ in BUCKETS}
    bkt_weekly  = {lbl: [] for lbl, _ in BUCKETS}

    total = 0
    for i, sym in enumerate(symbols, 1):
        ohlcv_ts = load_ohlcv(sym)
        if not ohlcv_ts:
            continue
        all_dates = sorted(ohlcv_ts.keys())
        rows = process_symbol(sym, str(min_year), ohlcv_ts, all_dates)
        for r in rows:
            f = r['fwd10']
            mo_score_g[r['season_mo']].append(f)
            wk_score_g[r['season_wk']].append(f)
            mo_raw_g[r['month']].append(f)
            mw_raw_g[(r['month'], r['week'])].append(f)
            wk_raw_g[r['week']].append(f)
            for lbl, test in BUCKETS:
                if test(r['br_monthly']):
                    bkt_monthly[lbl].append(f)
                    break
            for lbl, test in BUCKETS:
                if test(r['br_weekly']):
                    bkt_weekly[lbl].append(f)
                    break
            total += 1
        if i % 50 == 0:
            print(f"  ... {i}/{len(symbols)}")

    print(f"\n  Total: {total:,} observations\n")
    print("=" * 60)

    # 1. Score-level comparison (same ±1/0.5/0 scoring, different period)
    report_component("Monthly seasonality SCORE -> 10d win%", mo_score_g)
    report_component("Week-of-month seasonality SCORE -> 10d win%", wk_score_g)

    # 2. Raw signal: does week-of-month have more variance?
    report_component("Week-of-month (1-4, all months pooled) -> 10d win%", wk_raw_g)

    # 3. Month × Week heatmap (win%)
    print(f"\n  Month × Week win% heatmap (10d forward return)")
    print(f"  {'':>5}", end="")
    for w in range(1, 5):
        print(f"  Wk{w:>1}", end="")
    print(f"  {'Month':>6}")
    print(f"  {'-'*35}")
    for m in range(1, 13):
        print(f"  {MONTH_NAMES[m]:<5}", end="")
        month_vals = mo_raw_g[m]
        for w in range(1, 5):
            vals = mw_raw_g.get((m, w), [])
            if vals:
                win = 100 * sum(1 for v in vals if v > 0) / len(vals)
                print(f"  {win:>4.1f}", end="")
            else:
                print(f"  {'N/A':>4}", end="")
        # Month total
        if month_vals:
            mwin = 100 * sum(1 for v in month_vals if v > 0) / len(month_vals)
            print(f"  {mwin:>5.1f}%")
        else:
            print()

    # 4. Overall BR bucket comparison
    report_buckets("BR buckets using MONTHLY seasonality (current)", bkt_monthly)
    report_buckets("BR buckets using WEEK-OF-MONTH seasonality (candidate)", bkt_weekly)


if __name__ == "__main__":
    min_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    run(min_year)
