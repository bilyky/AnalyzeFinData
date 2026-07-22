"""
Project AETHER: Systemic Crash "Circuit Breaker" (Systemic Risk Gate).

Audits broad market conditions (SPY single-day return, rolling 10-day drawdown,
VXX Volatility ETF proxy, and opening stabilization windows) to dynamically
freeze buying, tighten stop-losses, and prevent whipsaws on idiosyncratic
single-stock gap-downs. Features AETHER Elastic Memory to automatically cache and 
restore stop-losses to their original wide levels once market panic stabilizes,
and backfeeds systemic trigger events to the Failure DNA Ledger for weekly retrospectives.
"""

import json
import datetime
import pytz
from pathlib import Path
from aether import risk_utils
from aether.logger import get_logger as _get_logger

_log = _get_logger("circuit_breaker")

BASE_DIR = Path(__file__).resolve().parent.parent
SPY_FILE = BASE_DIR / "Data" / "Symbol_full" / "SPY_daily.json"
VXX_FILE = BASE_DIR / "Data" / "Symbol_full" / "VXX_daily.json"
DNA_FILE = BASE_DIR / "Data" / "trade_history_dna.json"

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

def load_vxx_prev_close() -> float | None:
    """Load yesterday's VXX close from local cache. Returns None if unavailable (disables VXX check)."""
    if not VXX_FILE.exists():
        return None
    try:
        with open(VXX_FILE, "r") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        if len(dates) >= 2:
            return float(ts[dates[-2]]["4. close"])
        return None
    except Exception:
        return None

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
    # When a live prices dict is supplied (even empty), SPY must be present and positive.
    # prices=None means "no live feed, fall back to cached history" — no freeze.
    live_spy_price = prices.get("SPY") if prices is not None else None
    if prices is not None and not (live_spy_price and live_spy_price > 0):
        _log.warning("CRITICAL WARNING: Unable to fetch live price for SPY! Defensively triggering Caution Freeze.")
        return True, "Data Feed Outage: Live SPY price unavailable. Caution freeze active."

    spy_series = load_spy_history()
    if not spy_series:
        return False, ""
        
    if len(spy_series) < 15:  # need at least 15 days for rolling drawdown check
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
    
    # VXX Volatility Proxy: surge > +15% signals systemic panic
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

def log_circuit_breaker_trigger_dna(reason: str, state: dict, prices=None, _spy_series=None):
    """Backfeed the systemic trigger event directly into the unified Trade History DNA Ledger."""
    DNA_FILE.parent.mkdir(parents=True, exist_ok=True)
    today = str(datetime.date.today())

    records = []
    if DNA_FILE.exists():
        try:
            with open(DNA_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception:
            records = []

    for r in records:  # deduplicate: only one trigger per day
        if isinstance(r, dict) and r.get("type") == "CIRCUIT_BREAKER_TRIGGER" and r.get("date") == today:
            return

    spy_series = _spy_series if _spy_series is not None else load_spy_history()
    spy_return = 0.0
    if spy_series and len(spy_series) >= 2:
        prev_close_val = spy_series[-2]["close"]
        live_spy = (prices or {}).get("SPY") or spy_series[-1]["close"]
        spy_return = round(((live_spy - prev_close_val) / prev_close_val) * 100, 2) if prev_close_val > 0 else 0.0

    vxx_prev = load_vxx_prev_close()
    live_vxx = (prices or {}).get("VXX") or vxx_prev
    vxx_return = round(((live_vxx - vxx_prev) / vxx_prev) * 100, 2) if vxx_prev > 0 else 0.0
    
    open_positions = [
        {"symbol": sym, "cost": pos.get("cost", 0.0),
         "stop_loss": pos.get("stop_loss", 0.0), "is_scarcity": pos.get("is_scarcity", False)}
        for sym, pos in state.get("positions", {}).items()
    ]
    record = {
        "type": "CIRCUIT_BREAKER_TRIGGER",
        "date": today,
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
        "reason": reason,
        "spy_return_pct": spy_return,
        "vxx_return_pct": vxx_return,
        "portfolio_equity": state.get("equity", 0.0),
        "cash_balance": state.get("balance", 0.0),
        "open_positions": open_positions,
        "profile": state.get("profile", "DEFENSIVE")
    }
    
    records.append(record)
    
    try:
        with open(DNA_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=4)
        _log.warning(f"  [Breaker Backfeed] Logged systemic trigger DNA to unified ledger (trade_history_dna.json)!")
    except Exception as e:
        _log.error(f"Failed to log circuit breaker trigger DNA: {e}")

def enforce_circuit_breaker(state, prices=None) -> list[str]:
    """
    If the Circuit Breaker is active, dynamically freeze buying and tighten stop-losses
    on all active positions to protect capital.
    If the Circuit Breaker is inactive, automatically restore any previously tightened stops.
    """
    is_active, reason = check_systemic_risk(prices)
    positions = state.get("positions", {})
    
    if not is_active:
        # AETHER Elastic Memory: If the breaker is inactive, restore original wide stops cleanly!
        restored_symbols = []
        for sym, pos in positions.items():
            if "original_stop_loss" in pos:
                original = pos.pop("original_stop_loss")
                pos["stop_loss"] = original
                restored_symbols.append(sym)
                
        if restored_symbols:
            _log.info(f"  [Breaker Recovery] Market stabilized. Restored stop-losses to original levels on: {', '.join(restored_symbols)}")
        return []
        
    _log.warning(f"CIRCUIT BREAKER TRIGGERED! Reason: {reason}")
    log_circuit_breaker_trigger_dna(reason, state, prices, _spy_series=load_spy_history())
    
    # 1. Freeze all buying (clears any queued buy orders)
    queued = state.get("queued_orders", [])
    original_count = len(queued)
    state["queued_orders"] = [q for q in queued if q.get("type") != "BUY"]
    if len(state["queued_orders"]) < original_count:
        _log.info(f"  [Breaker] Cleared {original_count - len(state['queued_orders'])} queued buy orders.")
        
    # 2. Tighten Stop-Losses on all active positions to lock in gains
    tightened_symbols = []
    if "Whipsaw protection active" not in reason and "Data Feed Outage" not in reason:
        for sym, pos in positions.items():
            if pos.get("is_scarcity", False):
                continue
            cur_stop = pos.get("stop_loss", 0.0)
            live_price = (prices or {}).get(sym) or pos.get("cost", 0.0)
            atr = risk_utils.calculate_atr(sym)
            if atr and atr > 0:
                tight_stop = round(live_price - atr, 2)
                if tight_stop > cur_stop and tight_stop < live_price:
                    if "original_stop_loss" not in pos:
                        pos["original_stop_loss"] = cur_stop
                    pos["stop_loss"] = tight_stop
                    tightened_symbols.append(sym)
                    
        if tightened_symbols:
            _log.warning(f"  [Breaker] Tightened stop-losses to 1.0x ATR (scarcity holdings left wide): {', '.join(tightened_symbols)}")
            
    return tightened_symbols
