import datetime
import os
import sys
import openpyxl
import json

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powergauge

def get_picks():
    xlsx_file = 'Data/investment.xlsx'
    wb = openpyxl.load_workbook(xlsx_file, data_only=True)
    ws = wb['Research']
    
    date = datetime.date(2026, 5, 26)
    session_id = "dummy"
    
    picks_data = []
    powergauge._build_cache_index()
    
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
            
            picks_data.append({
                'symbol':   symbol,
                'short10':  f['short_score'],
                'long60':   f['long_score'],
                'pgr':      f['pgr'],
                'pgr_val':  f['pgr_value'],
                'setup':    (1 if f['setup_ok'] else 0) if f['setup_ok'] is not None else None,
            })
        except Exception:
            continue

    # Filter for Bullish PGR (Bu or Bu+, which are values 4 and 5)
    bullish_picks = [d for d in picks_data if d['pgr_val'] >= 4]

    print("TOP 5 BULLISH STOCKS (PGR Bu/Bu+) by Short10:")
    top_short = sorted(bullish_picks, key=lambda x: x['short10'], reverse=True)[:5]
    for i, p in enumerate(top_short, 1):
        print(f"{i}. {p['symbol']} (Short10: {p['short10']}, PGR: {p['pgr']})")

    print("\nTOP 5 BULLISH STOCKS (PGR Bu/Bu+) by Long60:")
    top_long = sorted(bullish_picks, key=lambda x: x['long60'], reverse=True)[:5]
    for i, p in enumerate(top_long, 1):
        print(f"{i}. {p['symbol']} (Long60: {p['long60']}, PGR: {p['pgr']})")

if __name__ == "__main__":
    get_picks()
