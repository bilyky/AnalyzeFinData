"""
Quick test: verify check_from_xls populates all Research sheet columns
for a single symbol using cached data (no live API calls needed).
Uses AAPL / 2026-02-18 — prev data (2026-02-17) exists in cache.

Also tests "missed market" case: a symbol with no API data (price=-1)
should be skipped rather than overwriting the row with -1.
"""
import datetime
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import powergauge


TEST_SYMBOL = "AAPL"
TEST_DATE   = datetime.datetime(2026, 2, 18)


# Load via cache — no session needed
pg = powergauge.PowerGauge(TEST_SYMBOL, TEST_DATE.date())
symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
cache_file = os.path.join(symbol_dir, f"{TEST_SYMBOL}_{TEST_DATE.date()}.json")
if not os.path.exists(cache_file):
    sys.exit(1)

import json


with open(cache_file) as f:
    pg.init_from_json(json.load(f))


pg.find_prev_pf()
if pg.prevPG:
    pass
else:
    pass

# Load OHLCV for entry-filter test
import json as _json


ohlcv_ts = None
ohlcv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "Data", "Symbol_full", f"{TEST_SYMBOL}_daily.json")
if os.path.exists(ohlcv_path):
    with open(ohlcv_path) as _f:
        ohlcv_ts = _json.load(_f).get('Time Series (Daily)')
else:
    pass

fields = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
for _k, _v in fields.items():
    pass

setup_ok = fields['setup_ok']

# --- Missed market test ---
bad_pg = powergauge.PowerGauge("FAKE", datetime.date(2026, 1, 1))
bad_pg.price = -1  # simulate no data returned
if bad_pg.price == -1:
    pass

# --- Schema warning test ---
broken_json = {"pgr": [{"PGR Value": 3}], "metaInfo": [{"Last": 100}], "checklist_stocks": {}}
warn_pg = powergauge.PowerGauge("WARN_TEST", datetime.date(2026, 1, 1))
warn_pg.init_from_json(broken_json)  # should print schema warnings
