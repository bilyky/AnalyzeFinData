import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import openpyxl
import etrade
import notify
import time
import datetime
from pathlib import Path

from data_api import _SL  # canonical Short_Long column map (single source of truth)
from ai_portfolio_game import get_live_prices

XLSX_FILE = Path("Data/state_of_the_day.xlsx")

def get_monitored_positions():
    """Load positions and stops from Short_Long sheet."""
    positions = []
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, data_only=True, read_only=True)
        if "Short_Long" not in wb.sheetnames:
            return []
        
        ws = wb["Short_Long"]
        # Column indices come from the canonical map: sym=1, stop=6.
        # (Previously read row[4] = the "Top" column — a bug; Stop is index 6.)
        # Type-guard: the sheet has two tables separated by blank + repeated
        # "Symb"/"Stop" header rows — skip anything non-numeric so BOTH accounts load.
        for row in ws.iter_rows(min_row=3, values_only=True):
            sym  = row[_SL["sym"]]  if len(row) > _SL["sym"]  else None
            stop = row[_SL["stop"]] if len(row) > _SL["stop"] else None
            if (isinstance(sym, str) and sym.strip() and sym.strip() != "Symb"
                    and isinstance(stop, (int, float)) and stop > 0):
                positions.append({"symbol": sym.strip().upper(), "stop": stop})
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

    symbols = [p["symbol"] for p in monitored]
    quotes = get_live_prices(symbols)

    breaches = []
    for p in monitored:
        sym = p["symbol"]
        stop = p["stop"]
        last_price = quotes.get(sym)
        if last_price and last_price > 0:
            if last_price <= stop:
                msg = f"URGENT: {sym} breached stop! Price: {last_price:.2f}, Stop: {stop:.2f}"
                print(msg)
                breaches.append(msg)
        else:
            print(f"Warning: Could not fetch price for {sym}")

    if breaches:
        count = len(breaches)
        subject = f"AETHER ALERT: Stop Breach Detected ({count} Position{'s' if count > 1 else ''})"
        body = "The following stop breaches have been detected in your active E*TRADE portfolio:\n\n" + "\n".join(breaches) + "\n\nAction required: Please review and execute these exits manually in your broker."
        notify.send_email(subject, body)
        print(f"Sent consolidated alert email for {count} breaches.")
    else:
        print("No stop breaches detected.")

if __name__ == "__main__":
    monitor()
