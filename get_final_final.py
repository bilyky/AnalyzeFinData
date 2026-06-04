import datetime
import os
import sys
import json
import openpyxl

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powergauge

def get_top_br_with_setup():
    date = datetime.date(2026, 5, 26)
    session_id = "dummy"
    powergauge._build_cache_index()
    
    symbol_dir = os.path.join("Data", "Symbol")
    cached_symbols = []
    for root, dirs, files in os.walk(symbol_dir):
        for f in files:
            if f.endswith(f"_{date}.json"):
                cached_symbols.append(f.rsplit('_', 1)[0])
    
    cached_symbols = list(set(cached_symbols))

    results = []
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
            
            if f['setup_ok'] == True:
                results.append({
                    'symbol': symbol,
                    'br': f['buying_ratio'],
                    'short10': f['short_score'],
                    'pgr': f['pgr']
                })
        except Exception:
            continue

    # Rank by BR
    top_br = sorted(results, key=lambda x: x['br'], reverse=True)[:5]
    print("Top 5 by Buying Ratio (Setup OK):")
    for i, r in enumerate(top_br, 1):
        print(f"{i}. {r['symbol']} (BR: {r['br']}, S10: {r['short10']}, PGR: {r['pgr']})")

    # Rank by Short10
    top_s10 = sorted(results, key=lambda x: x['short10'], reverse=True)[:5]
    print("\nTop 5 by Short10 (Setup OK):")
    for i, r in enumerate(top_s10, 1):
        print(f"{i}. {r['symbol']} (S10: {r['short10']}, BR: {r['br']}, PGR: {r['pgr']})")

if __name__ == "__main__":
    get_top_br_with_setup()
