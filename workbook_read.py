"""
Shared read-only accessors for the daily state workbook (state_of_the_day.xlsx)
and the AI-game JSON.

These are pure readers: they open the workbook (read_only, data_only), pull rows,
and return plain dicts/lists. They have no side effects, send no email, and run no
subprocesses. Extracted here so both the top-level orchestrator (autonomous_pipeline)
and the web data layer (data_api) can share ONE definition instead of the web layer
reaching up into the pipeline module (which drags in email/subprocess machinery on
import). Neither of those modules is a dependency of this one.
"""

import json
import traceback
from pathlib import Path

import openpyxl

import risk_utils
from aether import pick_lists
from aether_logger import get_logger as _get_logger

_log = _get_logger("workbook_read")

BASE_DIR = Path(__file__).resolve().parent
XLSX_FILE = BASE_DIR / "Data" / "state_of_the_day.xlsx"
GAME_FILE = BASE_DIR / "Data" / "ai_portfolio_game.json"
ACCOUNT_RISK_USD = 500  # Dollars to lose if a stop is hit (position-sizing basis).


def research_rows(ws) -> list:
    """The Research sheet as aether.pick_lists rows (first row per symbol)."""
    rows, seen = [], set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        sym = str(row[3] or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        rows.append({
            "symbol": sym, "industry": str(row[4] or ""), "pgr": str(row[6] or ""),
            "stop": row[9] or 0.0, "price": row[10] or 0.0, "target": row[11] or 0.0,
            "setup": str(row[20] or ""), "br": row[21] or 0.0, "win_pct": row[23] or 0.0,
            "s10": row[24] or 0.0, "l60": row[25] or 0.0,
            "patterns": str(row[26] or "").strip() if len(row) > 26 else "",
        })
    return rows


def _pick_dict(r) -> dict:
    """The fields the morning report shows for one pick."""
    price, stop = r["price"], r["stop"]
    atr = risk_utils.calculate_atr(r["symbol"])
    return {
        "Symbol": r["symbol"], "PGR": r["pgr"], "Price": price, "Stop": stop, "Target": r["target"],
        "S10": r["s10"], "L60": r["l60"], "WinPct": r["win_pct"], "Total": pick_lists.total(r),
        "Risk_Reward": round(pick_lists.rr_ratio(r), 2), "Elite": pick_lists.is_elite(r),
        "ATR": atr, "Shares_ATR": risk_utils.get_atr_position_size(price, atr, ACCOUNT_RISK_USD),
        "Shares_Stop": risk_utils.get_position_size(price, stop, ACCOUNT_RISK_USD),
        "Patterns": r["patterns"], "Industry": r["industry"],
    }


def _morning_list(plist, skip=0) -> list:
    wb = None
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        return [_pick_dict(r) for r in plist.pick(research_rows(wb["Research"]), n=5, skip=skip)]
    except Exception as e:
        _log.error("Error computing picks", extra={"list": plist.key, "error": str(e)})
        traceback.print_exc()
        return []
    finally:
        if wb:
            wb.close()


def get_top_5_picks():
    """The 05:30 shortlist: criteria in aether.pick_lists.MORNING_TOP5 (shown in the email).
    The R:R floor used to have no elite-breakout waiver, so leaders at new highs (reward/risk
    near 0 to their own resistance) never qualified and the list kept laggards (PROD 11-02:
    one pick, HESM, S10 -2.7)."""
    return _morning_list(pick_lists.MORNING_TOP5)


def get_replacement_pairs():
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        if "Replacements" not in wb.sheetnames:
            return []

        ws = wb["Replacements"]
        pairs = []
        for row in ws.iter_rows(min_row=3, max_row=13, values_only=True):
            if row[1] and row[7]:
                pairs.append({
                    "Sell": row[1],
                    "Sell_Score": row[4],
                    "Sell_Status": row[5],
                    "Buy": row[7],
                    "Buy_Score": row[10],
                    "Buy_PGR": row[11]
                })
        return pairs
    except Exception as e:
        _log.error("Error reading replacements", extra={"error": str(e)})
        return []


def get_reserves_data():
    """The next five on the morning ranking (ranks 6-10; aether.pick_lists.MORNING_RESERVES).
    This was a hard-coded EIX, AMAT, URI, VLO, RS fallback: nothing ever wrote the game state's
    "reserves" key, so the same five names were audited every day since 2026-06-26."""
    return _morning_list(pick_lists.MORNING_RESERVES, skip=5)


def get_market_regime():
    """Detect current market regime based on SPY momentum. Returns (label, color)."""
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        ws = wb["Research"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3] == "SPY":
                l60 = row[25] or 0  # 60-day momentum drives the regime label
                if l60 > 2: return "🚀 BULLISH (Risk-On)", "#2ecc71"
                if l60 < -2: return "⚠️ BEARISH (Risk-Off / Defensive)", "#e74c3c"
                return "⚖️ NEUTRAL (Consolidation)", "#f39c12"
    except Exception as e:
        _log.warning("Could not determine market regime", extra={"error": str(e)})
    return "Unknown", "#7f8c8d"
