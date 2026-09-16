import datetime
import os
import sys
import json

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import powergauge

from aether_logger import get_logger as _get_logger

_log = _get_logger("debug_pgr")

def debug():
    date = datetime.date(2026, 5, 26)
    symbols = ['FCX', 'MU', 'COPX', 'ET', 'GRID']
    session_id = "dummy"
    powergauge._build_cache_index()
    
    for symbol in symbols:
        try:
            pg = powergauge.get_symbol_data(symbol, date, True, session_id)
            sys.stdout.write(f"{symbol}: price={pg.price}, pgr_val={pg.pgr_value}, pgr_corr={pg.pgr_corrected_value}\n")
        except Exception as e:
            _log.error(f"[debug_pgr] {symbol}: Error {e}", exc_info=True)

if __name__ == "__main__":
    debug()
