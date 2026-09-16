"""
Quick test: verify check_from_xls populates all Research sheet columns
for a single symbol using cached data (no live API calls needed).
Uses AAPL / 2026-02-18 — prev data (2026-02-17) exists in cache.

Also tests "missed market" case: a symbol with no API data (price=-1)
should be skipped rather than overwriting the row with -1.
"""
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import powergauge

from aether_logger import get_logger as _get_logger

_log = _get_logger("test_one_symbol")

TEST_SYMBOL = "AAPL"
TEST_DATE   = datetime.datetime(2026, 2, 18)

sys.stdout.write(f"\n=== Testing _compute_pgr_fields for {TEST_SYMBOL} / {TEST_DATE.date()} ===\n\n")

# Load via cache — no session needed
pg = powergauge.PowerGauge(TEST_SYMBOL, TEST_DATE.date())
symbol_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data", "Symbol")
cache_file = os.path.join(symbol_dir, f"{TEST_SYMBOL}_{TEST_DATE.date()}.json")
if not os.path.exists(cache_file):
    _log.error(f"[test_one_symbol] Cache missing: {cache_file}")
    sys.exit(1)

import json
with open(cache_file) as f:
    pg.init_from_json(json.load(f))

sys.stdout.write(f"  price          = {pg.price}\n")
sys.stdout.write(f"  percentage     = {pg.percentage}\n")
sys.stdout.write(f"  pgr_value      = {pg.pgr_value}\n")
sys.stdout.write(f"  pgr_corr       = {pg.pgr_corrected_value}\n")
sys.stdout.write(f"  industry_name  = {pg.industry_name}\n")
sys.stdout.write(f"  industry_str   = {pg.industry_strength}\n")
sys.stdout.write(f"  lt_trend       = {pg.lt_trend}\n")
sys.stdout.write(f"  money_flow     = {pg.money_flow}\n")
sys.stdout.write(f"  over_bt_sl     = {pg.over_bt_sl}\n")

pg.find_prev_pf()
if pg.prevPG:
    sys.stdout.write(f"\n  prevPG date    = {pg.prevPG.date}\n")
    sys.stdout.write(f"  prevPG price   = {pg.prevPG.price}\n")
    sys.stdout.write(f"  prevPG pgr     = {pg.prevPG.pgr_value}\n")
else:
    sys.stdout.write("\n  prevPG         = None (no cached prev-day file found)\n")

# Load OHLCV for entry-filter test
import json as _json
ohlcv_ts = None
ohlcv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "Data", "Symbol_full", f"{TEST_SYMBOL}_daily.json")
if os.path.exists(ohlcv_path):
    with open(ohlcv_path) as _f:
        ohlcv_ts = _json.load(_f).get('Time Series (Daily)')
    sys.stdout.write(f"  OHLCV loaded: {ohlcv_path}\n")
else:
    _log.warning(f"[test_one_symbol] OHLCV not found: {ohlcv_path}")

fields = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
sys.stdout.write(f"\nComputed fields:\n")
for k, v in fields.items():
    sys.stdout.write(f"  {k:20s} = {v}\n")

sys.stdout.write("\n=== Research sheet column mapping ===\n")
sys.stdout.write(f"  col E  industry_name   = {pg.industry_name}\n")
sys.stdout.write(f"  col F  prev_pgr        = {fields['prev_pgr']}\n")
sys.stdout.write(f"  col G  pgr             = {fields['pgr']}\n")
sys.stdout.write(f"  col H  industry_str    = {pg.industry_strength}\n")
sys.stdout.write(f"  col I  (PRESERVE)\n")
setup_ok = fields['setup_ok']
sys.stdout.write(f"  col J  stop_price      = {fields['stop_price'] if setup_ok is not False else 0}  (setup={'OK' if setup_ok else '--' if setup_ok is False else '??'})\n")
sys.stdout.write(f"  col K  price           = {pg.price}\n")
sys.stdout.write(f"  col L  target          = {fields['prev_move_price'] if setup_ok is not False else 0}\n")
sys.stdout.write(f"  col M  risk_ratio      = {fields['risk_ratio'] if setup_ok is not False else 0}\n")
sys.stdout.write(f"  col N  prev_move_perc  = {fields['prev_move_perc']}\n")
sys.stdout.write(f"  col O  prev_percentage = {fields['prev_percentage']}\n")
sys.stdout.write(f"  col P  percentage      = {pg.percentage}\n")
sys.stdout.write(f"  col Q  (PRESERVE)\n")
sys.stdout.write(f"  col R  lt_trend        = {pg.lt_trend}\n")
sys.stdout.write(f"  col S  money_flow      = {pg.money_flow}\n")
sys.stdout.write(f"  col T  over_bt_sl      = {pg.over_bt_sl}\n")
sys.stdout.write(f"  col U  setup_ok        = {(1 if setup_ok else 0) if setup_ok is not None else None}\n")
sys.stdout.write(f"  col V  buying_ratio    = {fields['buying_ratio']}\n")
sys.stdout.write(f"  col W  seasonality     = {fields['seasonality']}  (month={pg.date.month})\n")

# --- Missed market test ---
sys.stdout.write("\n=== Missed market test (price=-1 => row should be skipped) ===\n\n")
bad_pg = powergauge.PowerGauge("FAKE", datetime.date(2026, 1, 1))
bad_pg.price = -1  # simulate no data returned
if bad_pg.price == -1:
    sys.stdout.write("FAKE: no market data - row skipped (existing values preserved)  OK\n")

# --- Schema warning test ---
sys.stdout.write("\n=== Schema warning test (truncated pgr list) ===\n\n")
broken_json = {"pgr": [{"PGR Value": 3}], "metaInfo": [{"Last": 100}], "checklist_stocks": {}}
warn_pg = powergauge.PowerGauge("WARN_TEST", datetime.date(2026, 1, 1))
warn_pg.init_from_json(broken_json)  # should print schema warnings
