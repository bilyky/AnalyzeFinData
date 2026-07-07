import os
import openpyxl
import etrade
import notify
import ai_portfolio_game
from pathlib import Path

def run_real_copilot_audit():
    print("[Shadow Copilot] Starting real-account risk and buy audit...")
    
    # 1. Load today's Research technical scores
    XLSX_FILE = Path("Data/state_of_the_day.xlsx")
    if not XLSX_FILE.exists():
        print("[Shadow Copilot] Error: state_of_the_day.xlsx not found.")
        return
        
    wb = openpyxl.load_workbook(XLSX_FILE, data_only=True, read_only=True)
    try:
        ws = wb["Research"]

        scores = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            sym = row[3]
            if sym:
                scores[sym] = {
                    "pgr": row[6] or "N/A",
                    "s10": row[24] or 0.0,
                    "l60": row[25] or 0.0,
                    "total": (row[24] or 0.0) + (row[25] or 0.0),
                    "setup": str(row[20] or ''),
                    "ind": row[4] or "Unknown"
                }
    finally:
        wb.close()

    # 2. Connect to E*TRADE and fetch actual holdings
    try:
        tokens = etrade.get_tokens("production")
        if not tokens:
            print("[Shadow Copilot] Error: Could not obtain E*TRADE tokens.")
            return
        positions = etrade.fetch_positions(tokens, "production")
    except Exception as e:
        print(f"[Shadow Copilot] E*TRADE connection failed: {e}")
        return

    # 3. Analyze holdings for critical SELL signals (Score < 0)
    sell_tickets = []
    for pos in positions:
        sym = pos["symbol"]
        qty = pos["qty"]
        price = pos["price"]
        
        if sym in scores:
            sc = scores[sym]
            if sc["total"] < 0:
                sell_tickets.append({
                    "sym": sym, "qty": qty, "price": price, 
                    "total": sc["total"], "s10": sc["s10"], "l60": sc["l60"],
                    "pgr": sc["pgr"], "ind": sc["ind"]
                })

    # 4. Analyze our dynamic A-Reserves (including EWT/EWY) for BUY signals
    # We load reserves from ai_portfolio_game.json
    try:
        state = ai_portfolio_game.load_game()
        reserves = state.get("reserves", ["EIX", "AMAT", "URI", "GEV", "RS"])
    except Exception as e:
        print(f"[Shadow Copilot] Warning: Could not load game state ({e}). Using default reserves.")
        reserves = ["EIX", "AMAT", "URI", "GEV", "RS"]
        
    # Always include EWT and EWY in our copilot watchlist
    if "EWT" not in reserves: reserves.append("EWT")
    if "EWY" not in reserves: reserves.append("EWY")

    buy_tickets = []
    for sym in reserves:
        # Check if they have a confirmed bottom
        bottom_ok, bottom_msg = ai_portfolio_game.is_bottom_confirmed(sym)
        if bottom_ok and sym in scores:
            sc = scores[sym]
            buy_tickets.append({
                "sym": sym, "total": sc["total"], "pgr": sc["pgr"], 
                "ind": sc["ind"], "msg": bottom_msg
            })

    # 5. Format HTML Email Report
    if not sell_tickets and not buy_tickets:
        print("[Shadow Copilot] No action required. All real positions stable, no reserves triggered.")
        return

    html = """
    <html>
    <head>
        <style>
            body { font-family: monospace; background-color: #0d1117; color: #c9d1d9; padding: 20px; }
            h2 { color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
            .ticket { border-radius: 6px; padding: 15px; margin-bottom: 15px; border-left: 5px solid; }
            .sell-ticket { background-color: #3b1e1e; border-color: #f85149; color: #ff7b72; }
            .buy-ticket { background-color: #1e3a1e; border-color: #56d364; color: #7ee787; }
            .title { font-weight: bold; font-size: 16px; margin-bottom: 5px; }
            .meta { font-size: 12px; color: #8b949e; }
        </style>
    </head>
    <body>
        <h2>🛡️ AETHER: Real-Account Shadow Copilot Tickets</h2>
    """

    if sell_tickets:
        html += "<h3>🚨 CRITICAL SELLS (Momentum Decay Detected)</h3>"
        for t in sell_tickets:
            html += f"""
            <div class="ticket sell-ticket">
                <div class="title">SELL {t['sym']} (Position: {t['qty']} shares near ${t['price']:.2f})</div>
                <div><b>Rationale:</b> Technical Momentum collapsed to {t['total']:.1f} (S10/L60: {t['s10']:.1f}/{t['l60']:.1f})!</div>
                <div class="meta">Sector: {t['ind']} | PGR: {t['pgr']}</div>
            </div>
            """

    if buy_tickets:
        html += "<h3>🟢 BUY TRIGGERS (Oversold Bottom Confirmed)</h3>"
        for t in buy_tickets:
            html += f"""
            <div class="ticket buy-ticket">
                <div class="title">BUY {t['sym']} (Reserve Target Active)</div>
                <div><b>Signal:</b> {t['msg']} | Technical Score: {t['total']:.1f}</div>
                <div class="meta">Sector: {t['ind']} | PGR: {t['pgr']}</div>
            </div>
            """

    html += """
        <p class="meta" style="margin-top: 30px;">
            AETHER Shadow Copilot Audit completed autonomously. This is a read-only risk-mitigation report.<br>
            No automated trades have been executed on your real-money brokerage account.
        </p>
    </body>
    </html>
    """

    # 6. Send the Email
    subject = f"🛡️ AETHER Shadow Copilot: {len(sell_tickets)} Sells, {len(buy_tickets)} Buys Triggered"
    notify.send_email(subject, html, is_html=True)
    print(f"[Shadow Copilot] Audit complete. Actionable report dispatched to inbox!")

if __name__ == "__main__":
    run_real_copilot_audit()
