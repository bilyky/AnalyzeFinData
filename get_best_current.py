import datetime
import os
import sys
import json
import openpyxl

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powergauge

def get_best(date):
    session_id = "dummy"
    powergauge._build_cache_index()
    
    # Load symbols from Research sheet
    wb = openpyxl.load_workbook('Data/investment.xlsx', data_only=True)
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
            continue

    if not results:
        print(f"No results found for {date}")
        return

    # Filter for Bullish (4 or 5) and Setup OK (1)
    bullish = [r for r in results if r['pgr_val'] >= 4 and r['setup'] == 1]
    if not bullish:
        bullish = [r for r in results if r['pgr_val'] >= 4]

    # Sort by short10 (best for entry)
    best_short = sorted(bullish, key=lambda x: x['short10'], reverse=True)[:5]
    print(f"\nBest 5 Stocks for {date} (by Short10 score):")
    for i, r in enumerate(best_short, 1):
        print(f"{i}. {r['symbol']} (Short10: {r['short10']}, PGR: {r['pgr']}, BR: {r['br']})")

    # Sort by Buying Ratio
    best_br = sorted(bullish, key=lambda x: x['br'], reverse=True)[:5]
    print(f"\nBest 5 Stocks for {date} (by Buying Ratio):")
    for i, r in enumerate(best_br, 1):
        print(f"{i}. {r['symbol']} (BR: {r['br']}, Short10: {r['short10']}, PGR: {r['pgr']})")

    # Combined score (Short10 + BR)
    def combined_score(r):
        return r['short10'] + r['br']

    best_combined = sorted(bullish, key=combined_score, reverse=True)[:5]
    print(f"\nBest 5 Stocks for {date} (Combined Score):")
    for i, r in enumerate(best_combined, 1):
        print(f"{i}. {r['symbol']} (Score: {combined_score(r):.1f}, S10: {r['short10']}, BR: {r['br']}, PGR: {r['pgr']})")

if __name__ == "__main__":
    # Friday, May 29, 2026
    target_date = datetime.date(2026, 5, 29)
    get_best(target_date)
