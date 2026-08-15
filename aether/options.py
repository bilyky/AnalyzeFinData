"""
Project AETHER: Centralized Option Pricing & Premium Capture Engine (R&D #26)

Implements a self-contained Black-Scholes-Merton pricing model, automated weekly
out-of-the-money (OTM) Covered Call selection, and position-assignment lifecycles.
"""

import math
import datetime
from pathlib import Path
from aether_logger import get_logger as _get_logger
from aether.config import CFG

_log = _get_logger("options")

BASE_DIR = Path(__file__).resolve().parent.parent


def norm_cdf(x: float) -> float:
    """High-accuracy rational approximation for the cumulative standard normal distribution (N(x))."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def calculate_black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calculate the fair value of a European Call option using the Black-Scholes-Merton model.
    
    S: Current stock price
    K: Strike price
    T: Time to expiration in years (e.g. 7/365 for weekly options)
    r: Risk-free interest rate (annualized)
    sigma: Volatility (annualized, e.g. 0.30 for 30%)
    """
    if T <= 0.0 or sigma <= 0.0:
        return max(0.0, S - K)
        
    d1 = (math.log(S / K) + (r + (sigma ** 2) / 2.0) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    call_price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return max(0.01, round(call_price, 2))


def select_covered_call(symbol: str, current_price: float, atr: float, volatility: float = 0.30, interest_rate: float = 0.04) -> dict:
    """Select the optimal weekly out-of-the-money (OTM) Covered Call to write.
    
    Targets a Strike Price of approximately 1.5x ATR above the current price (safely OTM)
    and rounds it to the nearest standard option interval ($2.50 or $5.00).
    Targets a weekly expiration expiring next Friday (7 calendar days / 0.019 years).
    """
    # 1. Target Strike Price: current_price + 1.5 * ATR (with a minimum of 5% OTM padding)
    target_strike = max(current_price * 1.05, current_price + 1.5 * (atr or current_price * 0.04))
    
    # Round strike to the nearest standard interval
    # For stocks under $50, round to nearest $1.00. Under $150, nearest $2.50. Above $150, nearest $5.00.
    if target_strike < 50.0:
        strike = round(target_strike)
    elif target_strike < 150.0:
        strike = round(target_strike / 2.5) * 2.5
    else:
        strike = round(target_strike / 5.0) * 5.0
        
    # Ensure the rounded strike is strictly above the current price
    if strike <= current_price:
        strike = target_strike
        
    # 2. Time to Expiration: 7 calendar days
    T = 7.0 / 365.0
    
    # 3. Calculate fair premium price using Black-Scholes
    premium_price = calculate_black_scholes_call(current_price, strike, T, interest_rate, volatility)
    
    # Calculate percentage yield on stock value
    yield_pct = (premium_price / current_price) * 100.0
    
    return {
        "symbol": symbol,
        "underlying_price": current_price,
        "strike": strike,
        "expiration_days": 7,
        "premium_price": premium_price,
        "yield_pct": round(yield_pct, 2)
    }


def resolve_expiring_options(state: dict, today_str: str, prices: dict):
    """Scan and settle all active, expiring written Covered Call options.
    
    - Case A (Below Strike): Option expires worthless. We keep 100% of the cash premium as profit.
    - Case B (At/Above Strike): Stock is called away. We sell the underlying stock at the strike price,
      realizing the locked-in capital gains, and release the option liability.
    """
    for sym, pos in list(state.get("positions", {}).items()):
        written_call = pos.get("written_call")
        if not written_call:
            continue
            
        exp_date = written_call.get("expiration_date", "")
        # If the option has reached or passed its expiration date
        if exp_date and exp_date <= today_str:
            current_px = prices.get(sym, pos["cost"])
            strike = written_call["strike"]
            qty = written_call["qty"]
            premium = written_call["premium"]
            premium_usd = round(premium * qty, 2)
            
            if current_px < strike:
                # Scenario A: Option expires worthless!
                _log.info(f"💎 [Option Expiry] Covered Call on {sym} expired worthless! Kept 100% premium of ${premium_usd:.2f} (Strike: ${strike:.2f}, Price: ${current_px:.2f}).")
                # Remove option liability, keep stock!
                del pos["written_call"]
                
                # Record in history ledger
                tx = {
                    "date": today_str,
                    "time": "07:00:01",
                    "type": "OPTION_EXPIRY",
                    "symbol": sym,
                    "price": premium,
                    "qty": qty,
                    "pnl": premium_usd,
                    "details": f"Covered Call Expired Worthless (Strike: ${strike:.2f}, kept ${premium_usd:.2f} premium)"
                }
                state.setdefault("history", []).append(tx)
            else:
                # Scenario B: Stock is called away at the Strike!
                pnl_stock_usd = round((strike - pos["cost"]) * qty, 2)
                pnl_total_usd = round(pnl_stock_usd + premium_usd, 2)
                revenue_usd = round(strike * qty, 2)
                
                _log.info(f"💰 [Option Assignment] Stock {sym} called away at Strike ${strike:.2f} (Price: ${current_px:.2f})!")
                _log.info(f"   Realized stock capital gain: ${pnl_stock_usd:+.2f} | Premium kept: ${premium_usd:+.2f} | Total P&L: ${pnl_total_usd:+.2f}")
                
                # Add stock sale revenue to your cash balance
                state["balance"] += revenue_usd
                
                # Delete the underlying position from holdings
                del state["positions"][sym]
                
                # Record assignment sale in history ledger
                tx = {
                    "date": today_str,
                    "time": "07:00:01",
                    "type": "OPTION_ASSIGNMENT",
                    "symbol": sym,
                    "price": strike,
                    "qty": qty,
                    "pnl": pnl_total_usd,
                    "details": f"Stock called away at Strike ${strike:.2f} (Realized: ${pnl_stock_usd:+.2f} stock + ${premium_usd:.2f} premium)"
                }
                state.setdefault("history", []).append(tx)


def execute_weekly_covered_call_pass(state: dict, today_str: str, prices: dict, ws_research) -> list:
    """Scan active holdings and programmatically write weekly out-of-the-money Covered Calls
    on all risk-locked winning positions to collect steady cash flow premiums.
    
    A position qualifies if:
    1. It is a 'winner' (current_price > purchase_cost).
    2. Its stop-loss is raised to or above cost basis (guaranteeing a risk-free 'free ride').
    3. It does not already have an active written call option.
    """
    new_options = []
    
    # Find next Friday's expiration date (7 calendar days away)
    try:
        today_date = datetime.datetime.strptime(today_str, "%Y-%m-%d")
    except Exception:
        today_date = datetime.datetime.now()
        
    next_friday = today_date + datetime.timedelta(days=(4 - today_date.weekday() + 7) % 7)
    # If today is Friday, expire next Friday (7 days)
    if (next_friday - today_date).days == 0:
        next_friday = today_date + datetime.timedelta(days=7)
    next_friday_str = next_friday.strftime("%Y-%m-%d")

    # Map ATRs from Research sheet
    atr_map = {}
    for row in ws_research.iter_rows(min_row=2, values_only=True):
        sym = str(row[3] or "").strip().upper()
        if sym:
            # Column 23 (index 23) represents ATR
            atr_map[sym] = row[23] or 0.0

    for sym, pos in state.get("positions", {}).items():
        current_px = prices.get(sym, pos["cost"])
        is_winner = (current_px > pos["cost"])
        is_risk_locked = (pos.get("stop_loss", 0.0) >= pos["cost"])
        has_active_call = "written_call" in pos
        
        # We only write Covered Calls on our risk-locked winning positions (0% capital risk!)
        if is_winner and is_risk_locked and not has_active_call:
            atr = atr_map.get(sym, current_px * 0.04) # fallback to 4% ATR
            
            # Select optimal OTM Covered Call
            opt = select_covered_call(sym, current_px, atr)
            strike = opt["strike"]
            premium_price = opt["premium_price"]
            premium_usd = round(premium_price * pos["qty"], 2)
            
            _log.info(f"🚀 [Option Write] Writing weekly Covered Call on {sym} @ Strike ${strike:.2f} (Collected: ${premium_usd:.2f} Premium!)")
            
            # 1. Collect cash premium immediately!
            state["balance"] += premium_usd
            
            # 2. Attach the option liability to the position
            pos["written_call"] = {
                "strike": strike,
                "premium": premium_price,
                "expiration_date": next_friday_str,
                "qty": pos["qty"]
            }
            
            # 3. Record the transaction
            tx = {
                "date": today_str,
                "time": "07:35:05",
                "type": "OPTION_WRITE",
                "symbol": sym,
                "price": premium_price,
                "qty": pos["qty"],
                "pnl": premium_usd,
                "details": f"Wrote Weekly Covered Call (Strike: ${strike:.2f}, Exp: {next_friday_str}, collected ${premium_usd:.2f})"
            }
            state.setdefault("history", []).append(tx)
            new_options.append(tx)
            
    return new_options
