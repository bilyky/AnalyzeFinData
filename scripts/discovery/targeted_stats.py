import datetime
import json
import os
import sys


# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import powergauge


def get_targeted_stats():
    date = datetime.date(2026, 5, 26)
    session_id = "dummy"
    powergauge._build_cache_index()
    
    candidates = [
        'SNPS', 'ALLT', 'TROW', 'F', 'TLN', 
        'RPD', 'AIQ', 'MAGS', 'DIOD',
        'FCX', 'MU', 'COPX', 'ET', 'GRID',
        'ARLP', 'MOH', 'GNE', 'LBRT', 'TSLA', 'OXY', 'FNKO',
        'PRTS', 'DY', 'ADBE', 'AGR', 'ALB', 'COP', 'FDX'
    ]
    candidates = list(set(candidates))
    
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
            pgr_val = pg.pgr_corrected_value if pg.pgr_corrected_value != 0 else pg.pgr_value

            results.append({
                'symbol': symbol,
                'short10': f['short_score'],
                'br': f['buying_ratio'],
                'pgr': f['pgr'],
                'pgr_val': pgr_val
            })
        except Exception:
            continue

    for _i, r in enumerate(sorted(results, key=lambda x: x['short10'], reverse=True)[:5], 1):
        pass

    for _i, r in enumerate(sorted(results, key=lambda x: x['br'], reverse=True)[:5], 1):
        pass

    e_syms = ['ARLP', 'MOH', 'GNE', 'LBRT', 'TSLA', 'OXY', 'FNKO']
    for sym in e_syms:
        r = next((x for x in results if x['symbol'] == sym), None)
        if r: pass

    g_syms = ['PRTS', 'DY', 'ADBE', 'AGR', 'ALB', 'COP', 'FDX']
    for sym in g_syms:
        r = next((x for x in results if x['symbol'] == sym), None)
        if r: pass

if __name__ == "__main__":
    get_targeted_stats()
