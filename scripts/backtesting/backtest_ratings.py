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
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



import json
import os
import sys
import glob
from collections import defaultdict
from scoring import (
    short_score as _short_score_fn,
    long_score as _long_score_fn,
    fibonacci_retracement_score as _fib_score,
    rsi_divergence_score as _rsi_div_score,
    rel_volume_bucket as _rel_vol_fn,
    market_regime as _market_regime,
)
from patterns import (
    candlestick_score as _cs_score,
    chart_pattern_score as _cp_score,
    momentum_pattern_score as _mo_score,
)

SYM_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
OHLCV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol_full")

STOP_DAYS      = 3
TARGET_LOOKBACK = 10
SMA_DAYS       = 20
FWD_WINDOWS    = [5, 10, 20]  # trading days

# Buying ratio component maps (mirrors _buying_ratio in powergauge.py)
PGR_MAP  = {1: -2.0, 2: -1.0, 3: 0.0, 4: 1.0, 5: 2.0}   # was ±4, halved
LT_MAP   = {'Strong': -1.0, 'Neutral': 0.0, 'Weak': 1.0}
MF_MAP   = {'Strong': 0.75, 'Neutral': 0.0, 'Weak': -0.75}
OB_MAP   = {'Optimal': 1.0, 'Early': 0.25, 'Neutral': 0.0, 'Wait': -0.25}  # Optimal raised, Wait softened
IND_MAP  = {'Strong': -0.5, 'Weak': 0.5}                  # flipped: Weak = recovery play

BUCKETS = [
    ("br <= -2",  lambda b: b <= -2),
    ("-2 to  0",  lambda b: -2 < b <= 0),
    (" 0 to  2",  lambda b: 0  < b <= 2),
    (" 2 to  4",  lambda b: 2  < b <= 4),
    ("br >=  4",  lambda b: b  > 4),
]

SHORT_BUCKETS = [
    ("score <= -4", lambda s: s <= -4),
    ("-4 to -2",    lambda s: -4 < s <= -2),
    ("-2 to  0",    lambda s: -2 < s <= 0),
    (" 0 to  2",    lambda s: 0  < s <= 2),
    (" 2 to  4",    lambda s: 2  < s <= 4),
    ("score >=  4", lambda s: s  > 4),
]


def load_ohlcv(symbol):
    path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f).get("Time Series (Daily)") or None


def _week_of_month(day: int) -> int:
    if day <= 7:  return 1
    if day <= 15: return 2
    if day <= 22: return 3
    return 4


def precompute_seasonality(ohlcv_ts):
    """Returns {(month, week): score} using historical 10-day returns per slot."""
    all_dates = sorted(ohlcv_ts.keys())
    date_idx  = {d: i for i, d in enumerate(all_dates)}

    wk_last = {}
    for d in all_dates:
        y, m, day = int(d[:4]), int(d[5:7]), int(d[8:10])
        w = _week_of_month(day)
        wk_last[(y, m, w)] = d

    from collections import defaultdict
    raw = defaultdict(list)
    for (y, m, w), start_date in wk_last.items():
        idx = date_idx[start_date]
        future_idx = idx + 10
        if future_idx >= len(all_dates):
            continue
        c_start  = float(ohlcv_ts[start_date].get('4. close', 0))
        c_future = float(ohlcv_ts[all_dates[future_idx]].get('4. close', 0))
        if c_start > 0 and c_future > 0:
            raw[(m, w)].append((c_future - c_start) / c_start * 100)

    result = {}
    for m in range(1, 13):
        for w in range(1, 5):
            rets = raw.get((m, w), [])
            if len(rets) < 3:
                result[(m, w)] = 0.0
                continue
            avg = sum(rets) / len(rets)
            if avg > 2.0:   result[(m, w)] =  1.0
            elif avg > 1.0: result[(m, w)] =  0.5
            elif avg > -1.0:result[(m, w)] =  0.0
            elif avg > -2.0:result[(m, w)] = -0.5
            else:           result[(m, w)] = -1.0
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
    date_str      = all_dates[idx]
    month         = int(date_str[5:7])
    day           = int(date_str[8:10])
    week          = _week_of_month(day)

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

    seasonality = seasonality_map.get((month, week), 0.0)
    
    # BR score
    br = 0.0
    br += PGR_MAP.get(pgr_corr, 0.0)
    if rr >= 3.0:   br += 2.0
    elif rr >= 2.0: br += 1.5
    elif rr >= 1.0: br += 1.0
    elif rr >= 0.5: br += 0.5
    elif rr > 0:    br += 0.0
    else:           br -= 1.0
    br += LT_MAP.get(lt_trend, 0.0)
    br += MF_MAP.get(money_flow, 0.0)
    br += OB_MAP.get(over_bt_sl, 0.0)
    br += IND_MAP.get(industry, 0.0)
    br += 0.25 if pgr_delta != 0 else 0.0
    br += seasonality
    br = round(max(-10.0, min(10.0, br)), 1)

    # Pattern scores (Phase A calibration)
    cs_val       = _cs_score(ohlcv_ts, date_str)
    cps_val, _   = _cp_score(ohlcv_ts, date_str)
    ms_val, _    = _mo_score(ohlcv_ts, date_str)

    # Short/Long scores
    fields = {
        'rel_vol': _rel_vol_fn(ohlcv_ts, date_str),
        'ob_os': over_bt_sl,
        'money_flow': money_flow,
        'industry_strength': industry,
        'lt_trend': lt_trend,
        'seasonality': seasonality,
        'market_regime': _market_regime(date_str),
        'fibonacci': _fib_score(ohlcv_ts, date_str),
        'rsi_divergence': _rsi_div_score(ohlcv_ts, date_str),
        'candlestick_score': cs_val,
        'chart_score': cps_val,
        'momentum_score': ms_val,
    }
    short = _short_score_fn(fields)
    long = _long_score_fn(fields)

    # Version without Fibonacci
    f_no_fib = fields.copy()
    f_no_fib['fibonacci'] = 0.0
    short_no_fib = _short_score_fn(f_no_fib)
    long_no_fib = _long_score_fn(f_no_fib)

    rsi_div = fields['rsi_divergence']

    # Version without RSI divergence
    f_no_div = fields.copy()
    f_no_div['rsi_divergence'] = 0.0
    short_no_div = _short_score_fn(f_no_div)
    long_no_div  = _long_score_fn(f_no_div)

    return br, short, long, short_no_fib, long_no_fib, rsi_div, short_no_div, long_no_div, cs_val, cps_val, ms_val


def process_symbol(symbol, min_year, ohlcv_ts, all_dates):
    """Returns list of (br, short, long, s_nf, l_nf, rsi_div, s_nd, l_nd, cs, cps, ms, fwd_5, fwd_10, fwd_20) tuples."""
    seasonality_map = precompute_seasonality(ohlcv_ts)
    ohlcv_date_set = set(all_dates)

    # Load all Chaikin cache files for this symbol, sorted by date
    pattern = os.path.join(SYM_DIR, symbol, f"{symbol}_*.json")
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

        br, short, long, s_nf, l_nf, rsi_div, s_nd, l_nd, cs, cps, ms = compute_br(data, prev_data, price, idx, all_dates, ohlcv_ts, seasonality_map)

        # Forward returns from OHLCV
        fwd = []
        for w in FWD_WINDOWS:
            future_idx = idx + w
            if future_idx < len(all_dates):
                fwd_close = float(ohlcv_ts[all_dates[future_idx]].get('4. close', 0))
                fwd.append((fwd_close - price) / price * 100 if fwd_close > 0 else None)
            else:
                fwd.append(None)

        results.append((br, short, long, s_nf, l_nf, rsi_div, s_nd, l_nd, cs, cps, ms, fwd[0], fwd[1], fwd[2]))
        prev_data, prev_date = data, date_str

    return results


def run(min_year=2023, max_symbols=None):
    print(f"\nBacktesting buying_ratio >= {min_year} ...")

    # Find symbols with both Chaikin cache and OHLCV
    ohlcv_files = {os.path.basename(f).replace('_daily.json', '')
                   for f in glob.glob(os.path.join(OHLCV_DIR, '*_daily.json'))}
    cache_syms  = {d for d in os.listdir(SYM_DIR)
                   if os.path.isdir(os.path.join(SYM_DIR, d))}
    symbols = sorted(ohlcv_files & cache_syms)
    if max_symbols:
        symbols = symbols[:max_symbols]
    print(f"  Symbols with both Chaikin + OHLCV: {len(symbols)}")

    # Collect all results
    br_buckets = {label: {w: [] for w in FWD_WINDOWS} for label, _ in BUCKETS}

    # Short buckets
    s_buckets = {label: {w: [] for w in FWD_WINDOWS} for label, _ in SHORT_BUCKETS}
    s_buckets_nf = {label: {w: [] for w in FWD_WINDOWS} for label, _ in SHORT_BUCKETS}

    # Long buckets
    l_buckets = {label: {w: [] for w in FWD_WINDOWS} for label, _ in SHORT_BUCKETS}
    l_buckets_nf = {label: {w: [] for w in FWD_WINDOWS} for label, _ in SHORT_BUCKETS}

    # RSI divergence raw spread (Phase A calibration)
    DIV_VALUES = [-1.0, -0.5, 0.0, 0.5, 1.0]
    div_buckets = {v: {w: [] for w in FWD_WINDOWS} for v in DIV_VALUES}

    # WITH/NO DIV comparison (Phase B)
    s_buckets_nd = {label: {w: [] for w in FWD_WINDOWS} for label, _ in SHORT_BUCKETS}
    l_buckets_nd = {label: {w: [] for w in FWD_WINDOWS} for label, _ in SHORT_BUCKETS}

    # Pattern Phase A: raw spread per score value
    PAT_BUCKETS = [
        ("score <= -1", lambda s: s <= -1.0),
        ("-1 to -0.5",  lambda s: -1.0 < s <= -0.5),
        ("-0.5 to 0",   lambda s: -0.5 < s <= 0.0),
        (" 0 to 0.5",   lambda s: 0.0  < s <= 0.5),
        ("0.5 to 1",    lambda s: 0.5  < s <= 1.0),
        ("score >= 1",  lambda s: s > 1.0),
    ]
    cs_buckets  = {label: {w: [] for w in FWD_WINDOWS} for label, _ in PAT_BUCKETS}
    cps_buckets = {label: {w: [] for w in FWD_WINDOWS} for label, _ in PAT_BUCKETS}
    ms_buckets  = {label: {w: [] for w in FWD_WINDOWS} for label, _ in PAT_BUCKETS}

    total_obs = 0

    for i, sym in enumerate(symbols, 1):
        ohlcv_ts = load_ohlcv(sym)
        if not ohlcv_ts:
            continue
        all_dates = sorted(ohlcv_ts.keys())
        rows = process_symbol(sym, str(min_year), ohlcv_ts, all_dates)
        for br, short, long, s_nf, l_nf, rsi_div, s_nd, l_nd, cs, cps, ms, f5, f10, f20 in rows:
            total_obs += 1
            fwds = {5: f5, 10: f10, 20: f20}

            # Bucket BR
            for label, test in BUCKETS:
                if test(br):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            br_buckets[label][w].append(fwds[w])
                    break
            
            # Bucket Short
            for label, test in SHORT_BUCKETS:
                if test(short):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            s_buckets[label][w].append(fwds[w])
                if test(s_nf):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            s_buckets_nf[label][w].append(fwds[w])

            # Bucket Long
            for label, test in SHORT_BUCKETS:
                if test(long):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            l_buckets[label][w].append(fwds[w])
                if test(l_nf):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            l_buckets_nf[label][w].append(fwds[w])

            # RSI divergence raw spread
            if rsi_div in div_buckets:
                for w in FWD_WINDOWS:
                    if fwds[w] is not None:
                        div_buckets[rsi_div][w].append(fwds[w])

            # WITH/NO DIV (short)
            for label, test in SHORT_BUCKETS:
                if test(s_nd):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            s_buckets_nd[label][w].append(fwds[w])

            # WITH/NO DIV (long)
            for label, test in SHORT_BUCKETS:
                if test(l_nd):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            l_buckets_nd[label][w].append(fwds[w])

            # Pattern Phase A: bucket each pattern score
            for label, test in PAT_BUCKETS:
                if test(cs):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            cs_buckets[label][w].append(fwds[w])
                if test(cps):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            cps_buckets[label][w].append(fwds[w])
                if test(ms):
                    for w in FWD_WINDOWS:
                        if fwds[w] is not None:
                            ms_buckets[label][w].append(fwds[w])

        if i % 50 == 0:
            print(f"  ... {i}/{len(symbols)} symbols processed")

    print(f"\n  Total observations: {total_obs:,}\n")

    def print_table(title, buckets, bucket_labels):
        print(f"\n--- {title} ---")
        header = f"  {'Bucket':<12}  {'Count':>6}  " + \
                 "  ".join(f"{'Avg '+str(w)+'d':>8}  {'Win%'+str(w)+'d':>8}" for w in FWD_WINDOWS)
        print(header)
        print("-" * len(header))
        for label, _ in bucket_labels:
            data = buckets[label]
            count = len(data[FWD_WINDOWS[1]]) if data[FWD_WINDOWS[1]] else 0
            cols = []
            for w in FWD_WINDOWS:
                rets = data[w]
                if not rets:
                    cols.extend(["N/A", "N/A"])
                else:
                    avg = sum(rets) / len(rets)
                    win = len([r for r in rets if r > 0]) / len(rets) * 100
                    cols.extend([f"{avg:>8.2f}%", f"{win:>8.1f}%"])
            print(f"  {label:<12}  {count:>6}  " + "  ".join(cols))

    print_table("BUYING RATIO (Control)", br_buckets, BUCKETS)
    print_table("SHORT10 (WITH FIB)", s_buckets, SHORT_BUCKETS)
    print_table("SHORT10 (NO FIB)", s_buckets_nf, SHORT_BUCKETS)
    print_table("LONG60 (WITH FIB)", l_buckets, SHORT_BUCKETS)
    print_table("LONG60 (NO FIB)", l_buckets_nf, SHORT_BUCKETS)
    print_table("SHORT10 (WITH DIV)", s_buckets, SHORT_BUCKETS)
    print_table("SHORT10 (NO DIV)", s_buckets_nd, SHORT_BUCKETS)
    print_table("LONG60 (WITH DIV)", l_buckets, SHORT_BUCKETS)
    print_table("LONG60 (NO DIV)", l_buckets_nd, SHORT_BUCKETS)

    # RSI Divergence raw factor spread (Phase A - calibration)
    print("\n--- RSI DIVERGENCE - Raw Factor Spread (Phase A) ---")
    print("  (measure 10d spread = avg at +1.0 minus avg at -1.0; use to calibrate weight)")
    div_header = f"  {'Bucket':>6}  {'Count':>6}  " + \
                 "  ".join(f"{'Avg '+str(w)+'d':>8}  {'Win%'+str(w)+'d':>8}" for w in FWD_WINDOWS)
    print(div_header)
    print("-" * len(div_header))
    for v in DIV_VALUES:
        data = div_buckets[v]
        count = len(data[FWD_WINDOWS[1]]) if data[FWD_WINDOWS[1]] else 0
        cols = []
        for w in FWD_WINDOWS:
            rets = data[w]
            if not rets:
                cols.extend(["     N/A", "     N/A"])
            else:
                avg = sum(rets) / len(rets)
                win = len([r for r in rets if r > 0]) / len(rets) * 100
                cols.extend([f"{avg:>8.2f}%", f"{win:>8.1f}%"])
        label = f"{v:+.1f}"
        print(f"  {label:>6}  {count:>6}  " + "  ".join(cols))

    avg10_bull = (sum(div_buckets[1.0][10]) / len(div_buckets[1.0][10])
                  if div_buckets[1.0][10] else None)
    avg10_bear = (sum(div_buckets[-1.0][10]) / len(div_buckets[-1.0][10])
                  if div_buckets[-1.0][10] else None)
    if avg10_bull is not None and avg10_bear is not None:
        spread = avg10_bull - avg10_bear
        print(f"\n  10d spread (+1.0 vs -1.0): {spread:.2f}%")
        if spread >= 3.0:
            print("  -> Suggested weight: +/-1.5 in short_score, +/-0.75 in long_score")
        elif spread >= 2.0:
            print("  -> Suggested weight: +/-1.0 in short_score, +/-0.5 in long_score")
        elif spread >= 1.0:
            print("  -> Suggested weight: +/-0.5 in short_score, +/-0.25 in long_score")
        else:
            print("  -> Spread < 1% - consider dropping this factor")

    def _pat_spread_summary(title, buckets):
        print(f"\n--- {title} (Phase A) ---")
        print("  (spread = high bucket avg 10d minus low bucket avg 10d; use to calibrate weight)")
        hdr = f"  {'Bucket':<14}  {'Count':>6}  " + \
              "  ".join(f"{'Avg '+str(w)+'d':>8}  {'Win%'+str(w)+'d':>8}" for w in FWD_WINDOWS)
        print(hdr)
        print("-" * len(hdr))
        avgs10 = []
        for label, _ in PAT_BUCKETS:
            data = buckets[label]
            count = len(data[10]) if data[10] else 0
            cols = []
            for w in FWD_WINDOWS:
                rets = data[w]
                if not rets:
                    cols.extend(["     N/A", "     N/A"])
                else:
                    avg = sum(rets) / len(rets)
                    win = len([r for r in rets if r > 0]) / len(rets) * 100
                    avgs10.append(avg)
                    cols.extend([f"{avg:>8.2f}%", f"{win:>8.1f}%"])
            print(f"  {label:<14}  {count:>6}  " + "  ".join(cols))
        if len(avgs10) >= 2:
            spread10 = max(avgs10) - min(avgs10)
            print(f"\n  10d spread (high vs low bucket): {spread10:.2f}%")
            if spread10 >= 3.0:
                print("  -> Suggested weight: +-1.5 in short_score, +-0.75 in long_score")
            elif spread10 >= 2.0:
                print("  -> Suggested weight: +-1.0 in short_score, +-0.5 in long_score")
            elif spread10 >= 1.0:
                print("  -> Suggested weight: +-0.5 in short_score, +-0.25 in long_score")
            else:
                print("  -> Spread < 1% - consider dropping this factor")

    _pat_spread_summary("CANDLESTICK SCORE - Raw Factor Spread", cs_buckets)
    _pat_spread_summary("CHART PATTERN SCORE - Raw Factor Spread", cps_buckets)
    _pat_spread_summary("MOMENTUM SCORE - Raw Factor Spread", ms_buckets)


if __name__ == "__main__":
    min_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    max_symbols = int(sys.argv[2]) if len(sys.argv) > 2 else None
    run(min_year, max_symbols)
