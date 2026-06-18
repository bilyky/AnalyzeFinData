import json
import datetime
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "Data" / "performance_log.json"

def log_picks(picks):
    """Log daily picks to a JSON file for future verification."""
    today = str(datetime.date.today())
    
    data = {}
    if LOG_FILE.exists():
        with open(LOG_FILE, "r") as f:
            try:
                data = json.load(f)
            except:
                data = {}
    
    # Store minimal data needed for verification
    data[today] = [
        {
            "symbol": p["Symbol"],
            "entry_price": p["Price"],
            "s10": p["S10"],
            "l60": p["L60"],
            "target": p["Target"]
        } for p in picks
    ]
    
    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=4)

if __name__ == "__main__":
    # Test
    log_picks([{"Symbol": "AAPL", "Price": 150.0, "S10": 5.0, "L60": 5.0, "Target": 160.0}])
