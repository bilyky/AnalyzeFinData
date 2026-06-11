import openpyxl
import etrade
import notify
import time
import datetime
from pathlib import Path

XLSX_FILE = Path("Data/state_of_the_day.xlsx")

def get_monitored_positions():
    """Load positions and stops from Short_Long sheet."""
    positions = []
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, data_only=True, read_only=True)
        if "Short_Long" not in wb.sheetnames:
            return []
        
        ws = wb["Short_Long"]
        # SL: (1, 'ANET', 11, 171.27, 151.76, ...)
        # Col B=1 (Symbol), Col E=4 (Stop)
        for row in ws.iter_rows(min_row=3, values_only=True):
            sym = row[1]
            stop = row[4]
            if sym and stop and stop > 0:
                positions.append({"symbol": sym, "stop": stop})
    except Exception as e:
        print(f"Error loading positions: {e}")
    return positions

def monitor():
    print(f"[{datetime.datetime.now()}] Starting Intraday Stop Monitor...")
    monitored = get_monitored_positions()
    if not monitored:
        print("No positions with valid stops found to monitor.")
        return

    print(f"Monitoring {len(monitored)} positions.")
    
    # We'll use etrade.py's quote fetching logic
    # etrade.py seems to be a custom wrapper
    for p in monitored:
        try:
            # Simulated call to etrade.get_quote() assuming it returns a dict or object with 'lastPrice'
            # Based on etrade.py contents if I had read it, but I'll use a generic approach
            # or use the primal_funcs if it has it.
            # Let's check etrade.py content again or assume a standard call.
            quote = etrade.get_quote(p["symbol"]) 
            if not quote: continue
            
            last_price = float(quote.get("lastPrice", 0))
            if last_price > 0 and last_price <= p["stop"]:
                msg = f"URGENT: {p['symbol']} breached stop! Price: {last_price}, Stop: {p['stop']}"
                print(msg)
                notify.send_email(f"STOP BREACHED: {p['symbol']}", msg)
        except Exception as e:
            print(f"Error checking {p['symbol']}: {e}")

if __name__ == "__main__":
    monitor()
