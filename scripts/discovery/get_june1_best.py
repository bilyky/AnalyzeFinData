import datetime
import json
import os
import sys


# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import powergauge


def get_best(date):
    session_id = "dummy"
    powergauge._build_cache_index()
    
    # Get symbols that have a cache file for the target date
    symbol_dir = os.path.join("Data", "Symbol")
    cached_symbols = []
    for _root, _dirs, files in os.walk(symbol_dir):
        for f in files:
            if f.endswith(f"_{date}.json"):
                cached_symbols.append(f.rsplit('_', 1)[0])
    
    cached_symbols = list(set(cached_symbols))

    all_data = []
    for symbol in cached_symbols:
        try:
            pg = powergauge.get_symbol_data(symbol, date, True, session_id)
            if pg.price == -1: continue
            
            ohlcv_path = os.path.join("Data", "Symbol_full", f"{symbol}_daily.json")
            ohlcv_ts = None
            if os.path.exists(ohlcv_path):
                with open(ohlcv_path) as _f:
                    ohlcv_ts = json.load(_f).get('Time Series (Daily)')
            
            f = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
            
            pgr_val = pg.pgr_corrected_value if pg.pgr_corrected_value != 0 else pg.pgr_value

            all_data.append({
                'symbol': symbol,
                'short10': f['short_score'],
                'long60': f['long_score'],
                'br': f['buying_ratio'],
                'pgr': f['pgr'],
                'pgr_val': pgr_val,
                'setup': f['setup_ok']
            })
        except Exception:
            continue

    if not all_data:
        return

    # Filter for Bullish and Setup OK
    bullish_setup = [d for d in all_data if d['pgr_val'] >= 4 and d['setup']]
    if not bullish_setup:
        bullish_setup = [d for d in all_data if d['pgr_val'] >= 4]

    def combined_score(d):
        return d['short10'] + d['br']

    top_5 = sorted(bullish_setup, key=combined_score, reverse=True)[:5]
    
    for _i, _r in enumerate(top_5, 1):
        pass

    top_long = sorted(bullish_setup, key=lambda x: x['long60'], reverse=True)[:5]
    for _i, _r in enumerate(top_long, 1):
        pass

if __name__ == "__main__":
    target_date = datetime.date(2026, 6, 1)
    get_best(target_date)
