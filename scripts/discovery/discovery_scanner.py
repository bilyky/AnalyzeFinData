import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import subprocess
import openpyxl
import json
import datetime
from pathlib import Path

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
XLSX_FILE = BASE_DIR / "state_of_the_day.xlsx" # Root file for symbols
STRATEGIC_THEMES = [
    "strategic metals 2026 top movers",
    "AI thermal management liquid cooling stocks",
    "advanced robotics sensors motion control companies",
    "next gen semiconductor substrate materials",
    "electrical grid infrastructure transformers copper stocks"
]

def get_existing_symbols():
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, data_only=True, read_only=True)
        ws = wb["Research"]
        return {str(row[3]).strip().upper() for row in ws.iter_rows(min_row=2, values_only=True) if row[3]}
    except:
        return set()

def scan_for_missing_symbols():
    existing = get_existing_symbols()
    print(f"Current universe: {len(existing)} symbols.")
    
    # This function would ideally use a search tool or API.
    # For now, I will perform a mock scan based on the themes to find candidates
    # that are typically missing from broad lists.
    
    potential_candidates = [
        {"symbol": "VRT", "theme": "AI Cooling", "reason": "Leader in data center thermal management."},
        {"symbol": "MOD", "theme": "AI Cooling", "reason": "Heat transfer specialist for high-performance computing."},
        {"symbol": "ETN", "theme": "Power Grid", "reason": "Critical electrical infrastructure and power management."},
        {"symbol": "TXG", "theme": "AI Healthcare", "reason": "Single-cell sequencing data fuel for medical AI."},
        {"symbol": "RKLB", "theme": "Defense/Aerospace", "reason": "End-to-end space launch and systems leader."},
        {"symbol": "TER", "theme": "Robotics", "reason": "Automation sensors and testing equipment."},
    ]
    
    missing = []
    for cand in potential_candidates:
        if cand["symbol"] not in existing:
            missing.append(cand)
            
    return missing

def report_discovery():
    missing = scan_for_missing_symbols()
    if missing:
        print("\n--- 🔍 AETHER DISCOVERY: MISSING OPPORTUNITIES ---")
        for m in missing:
            print(f"Symbol: {m['symbol']} | Theme: {m['theme']}")
            print(f"Reason: {m['reason']}")
            print(f"Prompt: 'Add {m['symbol']} to Research?'\n")
        
        # In a real run, this text is pushed to the user's daily email.
        return missing
    else:
        print("No new symbols discovered today.")
        return []

if __name__ == "__main__":
    report_discovery()
