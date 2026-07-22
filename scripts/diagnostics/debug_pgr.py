import datetime
import os
import sys
import json

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import powergauge

def debug():
    date = datetime.date(2026, 5, 26)
    symbols = ['FCX', 'MU', 'COPX', 'ET', 'GRID']
    session_id = "dummy"
    powergauge._build_cache_index()
    
    for symbol in symbols:
        try:
            pg = powergauge.get_symbol_data(symbol, date, True, session_id)
            print(f"{symbol}: price={pg.price}, pgr_val={pg.pgr_value}, pgr_corr={pg.pgr_corrected_value}")
        except Exception as e:
            print(f"{symbol}: Error {e}")

if __name__ == "__main__":
    debug()
