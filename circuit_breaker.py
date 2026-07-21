"""
Project AETHER: Systemic Crash "Circuit Breaker" (Systemic Risk Gate).

Audits broad market conditions (SPY single-day return, rolling 10-day drawdown,
VXX Volatility ETF proxy, and opening stabilization windows) to dynamically
freeze buying, tighten stop-losses, and prevent whipsaws on idiosyncratic
single-stock gap-downs during systemic market panics.
"""

import json
import datetime
import pytz
from pathlib import Path
import risk_utils
from aether_logger import get_logger as _get_logger

_log = _get_logger("circuit_breaker")

BASE_DIR = Path(__file__).resolve().parent
SPY_FILE = BASE_DIR / "Data" / "Symbol_full" / "SPY_daily.json"
VXX_FILE = BASE_DIR / "Data" / "Symbol_full" / "VXX_daily.json"

def load_spy_history() -> list[dict]:
    """Load sorted daily price series for the SPY ETF from the local cache."""
    if not SPY_FILE.exists():
        return []
    try:
        with open(SPY_FILE, "r") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        # Parse into a sorted list of daily records
        series = []
        for d in sorted(ts.keys()):
            try:
                series.append({
                    "date": d,
                    "close": float(ts[d]["4. close"]),
                    "open": float(ts[d]["1. open"]),
                    "high": float(ts[d]["2. high"]),
                    "low": float(ts[d]["3. low"]),
                })
            except (ValueError, KeyError):
                continue
        return series
    except Exception as e:
        _log.warning(f"Failed to load SPY history for circuit breaker: {e}")
        return []

def load_vxx_prev_close() -> float:
    """Load yesterday's close for the VXX Volatility ETF from local cache."""
    if not VXX_FILE.exists():
        return 30.0  # safe default fallback
    try:
        with open(VXX_FILE, "r") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        if len(dates) >= 2:
            return float(ts[dates[-2]]["4. close"])
        return 30.0
    except Exception:
        return 30.0

def is_market_opening_window() -> bool:
    """True if we are within the 30-minute opening stabilization window (9:30 AM - 10:00 AM EST)."""
    tz = pytz.timezone("America/New_York")
    now = datetime.datetime.now(tz)
    
    # Exclude weekends
    if now.weekday() >= 5:
        return False
        
    opening_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    opening_end = now.replace(hour=10, minute=0, second=0, microsecond=0)
    
    return opening_start <= now <= opening_end

def is_single_stock_gap_frozen(symbol: str, live_price: float, prev_close: float) -> bool:
    """True if an open position has gapped down > 8% at the open, holding stop wide to prevent whipsaw."""
    if not is_market_opening_window():
        return False
        
    if not prev_close or prev_close <= 0 or not live_price or live_price <= 0:
        return False
        
    gap_pct = ((live_price - prev_close) / prev_close) * 100
    return gap_pct <= -8.0

def check_systemic_risk(prices=None) -> tuple[bool, str]:
    """
    Audit systemic risk factors to determine if the Circuit Breaker is active.
    Returns (is_active, reason).
    """
    # Patch 3: Agnostic Fallback & Caution State
    # If prices dict is completely empty or missing SPY, trigger protective Caution Freeze.
    live_spy_price = (prices or {}).get("SPY") if prices else None
    if prices is not None and (not live_spy_price or live_spy_price <= 0):
        _log.warning("CRITICAL WARNING: Unable to fetch live price for SPY! Defensively triggering Caution Freeze.")
        return True, "Data Feed Outage: Live SPY price unavailable. Caution freeze active."

    spy_series = load_spy_history()
    if not spy_series:
        return False, ""
        
    # We need at least 15 days of history for rolling checks
    if len(spy_series) < 15:
        return False, ""
        
    latest_day = spy_series[-1]
    prev_day = spy_series[-2]
    
    # 1. Single-Day Price Capitulation (SPY down > 2.0%)
    spy_price = live_spy_price if (live_spy_price and live_spy_price > 0) else latest_day["close"]
    prev_close = prev_day["close"]
    spy_return = ((spy_price - prev_close) / prev_close) * 100 if prev_close else 0.0
    
    # 2. Rolling 10-day Drawdown Breaker (Slow-bleed protection)
    recent_closes = [r["close"] for r in spy_series[-10:]]
    if live_spy_price and live_spy_price > 0:
        recent_closes[-1] = live_spy_price
    spy_peak = max(recent_closes)
    spy_drawdown = ((spy_price - spy_peak) / spy_peak) * 100 if spy_peak else 0.0
    
    # Patch 2: Volatility Proxy Check (VXX surges > +15.0%)
    vxx_prev = load_vxx_prev_close()
    vxx_price = (prices or {}).get("VXX") if prices else None
    vxx_return = ((vxx_price - vxx_prev) / vxx_prev) * 100 if (vxx_price and vxx_prev) else 0.0
    volatility_capitulation = vxx_return >= 15.0
    
    # 3. Apply Whipsaw Protection (Opening Stabilization Window)
    # If the market opened with a gap-down, we freeze buying immediately,
    # but we DO NOT tighten stops or execute panic-sells during the first 30 minutes!
    if is_market_opening_window():
        if spy_return <= -2.0 or spy_drawdown <= -5.0 or volatility_capitulation:
            vxx_desc = f", VXX: {vxx_return:+.1f}%" if volatility_capitulation else ""
            return True, f"Market Open Freeze (SPY: {spy_return:+.2f}%, Drawdown: {spy_drawdown:+.2f}%{vxx_desc}). Whipsaw protection active: stops held wide."
        return False, ""

    # 4. Enforce Systemic Rejection Triggers (Active Session after 10:00 AM EST)
    if spy_return <= -2.0:
        return True, f"Single-Day Capitulation (SPY fell {spy_return:+.2f}% in a single session)."
        
    if spy_drawdown <= -5.0:
        return True, f"Rolling 10-day Drawdown Breach (SPY is down {spy_drawdown:+.2f}% from its 10-day peak)."
        
    if volatility_capitulation:
        return True, f"Volatility Capitulation (VXX Volatility ETF surged {vxx_return:+.2f}% on systemic panic)."
        
    return False, ""

def enforce_circuit_breaker(state, prices=None) -> list[str]:
    """
    If the Circuit Breaker is active, dynamically freeze buying and tighten stop-losses
    on all active positions to protect capital.
    """
    is_active, reason = check_systemic_risk(prices)
    if not is_active:
        return []
        
    _log.warning(f"CIRCUIT BREAKER TRIGGERED! Reason: {reason}")
    
    # 1. Freeze all buying (clears any queued buy orders)
    queued = state.get("queued_orders", [])
    original_count = len(queued)
    state["queued_orders"] = [q for q in queued if q.get("type") != "BUY"]
    if len(state["queued_orders"]) < original_count:
        _log.info(f"  [Breaker] Cleared {original_count - len(state['queued_orders'])} queued buy orders.")
        
    # 2. Tighten Stop-Losses on all active positions to lock in gains
    # We tighten the stops of all open normal/satellite positions to a very conservative 1.0x ATR floor
    # (unless we are in the 30-minute opening window when stops are held wide to prevent whipsaws!)
    tightened_symbols = []
    if "Whipsaw protection active" not in reason and "Data Feed Outage" not in reason:
        positions = state.get("positions", {})
        for sym, pos in positions.items():
            if pos.get("is_scarcity", False):
                continue  # leave utility scarcity holdings wide
                
            cost = pos.get("cost", 0.0)
            cur_stop = pos.get("stop_loss", 0.0)
            live_price = (prices or {}).get(sym) or cost
            
            # Calculate a tight 1.0x ATR stop floor
            atr = risk_utils.calculate_atr(sym)
            if atr and atr > 0:
                tight_stop = round(live_price - (1.0 * atr), 2)
                # Only tighten if the new stop is higher (safer) than the existing stop,
                # and below the current live price!
                if tight_stop > cur_stop and tight_stop < live_price:
                    pos["stop_loss"] = tight_stop
                    tightened_symbols.append(sym)
                    
        if tightened_symbols:
            _log.warning(f"  [Breaker] Surgically tightened stop-losses to 1.0x ATR on satellite positions: {', '.join(tightened_symbols)}")
            
    return tightened_symbols
