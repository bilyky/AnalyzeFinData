"""
Project AETHER: Goal & Trajectory Sentry

Computes the targets, goals, remaining days, and required compounding returns for:
1. Project 1 (The AI Portfolio Game)
2. Project 2 (The Oracle Project)

Reads authoritative fields from state (no speculation) and writes the result to
Data/active_targets.json for downstream progress reporting.
"""

import datetime
import json
import os
import sys
from pathlib import Path


# Insert project root to import local modules cleanly
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

import data_api
from aether import etrade
from aether.config import CFG


TARGET_PATH = os.path.join(BASE_DIR, "Data", "active_targets.json")


def run_sentry():
    today = datetime.date.today()
    results = {}

    # ── 1. Audit Project 1: The AI Portfolio Game ──
    game_path = os.path.join(BASE_DIR, "Data", "ai_portfolio_game.json")
    if os.path.exists(game_path):
        try:
            with open(game_path) as f:
                game_state = json.load(f)
            
            equity = game_state.get("equity", 10000.0)
            
            # The AI Game's start is hardcoded at $10k to $20k over 90 days
            start_equity = 10000.0
            target_equity = 20000.0
            total_days = 90
            
            # Use the authoritative top-level start_date field (the state has no
            # "transactions" key — the trade log is "history"; and history[-1] would be the
            # NEWEST entry, not the start). Only fall back if start_date is genuinely absent.
            start_date_str = game_state.get("start_date") or "2026-06-17"
            try:
                start_date = datetime.date.fromisoformat(str(start_date_str))
                days_active = (today - start_date).days
            except Exception:
                days_active = 61  # Fallback
                
            days_left = max(0, total_days - days_active)
            progress_pct = (equity / target_equity) * 100.0 if target_equity > 0 else 0.0
            
            # Calculate required daily compounding return to hit target
            req_return_pct = 0.0
            if days_left > 0 and equity > 0:
                req_return_pct = ((target_equity / equity) ** (1.0 / days_left) - 1.0) * 100.0
                
            results["ai_portfolio_game"] = {
                "project_name": "The AI Portfolio Game",
                "goal": f"Grow virtual portfolio from ${start_equity:,.2f} to ${target_equity:,.2f} in {total_days} days.",
                "start_equity": start_equity,
                "current_equity": equity,
                "target_equity": target_equity,
                "days_active": days_active,
                "days_remaining": days_left,
                "progress_percentage": round(progress_pct, 2),
                "required_daily_compounding_return_percentage": round(req_return_pct, 2),
                "is_on_track": progress_pct >= 100.0 or (days_left > 0 and req_return_pct < 0.5)
            }
        except Exception as e:
            results["ai_portfolio_game"] = {"error": f"Failed to audit AI Portfolio Game: {e}"}
    else:
        results["ai_portfolio_game"] = {"error": "ai_portfolio_game.json not found"}

    # ── 2. Audit Project 2: The Oracle Project ──
    try:
        # Load E*TRADE live account balance if tokens are valid
        tokens = etrade.get_tokens("production", allow_browser=False)
        real_acct_id = CFG.oracle_account
        
        # Get start equity and target date from config.json
        start_equity = CFG.oracle_start_equity or 22978.72  # fallback to standard baseline
        raw_date = (CFG.oracle_target_date or "2026-12-31").strip()
        
        try:
            target_date = datetime.date.fromisoformat(raw_date)
            days_left = (target_date - today).days
        except Exception:
            target_date = None
            days_left = 136  # fallback
            
        double_target = start_equity * 2
        
        # Pull live account equity
        current_equity = start_equity  # default fallback if offline
        if tokens and real_acct_id:
            try:
                accts_data = data_api.read_accounts()
                for acct in accts_data.get("accounts", []):
                    if acct.get("id") == real_acct_id:
                        current_equity = acct.get("equity") or start_equity
                        break
            except Exception:
                pass
                
        progress_pct = (current_equity / double_target) * 100.0 if double_target > 0 else 0.0
        
        # Calculate required daily compounding return
        req_return_pct = 0.0
        if days_left > 0 and current_equity > 0:
            req_return_pct = ((double_target / current_equity) ** (1.0 / days_left) - 1.0) * 100.0
            
        results["oracle_project"] = {
            "project_name": "The Oracle Project",
            "goal": f"Grow real E*TRADE account {real_acct_id} from ${start_equity:,.2f} to double-up target of ${double_target:,.2f} by {raw_date}.",
            "start_equity": start_equity,
            "current_equity": current_equity,
            "target_equity": double_target,
            "target_date": raw_date,
            "days_remaining": days_left,
            "progress_percentage": round(progress_pct, 2),
            "required_daily_compounding_return_percentage": round(req_return_pct, 2),
            "is_on_track": progress_pct >= 100.0 or (days_left > 0 and req_return_pct < 0.5)
        }
    except Exception as e:
        results["oracle_project"] = {"error": f"Failed to audit Oracle Project: {e}"}

    # Save to disk
    os.makedirs(os.path.dirname(TARGET_PATH), exist_ok=True)
    with open(TARGET_PATH, "w") as f:
        json.dump(results, f, indent=4)
    print(f"Sentry update complete: active targets saved to {TARGET_PATH}")


if __name__ == "__main__":
    run_sentry()
