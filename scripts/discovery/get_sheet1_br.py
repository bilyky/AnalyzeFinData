import datetime
import os
import sys
import json

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powergauge

def get_sheet1_br():
    date = datetime.date(2026, 5, 26)
    session_id = "dummy"
    powergauge._build_cache_index()
    
    candidates = ['ARLP', 'MOH', 'GNE', 'LBRT', 'TSLA', 'OXY', 'FNKO', 'PRTS', 'DY', 'ADBE', 'AGR', 'ALB', 'COP', 'FDX']
    
    results = []
    for symbol in candidates:
        try:
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
                'pgr': f['pgr']
            })
        except Exception:
            continue

    print("Sheet1 Symbols Stats:")
    for r in sorted(results, key=lambda x: x['br'], reverse=True):
        print(f"{r['symbol']} (BR: {r['br']}, S10: {r['short10']}, PGR: {r['pgr']})")

if __name__ == "__main__":
    get_sheet1_br()
