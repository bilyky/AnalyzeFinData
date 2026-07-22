import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import datetime
from pathlib import Path
import openpyxl

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
TRACKER_FILE = BASE_DIR / "Data" / "ops_and_rd_tracker.xlsx"
TOKEN_COST_PER_1K = 0.003 # Estimated average cost for Sonnet/Gemini/GPT
CHAIKIN_API_COST_PER_CALL = 0.01 # Placeholder for premium tier if applicable

def log_daily_costs(tokens_used, api_calls=0, saas_fees=0.0):
    """Log the financial footprint of the AI desk."""
    if not TRACKER_FILE.exists():
        return

    today = str(datetime.date.today())
    token_cost = (tokens_used / 1000) * TOKEN_COST_PER_1K
    api_fee = api_calls * CHAIKIN_API_COST_PER_CALL
    total = token_cost + api_fee + saas_fees

    try:
        wb = openpyxl.load_workbook(TRACKER_FILE)
        ws = wb["Resource_Costs"]
        ws.append([today, tokens_used, round(token_cost, 4), round(api_fee, 2), round(saas_fees, 2), round(total, 4)])
        wb.save(TRACKER_FILE)
        print(f"💰 Resource cost logged: ${round(total, 4)}")
    except Exception as e:
        print(f"Cost logging failed: {e}")

def run_learning_session():
    """Analyze historical wins to find 'Out of the Box' strategies."""
    print("🧠 Starting R&D Learning Session...")
    # 1. Load historical performance
    # 2. Cross-reference with detected patterns in patterns.py
    # 3. Output new hypothesis
    
    # Placeholder for the first 'Discovery'
    hypothesis = {
        "date": str(datetime.date.today()),
        "hypothesis": "Triple Threat: Combine Cup & Handle with Bullish Divergence and 30-day L60 breakout.",
        "combination": "C&H + RSI Div + L60 > 5",
        "win_pct": "TBD",
        "alpha": "TBD",
        "status": "INCUBATION"
    }
    
    try:
        wb = openpyxl.load_workbook(TRACKER_FILE)
        ws = wb["Strategy_R_D"]
        ws.append([hypothesis['date'], hypothesis['hypothesis'], hypothesis['combination'], hypothesis['win_pct'], hypothesis['alpha'], hypothesis['status']])
        wb.save(TRACKER_FILE)
        print(f"💡 New Strategy Hypothesis Logged: {hypothesis['hypothesis']}")
    except Exception as e:
        print(f"R&D logging failed: {e}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--learn", action="store_true", help="Run strategy R&D session")
    parser.add_argument("--log-cost", type=int, help="Log tokens used today")
    args = parser.parse_args()

    if args.learn:
        run_learning_session()
    elif args.log_cost:
        log_daily_costs(args.log_cost)
