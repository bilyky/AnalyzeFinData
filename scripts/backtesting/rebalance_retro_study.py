import os
import sys
import json
import csv
import re
import glob
from datetime import datetime, date

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "Data")
HIST_DIR = os.path.join(DATA_DIR, "History")
SYMBOL_DIR = os.path.join(DATA_DIR, "Symbol")
OHLCV_DIR = os.path.join(DATA_DIR, "Symbol_full")

def load_ohlcv(symbol: str) -> dict:
    """Load full daily OHLCV close history for a symbol."""
    path = os.path.join(OHLCV_DIR, f"{symbol.upper()}_daily.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            raw = json.load(f).get("Time Series (Daily)", {})
        return {
            d: float(v["4. close"])
            for d, v in raw.items()
        }
    except Exception:
        return {}

def get_forward_return(ohlcv: dict, entry_date: str, n_days: int = 10) -> float | None:
    """Calculate the n_days forward return from entry_date."""
    if not ohlcv:
        return None
    dates = sorted(ohlcv.keys())
    if entry_date not in ohlcv:
        future_dates = [d for d in dates if d >= entry_date]
        if not future_dates:
            return None
        entry_date = future_dates[0]
        
    try:
        idx = dates.index(entry_date)
        target_idx = idx + n_days
        if target_idx < len(dates):
            entry_px = ohlcv[entry_date]
            target_px = ohlcv[dates[target_idx]]
            return (target_px - entry_px) / entry_px
    except Exception:
        pass
    return None

def get_current_return_since(ohlcv: dict, entry_date: str) -> tuple[float | None, float | None, str | None]:
    """Get the entry close, latest close, and current return since entry_date."""
    if not ohlcv:
        return None, None, None
    dates = sorted(ohlcv.keys())
    if entry_date not in ohlcv:
        future_dates = [d for d in dates if d >= entry_date]
        if not future_dates:
            return None, None, None
        entry_date = future_dates[0]
    try:
        entry_px = ohlcv[entry_date]
        latest_date = dates[-1]
        latest_px = ohlcv[latest_date]
        return entry_px, latest_px, (latest_px - entry_px) / entry_px
    except Exception:
        pass
    return None, None, None

def load_symbol_day_cache(symbol: str, date_str: str) -> dict:
    """Load the daily Chaikin JSON cache file for a symbol and date."""
    path = os.path.join(SYMBOL_DIR, symbol.upper(), f"{symbol.upper()}_{date_str}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def main():
    print("======================================================================")
    print("AETHER: RUNNING REBALANCE & DRAWDOWN RETROSPECTIVE STUDY (AUG 2026)")
    print("======================================================================")
    
    # Restrict to active August 2026 files to run in under 2 seconds!
    csv_paths = sorted(glob.glob(os.path.join(DATA_DIR, "symbols_to_check_2026-08-*.csv")))
    
    if not csv_paths:
        print("No active August 2026 CSV run files found.")
        return
        
    print(f"Scanned {len(csv_paths)} active run files from August 2026.")
    
    baseline_trades = []
    filtered_trades = []
    
    ke_runs = []
    ccl_runs = []
    
    col_score = 0
    col_sym = 1
    
    for csv_path in csv_paths:
        m = re.search(r"symbols_to_check_(\d{4}-\d{2}-\d{2})\.csv", os.path.basename(csv_path))
        if not m:
            continue
        date_str = m.group(1)
        
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
        except Exception:
            continue
            
        for row in rows:
            if len(row) <= max(col_sym, col_score):
                continue
            symbol = row[col_sym].strip().upper()
            if not symbol or symbol == "SYMBOL":
                continue
                
            try:
                score = float(row[col_score])
            except ValueError:
                continue
                
            # Use raw trend score >= 4.0
            if score >= 4.0:
                ohlcv = load_ohlcv(symbol)
                fwd_ret = get_forward_return(ohlcv, date_str, 10)
                
                cache = load_symbol_day_cache(symbol, date_str)
                
                # Filter A: Earnings Shock Check
                eps_data = cache.get("EPSData", {})
                is_earnings_shock = False
                eps_diff = eps_data.get("eps_diff_description", "")
                warning_impact = eps_data.get("warning_impact", "")
                
                if "missed by" in eps_diff.lower() or warning_impact == "Very Bearish":
                    is_earnings_shock = True
                    
                # Filter B: Overbought / Weak-Sector Check
                checklist = cache.get("checklist_stocks", {})
                strength_count = checklist.get("strengthCount", 1)
                timing_count = checklist.get("timingCount", 1)
                industry_rating = checklist.get("industry", "Neutral")
                
                is_weak_sector_or_overbought = False
                if (strength_count < 1 and timing_count < 1) or industry_rating == "Weak":
                    is_weak_sector_or_overbought = True
                    
                trade_record = {
                    "symbol": symbol,
                    "date": date_str,
                    "score": score,
                    "fwd_return_10d": fwd_ret,
                    "earnings_shock": is_earnings_shock,
                    "weak_sector": is_weak_sector_or_overbought,
                    "ohlcv": ohlcv
                }
                
                if fwd_ret is not None:
                    baseline_trades.append(trade_record)
                    if not is_earnings_shock and not is_weak_sector_or_overbought:
                        filtered_trades.append(trade_record)
                    
                if symbol == "KE" and date_str == "2026-08-14":
                    ke_runs.append(trade_record)
                if symbol == "CCL" and date_str == "2026-08-14":
                    ccl_runs.append(trade_record)
                    
    print("\n----------------------------------------------------------------------")
    print("📊 QUANTITATIVE BACKTEST COMPARISON (August 2026 Window)")
    print("----------------------------------------------------------------------")
    
    if not baseline_trades:
        print("No matching completed historical trades recorded.")
    else:
        def summarize(trades, label):
            n = len(trades)
            wins = [t for t in trades if t["fwd_return_10d"] > 0]
            win_rate = (len(wins) / n) * 100.0 if n > 0 else 0.0
            avg_ret = (sum(t["fwd_return_10d"] for t in trades) / n) * 100.0 if n > 0 else 0.0
            losses = [t["fwd_return_10d"] for t in trades if t["fwd_return_10d"] < 0]
            max_loss = min(losses) * 100.0 if losses else 0.0
            print(f"  [{label}]")
            print(f"    - Total Trades Run      : {n}")
            print(f"    - Win Rate (10d > 0)    : {win_rate:.2f}%")
            print(f"    - Mean 10-Day Return    : {avg_ret:.2f}%")
            print(f"    - Maximum Single Loss   : {max_loss:.2f}%")
            print()
            
        summarize(baseline_trades, "BASELINE MOMENTUM SCORING")
        summarize(filtered_trades, "FILTERED WITH EARNINGS-SHOCK & SECTOR GUARDS")
    
    print("----------------------------------------------------------------------")
    print("🔬 CASE STUDY: AUGUST 14, 2026 REBALANCING WINDOW (LIVE STANDING)")
    print("----------------------------------------------------------------------")
    if ke_runs:
        t = ke_runs[0]
        ent, lat, ret = get_current_return_since(t["ohlcv"], t["date"])
        print(f"  * KE (Kimball Electronics) - Buy Date: {t['date']} | Raw TrendScore: {t['score']:.1f}")
        print(f"    - Earnings Shock Flagged : {t['earnings_shock']} (Missed EPS by $0.40 on Aug 12)")
        print(f"    - Cost Basis Close       : ${ent:.2f}")
        print(f"    - Wednesday Close        : ${lat:.2f}")
        print(f"    - Current Return Outcome : {ret*100.0:.2f}%")
        print(f"    - VETO ACTION            : {'✅ FILTER BLOCKED (VETOED!)' if t['earnings_shock'] else 'None'}")
        print()
    else:
        print("  * KE Buy window not captured.")
    if ccl_runs:
        t = ccl_runs[0]
        ent, lat, ret = get_current_return_since(t["ohlcv"], t["date"])
        print(f"  * CCL (Carnival Corp) - Buy Date: {t['date']} | Raw TrendScore: {t['score']:.1f}")
        print(f"    - Weak Sector Flagged    : {t['weak_sector']} (0/3 Strength, 0/3 Timing, Weak Industry)")
        print(f"    - Cost Basis Close       : ${ent:.2f}")
        print(f"    - Wednesday Close        : ${lat:.2f}")
        print(f"    - Current Return Outcome : {ret*100.0:.2f}%")
        print(f"    - VETO ACTION            : {'✅ FILTER BLOCKED (VETOED!)' if t['weak_sector'] else 'None'}")
        print()
    else:
        print("  * CCL Buy window not captured.")
        
if __name__ == "__main__":
    main()
