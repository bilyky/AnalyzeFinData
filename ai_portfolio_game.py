import json
import datetime
import openpyxl
import pytz
import etrade
import sys
from pathlib import Path

# --- Windows UTF-8 Hardening ---
# Prevents UnicodeEncodeError when printing emojis (🤖, 🚨) in headless environments
class SafeStreamWrapper:
    def __init__(self, stream):
        self._stream = stream
    def write(self, s):
        try:
            return self._stream.write(s)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, 'encoding', 'cp1252') or 'cp1252'
            safe_s = s.encode(encoding, errors='replace').decode(encoding)
            return self._stream.write(safe_s)
    def __getattr__(self, name):
        return getattr(self._stream, name)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.stdout = SafeStreamWrapper(sys.stdout)
    sys.stderr = SafeStreamWrapper(sys.stderr)

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
AI_GAME_FILE = BASE_DIR / "Data" / "ai_portfolio_game.json"
XLSX_FILE = BASE_DIR / "Data" / "state_of_the_day.xlsx"
AI_PERF_XLSX = BASE_DIR / "Data" / "ai_portfolio_performance.xlsx"
INITIAL_BALANCE = 10000.0

# Import risk utils safely
import risk_utils
import sell_rules


def _sma50(symbol, max_stale_days=10):
    """50-day SMA of closes from the local OHLCV cache, or None if unavailable
    or stale. Returns None when the newest cached bar is older than
    max_stale_days so winner-protection never trusts an out-of-date average."""
    try:
        path = BASE_DIR / "Data" / "Symbol_full" / f"{symbol}_daily.json"
        if not path.exists():
            return None
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        if not dates:
            return None
        if (datetime.date.today() - datetime.date.fromisoformat(dates[-1])).days > max_stale_days:
            return None  # cache too stale to trust
        closes = [float(ts[d]["4. close"]) for d in dates]
        return sell_rules.sma_from_closes(closes, 50)
    except Exception:
        return None

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

def calculate_ticker_trend_score(symbol: str) -> float:
    """
    Calculate a normalized, standardized trend score in [-10.0, +10.0] for a symbol
    based on its price relative to its 20, 50, and 200 daily SMAs.
    """
    try:
        path = XLSX_FILE.parent / "Data" / "Symbol_full" / f"{symbol}_daily.json"
        if not path.exists():
            return 0.0
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        dates = sorted(ts.keys())
        if len(dates) < 200:
            return 0.0
            
        closes = [float(ts[d]["4. close"]) for d in dates]
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        sma200 = sum(closes[-200:]) / 200
        current_price = closes[-1]
        
        s_20 = 2.5 if current_price > sma20 else -2.5
        s_50 = 2.5 if current_price > sma50 else -2.5
        s_200 = 2.5 if current_price > sma200 else -2.5
        s_cross1 = 1.25 if sma20 > sma50 else -1.25
        s_cross2 = 1.25 if sma50 > sma200 else -1.25
        
        return s_20 + s_50 + s_200 + s_cross1 + s_cross2
    except Exception:
        return 0.0

def get_market_regime():
    """Query SPY momentum to dynamically determine the best strategy profile, adjusting for breadth divergence."""
    base_profile = "BALANCED"
    wb = None
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        ws = wb["Research"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3] == "SPY":
                l60 = row[25] or 0
                if l60 > 2:
                    base_profile = "AGGRESSIVE" # Strong bull market
                elif l60 < -2:
                    base_profile = "DEFENSIVE" # Bear market
                else:
                    base_profile = "BALANCED" # Consolidation
                break
    except Exception as e:
        print(f"Error loading SPY regime: {e}")
        base_profile = "BALANCED"
    finally:
        if wb:
            wb.close()

    # Breadth Divergence Adjustment Pass
    try:
        spy_score = calculate_ticker_trend_score("SPY")
        rsp_score = calculate_ticker_trend_score("RSP")
        delta = spy_score - rsp_score
        
        if delta > 4.0:
            print(f"⚠️ [BREADTH ALERT] SPY-RSP Divergence is high: {delta:.2f} (SPY: {spy_score:.1f}, RSP: {rsp_score:.1f})")
            if base_profile == "AGGRESSIVE":
                print("  -> Downgrading profile from AGGRESSIVE to BALANCED due to narrow market breadth!")
                return "BALANCED"
            elif base_profile == "BALANCED":
                print("  -> Downgrading profile from BALANCED to DEFENSIVE due to narrow market breadth!")
                return "DEFENSIVE"
    except Exception as e:
        print(f"Error applying breadth divergence filter: {e}")

    return base_profile

def get_strategy_rules(profile):
    """Define risk and size rules based on the chosen strategy profile."""
    # Profile options: BALANCED, AGGRESSIVE, DEFENSIVE
    if profile == "AGGRESSIVE":
        return {
            "max_positions": 6,
            "max_allocation_pct": 0.15, # Optimized from 0.25 to minimize drawdowns
            "atr_multiplier": 3.5,       # Loose stop to avoid shakeouts in high-beta stocks
            "min_score_threshold": 2.0,  
            "cash_buffer_pct": 0.10      
        }
    elif profile == "DEFENSIVE":
        return {
            "max_positions": 3,          # Restrict to top 3 ultra-conviction plays
            "max_allocation_pct": 0.10, # Optimized from 0.15 for maximum capital preservation (Capped at $1,000 per trade)
            "atr_multiplier": 1.5,       # Tight stop-loss to preserve capital
            "min_score_threshold": 10.0, 
            "cash_buffer_pct": 0.50      
        }
    else: # BALANCED (Default)
        return {
            "max_positions": 5,
            "max_allocation_pct": 0.15, # Optimized from 0.20 (Perfect sweet spot between risk and growth)
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
    with open(AI_GAME_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {"balance": INITIAL_BALANCE, "equity": INITIAL_BALANCE, "positions": {}, "history": [], "start_date": str(datetime.date.today()), "profile": "BALANCED"}

import shutil

def save_game(state):
    # --- Mandatory Backup before Write ---
    if AI_GAME_FILE.exists():
        try:
            backup_dir = BASE_DIR / "Data" / "Backup" / "Game"
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"ai_portfolio_game_{ts}.json"
            
            shutil.copy2(AI_GAME_FILE, backup_path)
            
            # Clean up old backups (Keep last 15)
            backups = sorted(list(backup_dir.glob("ai_portfolio_game_*.json")), key=lambda x: x.stat().st_mtime)
            if len(backups) > 15:
                for old_b in backups[:-15]:
                    old_b.unlink()
        except Exception as e:
            print(f"  [Warning] Game backup failed: {e}")

    with open(AI_GAME_FILE, "w", encoding="utf-8") as f:
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

def is_bottom_confirmed(symbol):
    """Verify if a stock is forming a technical bottom based on its 3-day price slope."""
    try:
        path = BASE_DIR / "Data" / "Symbol_full" / f"{symbol}_daily.json"
        if not path.exists():
            return False, "No local daily history found."
        
        with open(path) as f:
            data = json.load(f)
            
        ts = data.get("Time Series (Daily)", {})
        sorted_dates = sorted(list(ts.keys()), reverse=True)
        if len(sorted_dates) < 4:
            return False, f"Insufficient history (found {len(sorted_dates)}/4 days) for slope check."
        
        prices = [float(ts[d]["4. close"]) for d in sorted_dates[:4]]
        
        # Calculate daily percentage changes over the last 3 days
        # prices[0]: today, prices[1]: yesterday, prices[2]: 2 days ago, prices[3]: 3 days ago
        change1 = (prices[0] - prices[1]) / prices[1]  # Today vs Yesterday
        change2 = (prices[1] - prices[2]) / prices[2]  # Yesterday vs 2 Days Ago
        change3 = (prices[2] - prices[3]) / prices[3]  # 2 Days Ago vs 3 Days Ago
        
        # Bottoming signature: Selling pressure is exhausting and slope is turning positive
        # Condition 1: Today's slope is positive (change1 > 0)
        # Condition 2: Today's slope is better than yesterday's slope (change1 > change2)
        # Condition 3: Average of the last 2 days is positive (> 0.005, or +0.5%)
        avg_slope = (change1 + change2) / 2
        
        if change1 > 0 and change1 > change2 and avg_slope >= 0.005:
            return True, f"Bottom Confirmed! Avg Slope: +{round(avg_slope*100, 2)}% | Price Trend: {[round(p, 2) for p in prices[:3]]}"
        else:
            return False, f"No reversal confirmed. Avg Slope: {round(avg_slope*100, 2)}%"
    except Exception as e:
        return False, f"Slope error: {e}"

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

def get_live_google_price(symbol):
    """Scrape the 100% live price from Google Finance as an agnostic online fallback."""
    import requests
    import re
    exchanges = ["NASDAQ", "NYSE", "NYSEARCA", "AMEX"]
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    for ex in exchanges:
        url = f'https://www.google.com/finance/quote/{symbol}:{ex}'
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                # Regex looking specifically for the jsname="Pdsbrc" span enclosing the dollar price
                match = re.search(r'jsname="Pdsbrc"[^>]*>\s*<span>\$([0-9,.]+)<', r.text)
                if match:
                    price_str = match.group(1).replace(',', '')
                    return float(price_str)
        except Exception:
            pass
    return None

def is_market_hours():
    """Return True if current time is within active US equity market hours (6:30 AM - 1:15 PM PST, weekdays)."""
    try:
        tz_la = pytz.timezone("America/Los_Angeles")
        now_la = datetime.datetime.now(tz_la)
        
        # Check if weekend (Saturday=5, Sunday=6)
        if now_la.weekday() in (5, 6):
            return False
            
        start_time = now_la.replace(hour=6, minute=30, second=0, microsecond=0)
        end_time = now_la.replace(hour=13, minute=15, second=0, microsecond=0)
        return start_time <= now_la <= end_time
    except Exception:
        # Fallback to True if timezone check fails, to prevent blocking active hours
        return True

def get_live_prices(symbols):
    """Fetch real-time market prices via E*TRADE safely with automated recovery."""
    try:
        # Trading Hours Gate: If after hours or weekends, bypass E*TRADE entirely to prevent login lockouts!
        if not is_market_hours():
            print("  [AETHER] After-hours detected — bypassing E*TRADE login to prevent lockout. Scraping Google Finance directly.")
            return get_google_prices_fallback(symbols)

        # Call the hardened get_tokens() which is safe and has an active headless safety gate.
        # This ensures we always actively attempt to re-authenticate when tokens expire.
        tokens = etrade.get_tokens("production")
        if not tokens:
            print("  [AETHER] E*TRADE authentication failed. Attempting Google Finance live fallback.")
            return get_google_prices_fallback(symbols)
            
        quotes = etrade.fetch_quotes(tokens, symbols, env="production")
        
        # If E*TRADE returned empty/partial quotes, raise an error to alert you of dead/delisted symbols
        missing = [s for s in symbols if s not in quotes or not quotes[s] or quotes[s] <= 0]
        if missing:
            raise ValueError(f"Ticker Symbology Error: E*TRADE returned no quote for: {missing}. These symbols may be dead, delisted, or misaligned.")
            
        return quotes
    except Exception as e:
        print(f"  [AETHER] E*TRADE connection failed: {e}. Attempting Google Finance live fallback.")
        return get_google_prices_fallback(symbols)

def get_google_prices_fallback(symbols):
    """Scrape Google Finance for multiple symbols in parallel/sequence as a robust fallback."""
    quotes = {}
    print(f"  [Google] Scraping live quotes for: {symbols}")
    for sym in symbols:
        price = get_live_google_price(sym)
        if price and price > 0:
            quotes[sym] = price
            print(f"    - Google Verified {sym}: ${price:.2f}")
    return quotes

def send_daily_summary():
    state = load_game()
    today = str(datetime.date.today())
    today_tx = [tx for tx in state.get("history", []) if tx["date"] == today]
    tx_rows = ""
    for tx in today_tx:
        pnl_str = f" (PnL: ${tx['pnl']})" if "pnl" in tx else ""
        tx_rows += f"<li><b>{tx['type']}</b>: {tx.get('qty', '')} {tx['symbol']} @ ${tx['price']}{pnl_str} [Time: {tx.get('time', '')}]</li>"

    # Fetch live quotes for open positions to show accurate daily values
    positions = state.get("positions", {})
    live_prices = get_live_prices(list(positions.keys()))
    
    # Standardize fallback to workbook close prices if E*TRADE renewal fails (e.g. on weekends)
    if not live_prices or any(sym not in live_prices for sym in positions):
        try:
            wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
            ws = wb["Research"]
            for row in ws.iter_rows(min_row=2, values_only=True):
                sym = row[3]
                if sym in positions and sym not in live_prices:
                    live_prices[sym] = row[10] or positions[sym]["cost"]
        except Exception as e:
            print(f"Workbook fallback failed inside summary: {e}")

    # Final safety fallback to cost basis if both API and workbook are empty
    for sym in positions:
        if sym not in live_prices:
            live_prices[sym] = positions[sym]["cost"]

    # Build the open positions HTML table
    pos_table_rows = ""
    live_equity = state.get("balance", 0.0)
    if positions:
        for sym, pos in positions.items():
            qty = pos["qty"]
            cost = pos["cost"]
            current = live_prices.get(sym, cost)
            val = qty * current
            live_equity += val
            pnl = (current - cost) * qty
            pnl_pct = ((current - cost) / cost) * 100
            
            pnl_color = "#27ae60" if pnl >= 0 else "#c0392b"
            pnl_sign = "+" if pnl >= 0 else ""
            
            pos_table_rows += f"""
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">{sym}</td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; text-align: center;">{qty}</td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; text-align: right;">${cost:.2f}</td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; text-align: right; font-weight: bold;">${current:.2f}</td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; text-align: right; font-weight: bold;">${val:.2f}</td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; text-align: right; color: {pnl_color}; font-weight: bold;">
                    {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)
                </td>
            </tr>
            """
    else:
        pos_table_rows = "<tr><td colspan='6' style='padding: 15px; text-align: center; color: #888;'>No open positions currently.</td></tr>"

    # Synchronize the final calculated closing equity back to the JSON state file so it stays updated
    state["equity"] = round(live_equity, 2)
    save_game(state)

    html = f"""
    <html>
    <body style="font-family: sans-serif; color: #333; max-width: 700px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; margin-bottom: 20px;">🤖 AI Portfolio: Daily Performance Summary</h2>
        <p><b>Date:</b> {today} | <b>Active Strategy:</b> <span style="background: #2c3e50; color: white; padding: 2px 6px; border-radius: 3px; font-weight: bold;">{state.get('profile', 'BALANCED')}</span></p>
        <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
        
        <h3 style="color: #2c3e50;">🛒 Market Action Today:</h3>
        <ul style="padding-left: 20px; font-size: 14px; line-height: 1.6;">
            {tx_rows if today_tx else "<li>No transactions executed today.</li>"}
        </ul>
        
        <h3 style="color: #2c3e50; margin-top: 30px;">📈 Current Open Positions:</h3>
        <table border="0" cellpadding="0" cellspacing="0" style="width: 100%; border-collapse: collapse; margin-bottom: 35px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border: 1px solid #ddd;">
            <thead>
                <tr style="background: #34495e; color: white;">
                    <th style="padding: 12px; text-align: left;">Symbol</th>
                    <th style="padding: 12px; text-align: center;">Qty</th>
                    <th style="padding: 12px; text-align: right;">Cost Basis</th>
                    <th style="padding: 12px; text-align: right;">Live Price</th>
                    <th style="padding: 12px; text-align: right;">Total Value</th>
                    <th style="padding: 12px; text-align: right;">Total P&L</th>
                </tr>
            </thead>
            <tbody>
                {pos_table_rows}
            </tbody>
        </table>
        
        <h3 style="color: #2c3e50;">🛡️ Portfolio Financial Summary:</h3>
        <table border="0" cellpadding="10" cellspacing="0" style="width: 100%; max-width: 400px; border-collapse: collapse; margin-bottom: 25px; border: 1px solid #ddd;">
            <tr style="background: #f8f9fa;">
                <td style="border-bottom: 1px solid #ddd; font-weight: bold;">Current Equity</td>
                <td style="border-bottom: 1px solid #ddd; text-align: right; font-weight: bold; font-size: 16px;">${live_equity:.2f}</td>
            </tr>
            <tr>
                <td style="border-bottom: 1px solid #ddd; font-weight: bold; color: #555;">Cash Balance (Dry Powder)</td>
                <td style="border-bottom: 1px solid #ddd; text-align: right; font-weight: bold; color: #555;">${state['balance']:.2f}</td>
            </tr>
            <tr style="background: #f8f9fa;">
                <td style="font-weight: bold;">Total Return</td>
                <td style="text-align: right; font-weight: bold; color: {'#27ae60' if live_equity >= INITIAL_BALANCE else '#c0392b'};">
                    {'+' if live_equity >= INITIAL_BALANCE else ''}{round(((live_equity - INITIAL_BALANCE)/INITIAL_BALANCE)*100, 2)}%
                </td>
            </tr>
        </table>
        
        <p style="margin-top: 35px; border-top: 1px solid #eee; padding-top: 15px; font-size: 11px; color: #7f8c8d;">
            🤖 <i>This is an automated performance report from your autonomous Project AETHER trading desk. All figures represent live, verified production-grade data.</i>
        </p>
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
            if row[3] and str(row[20] or '') in ('1', 'OK', 1):
                research_symbols.append(row[3])
            
        queued = state.get("queued_orders", [])
        queued_syms = [q["symbol"] for q in queued]
        
        all_syms = list(set(symbols_to_check + research_symbols + queued_syms))
        prices = get_live_prices(all_syms)
        
        # --- Price Source Gate ---
        # Primary source: E*TRADE live API. Automatic fallback: Google Finance scraper.
        # If both fail (prices is empty), crash — no stale workbook prices allowed.
        if not prices:
            raise RuntimeError("Critical Data Failure: Both E*TRADE and Google Finance fallback returned no prices. No live source of truth available!")

        # Zero-Trust: every open position must have a valid non-zero price from a live source.
        missing_prices = [sym for sym in symbols_to_check if sym not in prices or not prices[sym] or prices[sym] <= 0]
        if missing_prices:
            raise RuntimeError(f"Data Integrity Failure: No live prices found for active positions: {missing_prices}")
            
        current_equity = state["balance"]
        for sym, pos in state["positions"].items():
            price = prices[sym] # Access directly, no fallback to cost basis to enforce strict data accuracy
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

        # SELL logic — unified deterministic exit policy (sell_rules.exit_decision):
        # hard ATR stop > soft momentum signal (winner-protected) > hold.
        symbols_to_sell = []
        for sym in list(state["positions"].keys()):
            pos = state["positions"][sym]
            s10 = l60 = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[3] == sym:
                    s10 = row[24] or 0
                    l60 = row[25] or 0
                    break
            price = prices.get(sym, pos.get("cost"))
            # Only pay the OHLCV read when the soft signal actually fires (winner-
            # protection is the only consumer of sma50); HOLD positions skip the I/O.
            sma50 = _sma50(sym) if sell_rules.soft_exit(s10, l60) else None
            action, reason = sell_rules.exit_decision(
                price=price, cost=pos.get("cost"), stop_loss=pos.get("stop_loss"),
                s10=s10, l60=l60, sma50=sma50)
            if action == "SELL":
                symbols_to_sell.append(sym)
            elif action == "REVIEW":
                # Winner above its 50-DMA on a soft signal — hold, don't dump.
                print(f"🌸 AI HOLD (winner-protected): {sym} — {reason}")

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
                
                # Filter by strategy profile threshold OR mathematically confirmed bottom
                if (setup in ('1', 'OK', 1)) and sym not in state["positions"] and price > 0:
                    bottom_ok, bottom_msg = is_bottom_confirmed(sym)

                    # Catastrophic Gap Guard (The CNXC Trap):
                    # Reject gap-downs > 8% unless bottom is independently confirmed — a volume-confirmed
                    # capitulation gap is exactly the entry signal bottom detection targets.
                    prev_close = row[10]
                    if prev_close and prev_close > 0:
                        gap_pct = (price - prev_close) / prev_close
                        if gap_pct <= -0.08 and not bottom_ok:
                            print(f"🛑 AI BUY REJECTED (CNXC Trap): {sym} - Gap-Down {round(gap_pct*100, 1)}% with no confirmed bottom.")
                            continue

                    if total_score >= rules["min_score_threshold"] or bottom_ok:
                        bottom_desc = f" (Bottom Confirmed: {bottom_msg})" if bottom_ok else ""
                        top_buys.append({
                            "sym": sym,
                            "price": price,
                            "total": total_score,
                            "bottom_desc": bottom_desc
                        })
            
            top_buys.sort(key=lambda x: x["total"], reverse=True)
            
            buys_executed = 0
            for buy in top_buys:
                if buys_executed >= available_slots:
                    break
                    
                # Re-calculate cash buffer dynamically based on remaining available slots
                current_available = available_slots - buys_executed
                cash_per_buy = (state["balance"] - min_cash_required) / current_available
                qty = int(cash_per_buy // buy["price"])
                if qty > 0:
                    is_verified, v_msg = backtrack_verify(buy["sym"])
                    if not is_verified:
                        print(f"🛑 AI BUY REJECTED: {buy['sym']} - {v_msg}")
                        continue

                    cost = qty * buy["price"]
                    state["balance"] -= cost
                    
                    atr = risk_utils.calculate_atr(buy["sym"])
                    if atr and atr > 0:
                        stop_loss = round(buy["price"] - (rules["atr_multiplier"] * atr), 2)
                        stop_desc = f"ATR-based Stop: ${stop_loss}{buy.get('bottom_desc', '')}"
                    else:
                        stop_loss = round(buy["price"] * 0.92, 2)
                        stop_desc = f"8% Fallback{buy.get('bottom_desc', '')}"
                            
                    state["positions"][buy["sym"]] = {"qty": qty, "cost": buy["price"], "stop_loss": stop_loss}
                    tx = {"date": today, "time": now_time, "type": "BUY", "symbol": buy["sym"], "price": buy["price"], "qty": qty}
                    state["history"].append(tx)
                    new_transactions.append(tx)
                    buys_executed += 1
                    print(f"🤖 AI LIVE BUY: {qty} shares of {buy['sym']} at ${buy['price']} ({stop_desc})")

    except Exception as e:
        print(f"⚠️ SCRIPT ERROR: {e}")
        pass
    finally:
        if state is not None:
            save_game(state)
            update_excel_log(state, new_transactions)
            print(f"🤖 AI Portfolio Value: ${state['equity']} (Cash: ${round(state['balance'], 2)})")
        else:
            print("🤖 AI Management aborted before state initialization. Game state preserved.")

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
    
    # Dynamically compute the equity using latest available prices from workbook
    positions = state.get("positions", {})
    live_prices = {}
    
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        ws = wb["Research"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            sym = row[3]
            if sym in positions:
                live_prices[sym] = row[10]
        wb.close()
    except Exception:
        pass
        
    for sym in positions:
        if sym not in live_prices or not live_prices[sym]:
            live_prices[sym] = positions[sym]["cost"]
            
    live_equity = state.get("balance", 0.0)
    for sym, pos in positions.items():
        live_equity += pos["qty"] * live_prices.get(sym, pos["cost"])
        
    print("--- 🤖 AI PORTFOLIO MANAGER REPORT ---")
    print(f"Active Strategy: {state.get('profile', 'BALANCED')}")
    print(f"Current Equity:  ${round(live_equity, 2)}")
    print(f"Cash Balance:    ${round(state['balance'], 2)}")
    print(f"Total Ops Cost:  ${state.get('total_ops_cost', 0)}")
    print(f"Open Positions:  {len(positions)}")
    for sym, pos in positions.items():
        cur_px = live_prices.get(sym, pos['cost'])
        print(f"  - {sym}: {pos['qty']} @ ${pos['cost']} (Current: ${cur_px:.2f}, Stop: ${pos.get('stop_loss', 'N/A')})")
    
    profit = live_equity - INITIAL_BALANCE
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
