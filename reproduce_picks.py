import datetime
import os
import sys
import openpyxl
import json

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powergauge

def get_picks():
    xlsx_file = 'Data/state_of_the_day.xlsx'
    if not os.path.exists(xlsx_file):
        print(f"File {xlsx_file} not found")
        return
        
    wb = openpyxl.load_workbook(xlsx_file, data_only=True)
    ws = wb['Research']
    
    date = datetime.date(2026, 5, 26)
    session_id = "dummy" # Should be fine for cache-only
    
    picks_data = []
    
    # Pre-build cache index to speed up find_prev_pf
    powergauge._build_cache_index()
    
    symbols_checked = 0
    for row in ws.iter_rows(min_row=2):
        symbol = row[3].value
        if not symbol: continue
        
        symbols_checked += 1
        try:
            # Note: get_symbol_data(symbol, date, prefer_cache, session_id)
            pg = powergauge.get_symbol_data(symbol, date, True, session_id)
            if pg.price == -1:
                continue
            
            # Load OHLCV
            ohlcv_path = os.path.join("Data", "Symbol_full", f"{symbol}_daily.json")
            ohlcv_ts = None
            if os.path.exists(ohlcv_path):
                with open(ohlcv_path) as _f:
                    ohlcv_ts = json.load(_f).get('Time Series (Daily)')
            
            f = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
            setup_ok = f['setup_ok']
            
            picks_data.append({
                'symbol':   symbol,
                'short10':  f['short_score'],
                'long60':   f['long_score'],
                'pgr':      f['pgr'],
                'setup':    (1 if setup_ok else 0) if setup_ok is not None else None,
            })
        except Exception as e:
            # print(f"Error for {symbol}: {e}")
            continue

    print(f"Checked {symbols_checked} symbols, found {len(picks_data)} with data.")

    if not picks_data:
        print("No picks data found")
        return

    def top5(data, key, reverse):
        # Filter for setup = 1
        filtered = [d for d in data if d['setup'] == 1]
        if not filtered:
            print(f"Warning: No symbols passed setup filter for {key}")
            filtered = data # Fallback
        return sorted(filtered, key=lambda x: x.get(key, 0), reverse=reverse)[:5]

    print("\nTOP 5 BUY -- Short10 (10-day entry score)")
    for i, p in enumerate(top5(picks_data, 'short10', True), 1):
        print(f"{i}. {p['symbol']} (Score: {p['short10']}, PGR: {p['pgr']})")

    print("\nTOP 5 BUY -- Long60 (60-day position score)")
    for i, p in enumerate(top5(picks_data, 'long60', True), 1):
        print(f"{i}. {p['symbol']} (Score: {p['long60']}, PGR: {p['pgr']})")

if __name__ == "__main__":
    get_picks()
