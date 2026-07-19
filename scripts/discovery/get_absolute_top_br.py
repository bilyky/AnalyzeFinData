import datetime
import os
import sys
import json
import openpyxl

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powergauge

def get_absolute_top_br():
    date = datetime.date(2026, 5, 26)
    session_id = "dummy"
    powergauge._build_cache_index()
    
    # Get all symbols that have a cache file for 5/26
    symbol_dir = os.path.join("Data", "Symbol")
    cached_symbols = []
    for root, dirs, files in os.walk(symbol_dir):
        for f in files:
            if f.endswith(f"_{date}.json"):
                cached_symbols.append(f.rsplit('_', 1)[0])
    
    cached_symbols = list(set(cached_symbols))
    print(f"Found {len(cached_symbols)} symbols with cache for {date}")

    results = []
    for symbol in cached_symbols:
        try:
            # Note: get_symbol_data with prefer_cache=True should be fast
            pg = powergauge.get_symbol_data(symbol, date, True, session_id)
            if pg.price == -1: continue
            
            ohlcv_path = os.path.join("Data", "Symbol_full", f"{symbol}_daily.json")
            ohlcv_ts = None
            if os.path.exists(ohlcv_path):
                with open(ohlcv_path) as _f:
                    ohlcv_ts = json.load(_f).get('Time Series (Daily)')
            
            f = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
            
            results.append({
                'symbol': symbol,
                'br': f['buying_ratio'],
                'short10': f['short_score'],
                'pgr': f['pgr'],
                'setup': f['setup_ok']
            })
        except Exception:
            continue

    # Rank by BR
    top_br = sorted(results, key=lambda x: x['br'], reverse=True)[:10]
    print("\nAbsolute Top 10 by Buying Ratio (Across all cached symbols):")
    for i, r in enumerate(top_br, 1):
        setup_str = "OK" if r['setup'] else ("--" if r['setup'] is False else "??")
        print(f"{i}. {r['symbol']} (BR: {r['br']}, S10: {r['short10']}, PGR: {r['pgr']}, Setup: {setup_str})")

if __name__ == "__main__":
    get_absolute_top_br()
