import datetime
import os
import sys


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
            powergauge.get_symbol_data(symbol, date, True, session_id)
        except Exception:
            pass

if __name__ == "__main__":
    debug()
