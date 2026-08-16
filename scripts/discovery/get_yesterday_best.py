import datetime
import json
import os
import sys

import openpyxl


# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import powergauge


def get_best():
    # Friday close is the most recent market data for "yesterday evening" (Sunday)
    date = datetime.date(2026, 5, 29)
    session_id = "dummy"
    powergauge._build_cache_index()
    
    wb = openpyxl.load_workbook('Data/state_of_the_day.xlsx', data_only=True)
    ws_research = wb['Research']
    
    all_data = []
    for row in ws_research.iter_rows(min_row=2):
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

    # Filter for Bullish and Setup OK
    bullish_setup = [d for d in all_data if d['pgr_val'] >= 4 and d['setup']]
    
    # If none found, just Bullish
    if not bullish_setup:
        bullish_setup = [d for d in all_data if d['pgr_val'] >= 4]

    # Rank by combined Short10 + BR
    def combined_score(d):
        return d['short10'] + d['br']

    top_5 = sorted(bullish_setup, key=combined_score, reverse=True)[:5]
    
    for _i, _r in enumerate(top_5, 1):
        pass

    # Top 5 by Long60 (Position Score)
    top_long = sorted(bullish_setup, key=lambda x: x['long60'], reverse=True)[:5]
    for _i, _r in enumerate(top_long, 1):
        pass

    # Specifically Very Bullish (Bu+)
    very_bullish = [d for d in all_data if d['pgr_val'] == 5 and d['setup']]
    if not very_bullish:
        very_bullish = [d for d in all_data if d['pgr_val'] == 5]
    
    if very_bullish:
        top_bu_plus = sorted(very_bullish, key=combined_score, reverse=True)[:5]
        for _i, _r in enumerate(top_bu_plus, 1):
            pass

if __name__ == "__main__":
    get_best()
