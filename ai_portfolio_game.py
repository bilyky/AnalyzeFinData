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

def load_game():
    if not AI_GAME_FILE.exists():
        return {
            "balance": INITIAL_BALANCE,
            "equity": INITIAL_BALANCE,
            "positions": {},
            "history": [],
            "start_date": str(datetime.date.today())
        }
    with open(AI_GAME_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return {"balance": INITIAL_BALANCE, "equity": INITIAL_BALANCE, "positions": {}, "history": [], "start_date": str(datetime.date.today())}

def save_game(state):
    with open(AI_GAME_FILE, "w") as f:
        json.dump(state, f, indent=4)

def backtrack_verify(symbol):
    """Verify the consistency of a technical setup over the last 3 trading days."""
    try:
        base = BASE_DIR / "Data" / "Symbol" / symbol
        if not base.exists():
            return False, "No local cache found for backtracking."
        
        cache_files = sorted(list(base.glob(f"{symbol}_*.json")), reverse=True)
        if len(cache_files) < 3:
            return False, f"Insufficient history (found {len(cache_files)}/3 days) for verification."
        
        trend = []
        for cf in cache_files[:3]:
            with open(cf, "r") as f:
                data = json.load(f)
                cl = data.get("checklist_stocks", {})
                s10 = float(cl.get("shortTermTechnical", 0) or 0)
                l60 = float(cl.get("longTermTechnical", 0) or 0)
                trend.append(s10 + l60)
        
        avg_score = sum(trend) / 3
        # Rule: Setup must be stable (Average positive) AND improving/holding (Today >= Yesterday)
        if avg_score > 0 and trend[0] >= trend[1]:
            return True, f"Verified trend stability: {trend}"
        else:
            return False, f"Failed backtracking check. Trend: {trend}"
            
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
    """Fetch real-time market prices via E*TRADE."""
    try:
        tokens = etrade.get_tokens(env="production")
        if not tokens:
            return {}
        quotes = etrade.fetch_quotes(tokens, symbols, env="production")
        return quotes
    except Exception as e:
        print(f"Live price fetch failed: {e}")
        return {}

def send_daily_summary():
    """Send an HTML summary of the AI's day."""
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
        <p><b>Date:</b> {today}</p>
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

def run_daily_ai_management(force=False):
    try:
        open_status, msg = is_market_open()
        if not open_status and not force:
            print(f"Aborting AI Move: {msg}")
            return

        state = load_game()
        today = str(datetime.date.today())
        now_time = datetime.datetime.now().strftime("%H:%M:%S")
        new_transactions = []
        
        if not XLSX_FILE.exists():
            print("Workbook not found. AI Management deferred.")
            return

        wb = openpyxl.load_workbook(XLSX_FILE, data_only=True)
        ws = wb["Research"]
        
        symbols_to_check = list(state["positions"].keys())
        research_symbols = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3]: research_symbols.append(row[3])
            
        # Add queued symbols to fetch list
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

        # 0. Execute QUEUED ORDERS (Strategic Overrides with Risk Sizing)
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
                    # Strict Risk Sizing: Max 20% portfolio allocation per trade to minimize loss
                    max_positions = 5
                    available_slots = max_positions - len(state["positions"])
                    if state["balance"] > 500:
                        # Defensive: Cash buffer maintained
                        max_allocation = state["equity"] * 0.20
                        cash_to_use = min(state["balance"] / (available_slots if available_slots > 0 else 1), max_allocation)
                        
                        qty = int(cash_to_use // price)
                        if qty > 0:
                            cost = qty * price
                            state["balance"] -= cost
                            state["positions"][sym] = {"qty": qty, "cost": price, "stop_loss": round(price * 0.95, 2)} # Strict 5% Stop-loss
                            tx = {
                                "date": today, "time": now_time, "type": "BUY", 
                                "symbol": sym, "price": price, "qty": qty,
                                "details": f"Queued Buy: {order['reason']} (Stop-loss set at 5%)"
                            }
                            state["history"].append(tx)
                            new_transactions.append(tx)
                            print(f"🤖 AI QUEUED BUY EXECUTED: {qty} shares of {sym} at ${price} (Strict Stop: ${price * 0.95:.2f})")
            
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

        # BUY logic
        max_positions = 5
        available_slots = max_positions - len(state["positions"])
        
        if available_slots > 0:
            top_buys = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                sym = row[3]
                if not sym: continue
                setup = str(row[20] or '')
                price = prices.get(sym, 0)
                if (setup in ('1', 'OK', 1)) and sym not in state["positions"] and price > 0:
                    top_buys.append({
                        "sym": sym,
                        "price": price,
                        "total": (row[24] or 0) + (row[25] or 0)
                    })
            
            top_buys.sort(key=lambda x: x["total"], reverse=True)
            
            for buy in top_buys[:available_slots]:
                if state["balance"] > 500:
                    # MANDATORY: Backtracking Verification
                    is_verified, v_msg = backtrack_verify(buy["sym"])
                    if not is_verified:
                        print(f"🛑 AI BUY REJECTED: {buy['sym']} - {v_msg}")
                        continue

                    cash_per_buy = state["balance"] / (available_slots if available_slots > 0 else 1)
                    qty = int(cash_per_buy // buy["price"])
                    if qty > 0:
                        cost = qty * buy["price"]
                        state["balance"] -= cost
                        state["positions"][buy["sym"]] = {"qty": qty, "cost": buy["price"]}
                        tx = {"date": today, "time": now_time, "type": "BUY", "symbol": buy["sym"], "price": buy["price"], "qty": qty}
                        state["history"].append(tx)
                        new_transactions.append(tx)
                        print(f"🤖 AI LIVE BUY: {qty} shares of {buy['sym']} at ${buy['price']} (Time: {now_time})")

        save_game(state)
        update_excel_log(state, new_transactions)
        print(f"🤖 AI Portfolio Value: ${state['equity']} (Cash: ${round(state['balance'], 2)})")
    
    except Exception as e:
        print(f"⚠️ SCRIPT ERROR: {e}")
        pass

def deduct_operational_costs(amount):
    """Subtract operational costs (tokens, fees) from the virtual cash balance."""
    if amount <= 0: return
    state = load_game()
    state["balance"] -= amount
    # Track cumulative costs
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
    print(f"Current Equity: ${state['equity']}")
    print(f"Cash Balance:   ${round(state['balance'], 2)}")
    print(f"Total Ops Cost: ${state.get('total_ops_cost', 0)}")
    print(f"Open Positions: {len(state['positions'])}")
    for sym, pos in state["positions"].items():
        print(f"  - {sym}: {pos['qty']} @ ${pos['cost']}")
    
    # Net Profit = Current Equity - Initial Balance
    profit = state['equity'] - INITIAL_BALANCE
    print(f"Net Profit:     ${round(profit, 2)} ({round((profit/INITIAL_BALANCE)*100, 2)}%)")
    
    target = INITIAL_BALANCE * 2
    days_elapsed = (datetime.date.today() - datetime.datetime.strptime(state["start_date"], "%Y-%m-%d").date()).days
    print(f"Goal Progress:  {round((profit/INITIAL_BALANCE)*100, 1)}% of 100% (Target: ${target})")
    print(f"Days Active:    {days_elapsed} / 90")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Execute AI daily moves")
    parser.add_argument("--force", action="store_true", help="Force run outside market hours")
    parser.add_argument("--report", action="store_true", help="Show CLI performance report")
    parser.add_argument("--summary", action="store_true", help="Send daily email summary")
    args = parser.parse_args()

    if args.run:
        run_daily_ai_management(force=args.force)
    elif args.summary:
        send_daily_summary()
    else:
        show_report()
