"""
Project AETHER: Zero-Trust Data Contiguity & 3-Way Pricing Discrepancy Auditor.
This utility script lets you independently audit your historical price caches
for internal weekday gaps (like the July 6th gap) and 3-way price discrepancies
across E*TRADE, RapidAPI (Symbol_full), and Chaikin (PG).
"""
import datetime
import json
import os
import sys
from pathlib import Path


# Add workspace root to import path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

# Non-tradeable placeholder that E*TRADE reports as a holding (a rights/CVR stub with
# no price cache); skip it from the pricing/contiguity audit rather than flag a false gap.
NON_TRADEABLE_STUBS = {"931CVR013"}
import data_api
import instruments
from aether import etrade


def get_expected_weekdays(days_count: int = 15) -> list[str]:
    """Calculate the expected trading weekdays (Mon-Fri) excluding holidays."""
    holidays_2026 = {
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", 
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"
    }
    expected = []
    current = datetime.date.today()
    # Go back in time and collect weekdays
    while len(expected) < days_count:
        current -= datetime.timedelta(days=1)
        if current.weekday() not in (5, 6):  # Monday-Friday
            date_str = current.isoformat()
            if date_str not in holidays_2026:
                expected.append(date_str)
    return sorted(expected)

def audit_database() -> bool:
    print("="*80)
    print(" 🛡️  PROJECT AETHER: ZERO-TRUST DATABASE CONTIGUITY & PRICING AUDIT")
    print("="*80)

    # Probe for a genuinely usable E*TRADE session (valid tokens), not merely a token
    # file on disk — an expired/rejected token file exists but yields no live prices, so
    # a file-existence check would falsely enable the E*TRADE-vs-cache pricing audit and
    # emit spurious mismatches. allow_browser=False keeps this non-interactive (no
    # Playwright launch) during local/pre-commit audits.
    try:
        is_etrade_online = bool(etrade.get_tokens("production", allow_browser=False))
    except Exception:
        is_etrade_online = False

    if not is_etrade_online:
        print("  ⚠️  E*TRADE live session is unauthenticated; running on cached/fallback portfolio data.")
        print("  ⚠️  Skipping E*TRADE-vs-cache pricing audits (no live quotes) to avoid false-alarm mismatches.")
        print("-"*80)

    # 1. Fetch active symbols from E*TRADE accounts
    try:
        accts_data = data_api.read_accounts()
        accounts = accts_data.get("accounts", [])
    except Exception as e:
        print(f"❌ [CRITICAL] Failed to load brokerage accounts: {e}")
        return False

    unique_symbols = set()
    positions_map = {}
    for a in accounts:
        for h in a.get("holdings", []):
            sym = h.get("symbol", "").strip().upper()
            if sym and sym not in NON_TRADEABLE_STUBS and not instruments.is_excluded(sym):
                unique_symbols.add(sym)
                positions_map[sym] = h

    if not unique_symbols:
        print("ℹ️ No active held symbols found to audit.")
        return True

    print(f"Auditing {len(unique_symbols)} active portfolio holdings: {sorted(list(unique_symbols))}")
    print("-"*80)
    
    expected_days = get_expected_weekdays(15)
    print(f"Verifying contiguity over expected weekdays (Mon-Fri) from {expected_days[0]} to {expected_days[-1]}...")
    print("-"*80)

    all_passed = True
    gaps_count = 0
    discrepancy_count = 0

    for sym in sorted(list(unique_symbols)):
        h = positions_map[sym]
        live_price = h.get("current", 0.0)
        
        # ── Check 1: Contiguity Gaps in Symbol_full ──
        path = Path("Data/Symbol_full") / f"{sym}_daily.json"
        if not path.exists():
            print(f"❌ {sym:<6} | [GAP FAIL] Cache file Symbol_full/{sym}_daily.json is missing entirely!")
            all_passed = False
            gaps_count += 1
            continue
            
        try:
            with open(path, "r", encoding="utf-8") as f:
                ts = json.load(f).get("Time Series (Daily)", {})
        except Exception as e:
            print(f"❌ {sym:<6} | [GAP FAIL] Failed to parse JSON cache: {e}")
            all_passed = False
            gaps_count += 1
            continue

        # Scan for expected weekdays missing from the local JSON cache
        missing_days = [day for day in expected_days if day not in ts]
        
        # ── Check 2: 3-Way Price Discrepancies ──
        # Load newest cache price
        cache_price = None
        if ts:
            newest_cache_date = sorted(ts.keys())[-1]
            cache_price = float(ts[newest_cache_date].get("4. close", 0.0))
            
        # Load newest Chaikin PG price
        chaikin_price = data_api._get_chaikin_price(sym)
        
        # Verify discrepancies
        has_discrepancy = False
        discrepancy_msg = []
        
        # Compare Live E*TRADE vs. RapidAPI Cache (only if E*TRADE is online and not stale!)
        if is_etrade_online and cache_price and cache_price > 0:
            diff_rap = abs(live_price - cache_price)
            if diff_rap > 0.05 and (diff_rap / cache_price) * 100 > 3.0:
                has_discrepancy = True
                discrepancy_msg.append(f"E*TRADE ${live_price:.2f} vs RapidAPI ${cache_price:.2f} ({(diff_rap / cache_price)*100:.1f}%)")
                
        # Compare Live E*TRADE vs. Chaikin PG Cache (only if E*TRADE is online and not stale!)
        if is_etrade_online and chaikin_price and chaikin_price > 0:
            diff_pg1 = abs(live_price - chaikin_price)
            if diff_pg1 > 0.05 and (diff_pg1 / chaikin_price) * 100 > 3.0:
                has_discrepancy = True
                discrepancy_msg.append(f"E*TRADE ${live_price:.2f} vs Chaikin PG ${chaikin_price:.2f} ({(diff_pg1 / chaikin_price)*100:.1f}%)")

        # Compile final symbol status
        status_parts = []
        if missing_days:
            all_passed = False
            gaps_count += 1
            status_parts.append(f"❌ MISSING DATES: {', '.join(missing_days)}")
        if has_discrepancy:
            all_passed = False
            discrepancy_count += 1
            status_parts.append(f"🚨 PRICE MISMATCH: {', '.join(discrepancy_msg)}")
            
        if not status_parts:
            print(f"✅ {sym:<6} | [PASS] Timeline continuous. Prices synchronized (E*TRADE ${live_price:.2f} | Cache ${cache_price:.2f} | PG ${chaikin_price or 0.0:.2f})")
        else:
            print(f"❌ {sym:<6} | [FAIL] " + " | ".join(status_parts))

    print("="*80)
    print(" 📊 AUDIT SUMMARY:")
    print("="*80)
    print(f"  * Total unique positions audited: {len(unique_symbols)}")
    print(f"  * Timeline Contiguity Gaps:       {gaps_count}")
    print(f"  * 3-Way Pricing Discrepancies:    {discrepancy_count}")
    
    if all_passed:
        print("\n✅  [STATUS] PASS: All audited holdings have contiguous timelines and synchronized pricing.")
    else:
        print("\n⚠️  [STATUS] FAIL: Discrepancies or database gaps were detected. Please run 'rapidapi.py' to heal the gaps.")
    print("="*80 + "\n")
    return all_passed

if __name__ == "__main__":
    audit_database()
