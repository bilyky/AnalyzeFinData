import json
import datetime
import openpyxl
import pytz
import etrade
import sys
from pathlib import Path

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
AI_GAME_FILE = BASE_DIR / "Data" / "ai_portfolio_game.json"
XLSX_FILE = BASE_DIR / "Data" / "state_of_the_day.xlsx"
AI_PERF_XLSX = BASE_DIR / "Data" / "ai_portfolio_performance.xlsx"
INITIAL_BALANCE = 10000.0

# Import risk utils safely
import risk_utils

def is_market_open():
    """Check if current time is within US Market hours (9:30 AM - 4:00 PM EST)."""
    tz = pytz.timezone("America/New_York")
    now = datetime.datetime.now(tz)
    
    if now.weekday() >= 5:
        return False, "Market is closed (Weekend)."
    
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    if market_open <= now <= market_close:
        return True, "Market is Open."
    else:
        return False, f"Market is Closed. (Current EST: {now.strftime('%H:%M')})"

def get_market_regime():
    """Query SPY momentum to dynamically determine the best strategy profile."""
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        ws = wb["Research"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3] == "SPY":
                l60 = row[25] or 0
                if l60 > 2: return "AGGRESSIVE" # Strong bull market
                if l60 < -2: return "DEFENSIVE" # Bear market
                return "BALANCED" # Consolidation
    except:
        pass
    return "BALANCED"

def get_strategy_rules(profile):
    """Define risk and size rules based on the chosen strategy profile."""
    # Profile options: BALANCED, AGGRESSIVE, DEFENSIVE
    if profile == "AGGRESSIVE":
        return {
            "max_positions": 6,
            "max_allocation_pct": 0.25, # Deeper allocation per trade
            "atr_multiplier": 3.5,       # Loose stop to avoid shakeouts in high-beta stocks
            "min_score_threshold": 2.0,  # Willing to buy slightly lower momentum for higher beta
            "cash_buffer_pct": 0.10      # Aggressive deployment
        }
    elif profile == "DEFENSIVE":
        return {
            "max_positions": 3,          # Restrict to top 3 ultra-conviction plays
            "max_allocation_pct": 0.15, # Lower risk per trade
            "atr_multiplier": 1.5,       # Tight stop-loss to preserve capital
            "min_score_threshold": 10.0, # Only buy elite setups
            "cash_buffer_pct": 0.50      # Keep 50% in cash
        }
    else: # BALANCED (Default)
        return {
            "max_positions": 5,
            "max_allocation_pct": 0.20,
            "atr_multiplier": 2.5,
            "min_score_threshold": 5.0,
            "cash_buffer_pct": 0.20
        }

def load_game():
    if not AI_GAME_FILE.exists():
        return {
            "balance": INITIAL_BALANCE,
            "equity": INITIAL_BALANCE,
            "positions": {},
            "history": [],
            "start_date": str(datetime.date.today()),
            "profile": "BALANCED"
        }
    with open(AI_GAME_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return {"balance": INITIAL_BALANCE, "equity": INITIAL_BALANCE, "positions": {}, "history": [], "start_date": str(datetime.date.today()), "profile": "BALANCED"}

def save_game(state):
    with open(AI_GAME_FILE, "w") as f:
        json.dump(state, f, indent=4)

def backtrack_verify(symbol):
    """Verify the consistency of a price trend over the last 3 trading days using local daily history."""
    try:
        path = BASE_DIR / "Data" / "Symbol_full" / f"{symbol}_daily.json"
        if not path.exists():
            return False, "No local daily history found."
        
        with open(path) as f:
            data = json.load(f)
            
        ts = data.get("Time Series (Daily)", {})
        sorted_dates = sorted(list(ts.keys()), reverse=True)
        if len(sorted_dates) < 3:
            return False, f"Insufficient history (found {len(sorted_dates)}/3 days) for verification."
        
        prices = [float(ts[d]["4. close"]) for d in sorted_dates[:3]]
        
        # Rule: Today's price (prices[0]) must not be in a vertical collapse 
        # (e.g. no day-over-day drop greater than 2% in the last 2 days)
        day1_change = (prices[0] - prices[1]) / prices[1]
        day2_change = (prices[1] - prices[2]) / prices[2]
        
        if day1_change >= -0.02 and day2_change >= -0.02:
            return True, f"Verified price trend stability: {[round(p, 2) for p in prices]}"
        else:
            return False, f"Failed backtracking check (vertical drop detected). Changes: {round(day1_change*100, 1)}%, {round(day2_change*100, 1)}%"
    except Exception as e:
        return False, f"Verification error: {e}"

def update_excel_log(state, new_transactions):
    if not AI_PERF_XLSX.exists():
        return
    try:
        wb = openpyxl.load_workbook(AI_PERF_XLSX)
        today = str(datetime.date.today())
        ws1 = wb["Summary"]
        profit = state["equity"] - INITIAL_BALANCE
        ws1.append([today, state["equity"], round(state["balance"], 2), round(profit, 2), f"{round((profit/INITIAL_BALANCE)*100, 2)}%", len(state["positions"])])
        ws2 = wb["Transaction_Log"]
        for tx in new_transactions:
            val = tx.get("qty", 1) * tx["price"]
            ws2.append([tx["date"], tx["time"], tx["type"], tx["symbol"], tx["price"], tx.get("qty", ""), round(val, 2), tx.get("pnl", "")])
        wb.save(AI_PERF_XLSX)
    except Exception as e:
        print(f"Failed to update Excel log: {e}")

def get_live_prices(symbols):
    """Fetch real-time market prices via E*TRADE safely (no interactive prompts)."""
    try:
        # Load cached tokens directly to avoid Playwright/MFA interactive prompts
        cached = etrade._load_tokens("production")
        if not cached:
            print("  [AETHER] No cached E*TRADE tokens found. Falling back to workbook prices.")
            return {}
            
        # Try to renew them silently
        tokens = etrade.renew_tokens(cached, "production")
        if not tokens:
            print("  [AETHER] E*TRADE token renewal failed. Falling back to workbook prices.")
            return {}
            
        quotes = etrade.fetch_quotes(tokens, symbols, env="production")
        return quotes
    except Exception as e:
        print(f"Live price fetch failed: {e}")
        return {}

def send_daily_summary():
    state = load_game()
    today = str(datetime.date.today())
    today_tx = [tx for tx in state.get("history", []) if tx["date"] == today]
    tx_rows = ""
    for tx in today_tx:
        pnl_str = f" (PnL: ${tx['pnl']})" if "pnl" in tx else ""
        tx_rows += f"<li><b>{tx['type']}</b>: {tx.get('qty', '')} {tx['symbol']} @ ${tx['price']}{pnl_str} [Time: {tx.get('time', '')}]</li>"

    html = f"""
    <html>
    <body style="font-family: sans-serif; color: #333;">
        <h2 style="color: #2c3e50;">🤖 AI Portfolio: Daily Performance Summary</h2>
        <p><b>Date:</b> {today} | <b>Active Strategy:</b> {state.get('profile', 'BALANCED')}</p>
        <hr>
        <h3>Market Action Today:</h3>
        <ul>{tx_rows if today_tx else "<li>No transactions executed today.</li>"}</ul>
        <h3>Current Portfolio Standing:</h3>
        <table border="1" cellpadding="8" style="border-collapse: collapse;">
            <tr style="background: #f8f9fa;"><td><b>Current Equity</b></td><td>${state['equity']}</td></tr>
            <tr><td><b>Cash Balance</b></td><td>${round(state['balance'], 2)}</td></tr>
            <tr style="background: #f8f9fa;"><td><b>Total Return</b></td><td>{round(((state['equity'] - INITIAL_BALANCE)/INITIAL_BALANCE)*100, 2)}%</td></tr>
        </table>
        <p style="margin-top: 20px;"><small>Automated AI Manager | Project: AnalyzeFinData</small></p>
    </body>
    </html>
    """
    import notify
    notify.send_email(f"AI Portfolio Summary: {today}", html, is_html=True)
    print(f"Summary email sent for {today}.")

def run_daily_ai_management(force=False, manual_profile=None):
    state = None
    try:
        open_status, msg = is_market_open()
        if not open_status and not force:
            print(f"Aborting AI Move: {msg}")
            return

        state = load_game()
        today = str(datetime.date.today())
        now_time = datetime.datetime.now().strftime("%H:%M:%S")
        new_transactions = []
        
        # Determine strategy profile (Adaptive vs. Manual Override)
        profile = manual_profile or get_market_regime()
        rules = get_strategy_rules(profile)
        state["profile"] = profile
        print(f"🤖 AI ACTIVE STRATEGY: {profile} ({'Manual' if manual_profile else 'Adaptive'})")

        if not XLSX_FILE.exists():
            print("Workbook not found. AI Management deferred.")
            return

        wb = openpyxl.load_workbook(XLSX_FILE, data_only=True)
        ws = wb["Research"]
        
        symbols_to_check = list(state["positions"].keys())
        research_symbols = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3]: research_symbols.append(row[3])
            
        queued = state.get("queued_orders", [])
        queued_syms = [q["symbol"] for q in queued]
        
        all_syms = list(set(symbols_to_check + research_symbols[:50] + queued_syms))
        prices = get_live_prices(all_syms)
        
        if not prices:
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[3]: prices[row[3]] = row[10]
            
        current_equity = state["balance"]
        for sym, pos in state["positions"].items():
            price = prices.get(sym, pos["cost"])
            current_equity += pos["qty"] * price
        state["equity"] = round(current_equity, 0)

        # 0. Execute QUEUED ORDERS (Strategic Overrides with Volatility Sizing)
        if queued:
            print("🤖 AI EXECUTING QUEUED STRATEGIC ORDERS...")
            for order in queued:
                sym = order["symbol"]
                price = prices.get(sym, 0)
                if price <= 0: continue
                
                if order["type"] == "SELL" and sym in state["positions"]:
                    pos = state["positions"].pop(sym)
                    proceeds = pos["qty"] * price
                    state["balance"] += proceeds
                    tx = {
                        "date": today, "time": now_time, "type": "SELL", 
                        "symbol": sym, "price": price, "qty": pos["qty"], 
                        "pnl": round((price - pos["cost"]) * pos["qty"], 2),
                        "details": f"Queued Sell: {order['reason']}"
                    }
                    state["history"].append(tx)
                    new_transactions.append(tx)
                    print(f"🤖 AI QUEUED SELL EXECUTED: {sym} at ${price} (PnL: ${tx['pnl']})")
                    
                elif order["type"] == "BUY" and sym not in state["positions"]:
                    max_positions = rules["max_positions"]
                    available_slots = max_positions - len(state["positions"])
                    if state["balance"] > 500 and available_slots > 0:
                        max_allocation = state["equity"] * rules["max_allocation_pct"]
                        cash_to_use = min(state["balance"] / available_slots, max_allocation)
                        
                        qty = int(cash_to_use // price)
                        if qty > 0:
                            cost = qty * price
                            state["balance"] -= cost
                            
                            # Volatility-Based Stop Loss customized by profile
                            atr = risk_utils.calculate_atr(sym)
                            if atr and atr > 0:
                                stop_loss = round(price - (rules["atr_multiplier"] * atr), 2)
                                stop_desc = f"ATR-based Stop: ${stop_loss} ({rules['atr_multiplier']} * ATR)"
                            else:
                                stop_loss = round(price * 0.92, 2)
                                stop_desc = f"8% Fallback Stop: ${stop_loss}"
                                
                            state["positions"][sym] = {"qty": qty, "cost": price, "stop_loss": stop_loss}
                            tx = {
                                "date": today, "time": now_time, "type": "BUY", 
                                "symbol": sym, "price": price, "qty": qty,
                                "details": f"Queued Buy: {order['reason']} ({stop_desc})"
                            }
                            state["history"].append(tx)
                            new_transactions.append(tx)
                            print(f"🤖 AI QUEUED BUY EXECUTED: {qty} shares of {sym} at ${price} ({stop_desc})")
            
            state["queued_orders"] = []

        # SELL logic (standard technical decay)
        symbols_to_sell = []
        for sym in list(state["positions"].keys()):
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[3] == sym:
                    s10 = row[24] or 0
                    l60 = row[25] or 0
                    if (s10 + l60) < 0:
                        symbols_to_sell.append(sym)
                    break
        
        for sym in symbols_to_sell:
            pos = state["positions"].pop(sym)
            price = prices.get(sym, pos["cost"])
            proceeds = pos["qty"] * price
            state["balance"] += proceeds
            tx = {"date": today, "time": now_time, "type": "SELL", "symbol": sym, "price": price, "qty": pos["qty"], "pnl": round((price - pos["cost"]) * pos["qty"], 2)}
            state["history"].append(tx)
            new_transactions.append(tx)
            print(f"🤖 AI LIVE SELL: {sym} at ${price} (Time: {now_time})")

        # BUY logic (filtered by profile momentum threshold)
        max_positions = rules["max_positions"]
        available_slots = max_positions - len(state["positions"])
        
        # Enforce defensive cash buffer
        min_cash_required = state["equity"] * rules["cash_buffer_pct"]
        
        if available_slots > 0 and state["balance"] > min_cash_required:
            top_buys = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                sym = row[3]
                if not sym: continue
                setup = str(row[20] or '')
                price = prices.get(sym, 0)
                total_score = (row[24] or 0) + (row[25] or 0)
                
                # Filter by strategy profile threshold
                if (setup in ('1', 'OK', 1)) and sym not in state["positions"] and price > 0:
                    if total_score >= rules["min_score_threshold"]:
                        top_buys.append({
                            "sym": sym,
                            "price": price,
                            "total": total_score
                        })
            
            top_buys.sort(key=lambda x: x["total"], reverse=True)
            
            for buy in top_buys[:available_slots]:
                # Re-check cash buffer before buying
                if state["balance"] - (int((state["balance"] / available_slots) // buy["price"]) * buy["price"]) >= min_cash_required:
                    is_verified, v_msg = backtrack_verify(buy["sym"])
                    if not is_verified:
                        print(f"🛑 AI BUY REJECTED: {buy['sym']} - {v_msg}")
                        continue

                    cash_per_buy = (state["balance"] - min_cash_required) / available_slots
                    qty = int(cash_per_buy // buy["price"])
                    if qty > 0:
                        cost = qty * buy["price"]
                        state["balance"] -= cost
                        
                        atr = risk_utils.calculate_atr(buy["sym"])
                        if atr and atr > 0:
                            stop_loss = round(buy["price"] - (rules["atr_multiplier"] * atr), 2)
                            stop_desc = f"ATR-based Stop: ${stop_loss}"
                        else:
                            stop_loss = round(buy["price"] * 0.92, 2)
                            stop_desc = "8% Fallback"
                            
                        state["positions"][buy["sym"]] = {"qty": qty, "cost": buy["price"], "stop_loss": stop_loss}
                        tx = {"date": today, "time": now_time, "type": "BUY", "symbol": buy["sym"], "price": buy["price"], "qty": qty}
                        state["history"].append(tx)
                        new_transactions.append(tx)
                        print(f"🤖 AI LIVE BUY: {qty} shares of {buy['sym']} at ${buy['price']} ({stop_desc})")

    except Exception as e:
        print(f"⚠️ SCRIPT ERROR: {e}")
        pass
    finally:
        save_game(state)
        update_excel_log(state, new_transactions)
        print(f"🤖 AI Portfolio Value: ${state['equity']} (Cash: ${round(state['balance'], 2)})")

def deduct_operational_costs(amount):
    if amount <= 0: return
    state = load_game()
    state["balance"] -= amount
    state["total_ops_cost"] = round(state.get("total_ops_cost", 0) + amount, 4)
    state["history"].append({
        "date": str(datetime.date.today()),
        "type": "COST_DEDUCTION",
        "symbol": "OPS",
        "price": amount,
        "details": "Token & API fees"
    })
    save_game(state)
    print(f"💸 AI Account debited ${round(amount, 4)} for operational costs.")

def show_report():
    state = load_game()
    print("--- 🤖 AI PORTFOLIO MANAGER REPORT ---")
    print(f"Active Strategy: {state.get('profile', 'BALANCED')}")
    print(f"Current Equity:  ${state['equity']}")
    print(f"Cash Balance:    ${round(state['balance'], 2)}")
    print(f"Total Ops Cost:  ${state.get('total_ops_cost', 0)}")
    print(f"Open Positions:  {len(state['positions'])}")
    for sym, pos in state["positions"].items():
        print(f"  - {sym}: {pos['qty']} @ ${pos['cost']} (Stop: ${pos.get('stop_loss', 'N/A')})")
    
    profit = state['equity'] - INITIAL_BALANCE
    print(f"Net Profit:      ${round(profit, 2)} ({round((profit/INITIAL_BALANCE)*100, 2)}%)")
    target = INITIAL_BALANCE * 2
    days_elapsed = (datetime.date.today() - datetime.datetime.strptime(state["start_date"], "%Y-%m-%d").date()).days
    print(f"Goal Progress:   {round((profit/INITIAL_BALANCE)*100, 1)}% of 100% (Target: ${target})")
    print(f"Days Active:     {days_elapsed} / 90")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Execute AI daily moves")
    parser.add_argument("--profile", type=str, help="Manually override strategy profile (AGGRESSIVE, DEFENSIVE, BALANCED)")
    parser.add_argument("--force", action="store_true", help="Force run outside market hours")
    parser.add_argument("--report", action="store_true", help="Show CLI performance report")
    parser.add_argument("--summary", action="store_true", help="Send daily email summary")
    args = parser.parse_args()

    if args.run:
        run_daily_ai_management(force=args.force, manual_profile=args.profile)
    elif args.summary:
        send_daily_summary()
    else:
        show_report()
