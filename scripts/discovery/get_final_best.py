import datetime
import os
import sys
import json

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powergauge

def get_best():
    date = datetime.date(2026, 5, 26)
    session_id = "dummy"
    powergauge._build_cache_index()
    
    # Load symbols from Research sheet
    import openpyxl
    wb = openpyxl.load_workbook('Data/state_of_the_day.xlsx', data_only=True)
    ws = wb['Research']
    
    results = []
    for row in ws.iter_rows(min_row=2):
        symbol = row[3].value
        if not symbol: continue
        try:
            pg = powergauge.get_symbol_data(symbol, date, True, session_id)
            if pg.price == -1: continue
            
            ohlcv_path = os.path.join("Data", "Symbol_full", f"{symbol}_daily.json")
            ohlcv_ts = None
            if os.path.exists(ohlcv_path):
                with open(ohlcv_path) as _f:
                    ohlcv_ts = json.load(_f).get('Time Series (Daily)')
            
            f = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
            
            # Use pgr_corrected_value or pgr_value
            pgr_val = pg.pgr_corrected_value if pg.pgr_corrected_value != 0 else pg.pgr_value

            results.append({
                'symbol': symbol,
                'short10': f['short_score'],
                'long60': f['long_score'],
                'pgr': f['pgr'],
                'pgr_val': pgr_val,
                'br': f['buying_ratio'],
                'setup': (1 if f['setup_ok'] else 0) if f['setup_ok'] is not None else None
            })
        except Exception as e:
            # print(f"Error for {symbol}: {e}")
            continue

    print(f"Total results with data: {len(results)}")
    
    # Filter for Bullish (4 or 5) and Setup OK (1)
    bullish = [r for r in results if r['pgr_val'] >= 4 and r['setup'] == 1]
    if not bullish:
        print("No bullish symbols passed setup filter. Showing all bullish.")
        bullish = [r for r in results if r['pgr_val'] >= 4]

    # Sort by short10
    best_short = sorted(bullish, key=lambda x: x['short10'], reverse=True)[:5]
    print("\nBest 5 Bullish by Short10 (Entry Score):")
    for i, r in enumerate(best_short, 1):
        print(f"{i}. {r['symbol']} (Short10: {r['short10']}, PGR: {r['pgr']}, BR: {r['br']})")

    # Sort by long60
    best_long = sorted(bullish, key=lambda x: x['long60'], reverse=True)[:5]
    print("\nBest 5 Bullish by Long60 (Position Score):")
    for i, r in enumerate(best_long, 1):
        print(f"{i}. {r['symbol']} (Long60: {r['long60']}, PGR: {r['pgr']}, BR: {r['br']})")

if __name__ == "__main__":
    get_best()
