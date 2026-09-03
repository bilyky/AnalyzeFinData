"""
Project AETHER: Centralized Option Pricing & Premium Capture Engine (R&D #26)

Implements a self-contained Black-Scholes-Merton pricing model, automated weekly
out-of-the-money (OTM) Covered Call selection, and position-assignment lifecycles.
"""

import datetime
import math

from aether import instruments
from aether.config import CFG
from aether_logger import get_logger as _get_logger


_log = _get_logger("options")

# Flat pricing placeholders — the documented FALLBACK when a per-symbol vol proxy can't be
# computed (missing/zero ATR or price). FLAT_RATE is the shared risk-free rate for both legs.
FLAT_SIGMA = 0.30
FLAT_RATE = 0.04

# Per-symbol implied-vol proxy (R&D #26 follow-up, Item 2 — SHIPPED).
# The write side now prices each premium at sigma = clamp((ATR/price)*sqrt(252)*IV_ATR_K,
# IV_FLOOR, IV_CEILING) instead of the flat 0.30, and stores that sigma on the written_call so
# buy-to-close prices against the same model it sold at. A flat 0.30 over-credited calm blue
# chips ~3x on weekly premium dollars and under-credited volatile names to ~1/3, biasing the
# booked ledger. Constants calibrated by scripts/backtesting/covered_call_iv_study.py
# (557 symbols): K = median(realized_vol / ATR-proxy) = 0.67; floor/ceiling = 5th/95th pct of
# per-symbol realized vol. Re-run that study monthly after the OHLCV top-up.
TRADING_DAYS = 252
IV_ATR_K = 0.67
IV_FLOOR = 0.18
IV_CEILING = 0.64


def atr_implied_vol(atr: float, price: float) -> float:
    """Per-symbol annualized vol proxy from ATR, clamped to [IV_FLOOR, IV_CEILING].

    Falls back to FLAT_SIGMA when ATR or price is missing/non-positive.
    """
    if not atr or not price or atr <= 0 or price <= 0:
        return FLAT_SIGMA
    proxy = (atr / price) * math.sqrt(TRADING_DAYS) * IV_ATR_K
    return max(IV_FLOOR, min(IV_CEILING, proxy))


def norm_cdf(x: float) -> float:
    """Exact cumulative standard normal distribution N(x) via math.erf (not an approximation)."""
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


def select_covered_call(symbol: str, current_price: float, atr: float, volatility: float = FLAT_SIGMA, interest_rate: float = FLAT_RATE) -> dict:
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
            # Do NOT default a missing quote to cost basis: cost is always below the OTM strike,
            # which would silently force the "expires worthless" (favorable) branch. Defer
            # settlement to a later pass when a live quote is available.
            if sym not in prices:
                _log.warning(f"[Option Expiry] No live quote for {sym} on its settlement day; deferring Covered Call settlement (no cost-basis fallback).")
                continue
            current_px = prices[sym]
            strike = written_call.get("strike")
            qty = written_call.get("qty")
            premium = written_call.get("premium")
            if strike is None or qty is None or premium is None:
                _log.warning(f"[Option Expiry] Malformed written_call on {sym} (missing strike/qty/premium); skipping settlement.")
                continue
            premium_usd = round(premium * qty, 2)

            if current_px < strike:
                # Scenario A: Option expires worthless; we keep the stock. The premium cash was
                # already booked (balance + pnl) at OPTION_WRITE, so this event realizes no new
                # cash flow -> pnl = 0.0 to avoid double-counting the premium in the ledger.
                _log.info(f"[Option Expiry] Covered Call on {sym} expired worthless; kept ${premium_usd:.2f} premium (Strike: ${strike:.2f}, Price: ${current_px:.2f}).")
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
                    "pnl": 0.0,
                    "details": f"Covered Call Expired Worthless (Strike: ${strike:.2f}, ${premium_usd:.2f} premium booked at write)"
                }
                state.setdefault("history", []).append(tx)
            else:
                # Scenario B: Stock is called away at the Strike. The premium was already booked at
                # OPTION_WRITE, so this event's pnl is the realized stock capital gain ONLY.
                #
                # Only the COVERED quantity is called away. If the position grew beyond the written
                # call's coverage after the call was written (e.g. a later pyramiding scale-in raised
                # pos["qty"] past written_call["qty"]), the uncovered remainder must stay an open
                # position — never silently deleted with the covered shares (that would leak capital).
                held_qty = pos.get("qty", qty)
                called_qty = min(qty, held_qty)
                pnl_stock_usd = round((strike - pos["cost"]) * called_qty, 2)
                revenue_usd = round(strike * called_qty, 2)
                remaining_qty = held_qty - called_qty

                _log.info(f"[Option Assignment] {called_qty} share(s) of {sym} called away at Strike ${strike:.2f} (Price: ${current_px:.2f}).")
                _log.info(f"   Realized stock capital gain: ${pnl_stock_usd:+.2f} | Premium (booked at write): ${premium_usd:+.2f}")

                # Add stock sale revenue to your cash balance
                state["balance"] += revenue_usd

                if remaining_qty > 0:
                    # Uncovered shares survive as an open position; the liability is discharged.
                    pos["qty"] = remaining_qty
                    pos.pop("written_call", None)
                    _log.info(f"   {remaining_qty} uncovered share(s) of {sym} remain held (cost ${pos['cost']:.2f}).")
                else:
                    # Entire position was covered and is now called away.
                    del state["positions"][sym]

                # Record assignment sale in history ledger
                tx = {
                    "date": today_str,
                    "time": "07:00:01",
                    "type": "OPTION_ASSIGNMENT",
                    "symbol": sym,
                    "price": strike,
                    "qty": called_qty,
                    "pnl": pnl_stock_usd,
                    "details": f"{called_qty} share(s) called away at Strike ${strike:.2f} (Realized stock gain ${pnl_stock_usd:+.2f}; ${premium_usd:.2f} premium booked at write)"
                }
                state.setdefault("history", []).append(tx)


def execute_weekly_covered_call_pass(state: dict, today_str: str, prices: dict, ws_research) -> list:
    """Scan active holdings and write weekly out-of-the-money Covered Calls on risk-locked
    winning positions to collect option premium.

    A position qualifies if:
    1. It is a 'winner' (current_price > purchase_cost).
    2. Its stop-loss is raised to or above cost basis (downside is capped at breakeven).
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

    # Map ATRs from the Research sheet. Resolve the ATR/Symbol columns by header name so a
    # column re-order upstream can't silently point us at the wrong field.
    header = next(ws_research.iter_rows(min_row=1, max_row=1, values_only=True), ()) or ()
    header_norm = [str(h or "").strip().lower() for h in header]
    try:
        sym_idx = header_norm.index("symbol")
    except ValueError:
        sym_idx = 3
        _log.warning("[Option Write] Research sheet has no 'Symbol' header; falling back to column index 3 (may bind the wrong field).")
    atr_idx = next((i for i, h in enumerate(header_norm) if h == "atr"), None)
    if atr_idx is None:
        atr_idx = next((i for i, h in enumerate(header_norm) if "atr" in h), None)
    if atr_idx is None:
        atr_idx = 23
        _log.warning("[Option Write] Research sheet has no 'ATR' header; falling back to column index 23 (may bind the wrong field).")
    # L60 (60-day conviction score) gates the flower exclusion below — high-conviction winners
    # are excluded from call-writing so their upside is not capped (R&D #26 follow-up, Item 1).
    l60_idx = next((i for i, h in enumerate(header_norm) if h == "l60"), None)
    if l60_idx is None:
        l60_idx = 25
        _log.warning("[Option Write] Research sheet has no 'L60' header; falling back to column index 25 (may bind the wrong field).")

    atr_map, l60_map = {}, {}
    for row in ws_research.iter_rows(min_row=2, values_only=True):
        sym = str(row[sym_idx] or "").strip().upper() if sym_idx < len(row) else ""
        if sym:
            atr_map[sym] = (row[atr_idx] if atr_idx < len(row) else None) or 0.0
            try:
                l60_map[sym] = float(row[l60_idx]) if l60_idx < len(row) and row[l60_idx] is not None else 0.0
            except (TypeError, ValueError):
                l60_map[sym] = 0.0

    for sym, pos in state.get("positions", {}).items():
        current_px = prices.get(sym, pos["cost"])
        is_winner = (current_px > pos["cost"])
        is_risk_locked = (pos.get("stop_loss", 0.0) >= pos["cost"])
        has_active_call = "written_call" in pos
        # Never write on leveraged / inverse / crypto instruments: their realized vol and daily
        # decay dynamics make the flat-IV Black-Scholes premium meaningless, and the Aug-8 roadmap
        # already carves this cohort out of the long framework (instruments.is_excluded).
        is_optionable = not instruments.is_excluded(sym)
        # Flower exclusion: do NOT cap the upside of high-conviction winners. Capping a strong
        # flower with a covered call costs 5-10x the premium edge of a mid-conviction name
        # (scripts/backtesting/covered_call_winner_study.py: the >50%-momentum cohort loses
        # -0.488%/write vs -0.046% in the 0-25% middle). Above the CFG L60 ceiling the position
        # is left uncapped — the code form of CLAUDE.md's "a win that dumps a flower is a mistake".
        l60 = l60_map.get(sym, 0.0)
        is_high_conviction = l60 >= CFG.system_covered_call_l60_ceiling

        # Only write Covered Calls on risk-locked winners (downside already capped at breakeven),
        # excluding high-conviction flowers whose upside we protect.
        if is_winner and is_risk_locked and not has_active_call and is_optionable and not is_high_conviction:
            atr = atr_map.get(sym, current_px * 0.04) # fallback to 4% ATR

            # Price the premium at the per-symbol ATR-implied vol (not the flat 0.30), and carry
            # that sigma onto the written_call so buy-to-close prices against the same model.
            sigma = atr_implied_vol(atr, current_px)
            opt = select_covered_call(sym, current_px, atr, volatility=sigma)
            strike = opt["strike"]
            premium_price = opt["premium_price"]
            premium_usd = round(premium_price * pos["qty"], 2)

            _log.info(f"[Option Write] Writing weekly Covered Call on {sym} @ Strike ${strike:.2f} (collected ${premium_usd:.2f} premium).")
            
            # 1. Collect cash premium immediately!
            state["balance"] += premium_usd
            
            # 2. Attach the option liability to the position (store sigma for symmetric BTC pricing)
            pos["written_call"] = {
                "strike": strike,
                "premium": premium_price,
                "expiration_date": next_friday_str,
                "qty": pos["qty"],
                "sigma": sigma
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
        elif is_winner and is_risk_locked and not has_active_call and is_optionable and is_high_conviction:
            _log.info(f"[Option Write] Skipping {sym}: high-conviction flower (L60 {l60:.1f} >= ceiling {CFG.system_covered_call_l60_ceiling:.1f}); leaving upside uncapped.")

    return new_options


def unwind_option_liability_if_held(sym: str, pos: dict, state: dict, current_price: float, today_str: str):
    """If the position has an active written Covered Call, buy-to-close (BTC) the short call at its
    current Black-Scholes fair value before the underlying is sold, so no naked call is left behind.
    """
    written_call = pos.get("written_call")
    if not written_call:
        return

    strike = written_call.get("strike")
    qty = written_call.get("qty")
    exp_date = written_call.get("expiration_date", "")
    if strike is None or qty is None:
        _log.warning(f"[Option Buy-To-Close] Malformed written_call on {sym} (missing strike/qty); clearing liability without BTC.")
        pos.pop("written_call", None)
        return

    # Calculate days to expiration
    try:
        today_date = datetime.datetime.strptime(today_str, "%Y-%m-%d")
        exp_date_dt = datetime.datetime.strptime(exp_date, "%Y-%m-%d")
        days_rem = max(0.1, (exp_date_dt - today_date).days)
    except Exception:
        days_rem = 5.0

    T_rem = days_rem / 365.0

    # Price the buy-to-close at the SAME per-symbol sigma the write side sold at (stored on the
    # written_call), so there is no write-at-proxy / BTC-at-flat asymmetry. Falls back to
    # FLAT_SIGMA for legacy positions written before the sigma was persisted. The debit hits the
    # live balance; FLAT_RATE is the shared risk-free rate (R&D #26 follow-up, Item 2).
    sigma = written_call.get("sigma") or FLAT_SIGMA
    btc_price = calculate_black_scholes_call(current_price, strike, T_rem, r=FLAT_RATE, sigma=sigma)
    btc_usd = round(btc_price * qty, 2)

    _log.warning(f"[Option Buy-To-Close] {sym} hit stop-loss/rotation; buying back short Call @ ${btc_price:.2f} before sale to avoid a naked call (cost ${btc_usd:.2f}).")

    # Deduct option buy-back cost from cash balance
    state["balance"] -= btc_usd

    # Record Buy-To-Close transaction
    tx = {
        "date": today_str,
        "time": "07:35:01",
        "type": "OPTION_BUY_TO_CLOSE",
        "symbol": sym,
        "price": btc_price,
        "qty": qty,
        "pnl": -btc_usd,
        "details": f"Buy-To-Close Short Call (Strike: ${strike:.2f}, Paid: ${btc_usd:.2f} to prevent naked liability)"
    }
    state.setdefault("history", []).append(tx)

    # Clear the written call liability
    del pos["written_call"]

