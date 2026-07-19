"""
Project AETHER: Automated Feedback Retrospective Analyzer.

Scans 'Data/trade_history_dna.json', separates winners and losers,
runs statistical pattern audits to isolate toxic clusters (bad-habit trades),
writes them to 'Data/failure_dna_rules.json' as actionable rules for the autopilot,
and outputs a rich, human-readable 'Data/retrospective_report.txt'.
"""

import json
from pathlib import Path
import datetime
from aether_logger import get_logger as _get_logger

_log = _get_logger("retrospective")

BASE_DIR = Path(__file__).resolve().parent
DNA_FILE = BASE_DIR / "Data" / "trade_history_dna.json"
RULES_FILE = BASE_DIR / "Data" / "failure_dna_rules.json"
REPORT_FILE = BASE_DIR / "Data" / "retrospective_report.txt"

def analyze():
    _log.info("Launching automated Feedback & Pattern Analyzer...")
    
    if not DNA_FILE.exists() or DNA_FILE.stat().st_size == 0:
        _log.warning("No raw trade DNA history found. Please run bootstrap_dna.py first.")
        return
        
    with open(DNA_FILE, "r", encoding="utf-8") as f:
        trades = json.load(f)
        
    total_trades = len(trades)
    _log.info(f"Loaded {total_trades} completed historical trades from the ledger.")
    
    if total_trades == 0:
        _log.warning("Empty trade ledger. Skipping analysis.")
        return
        
    winners = [t for m in [trades] for t in m if t.get("pnl_pct", 0.0) >= 0.0]
    losers = [t for m in [trades] for t in m if t.get("pnl_pct", 0.0) < 0.0]
    
    win_count = len(winners)
    loss_count = len(losers)
    win_rate = round((win_count / total_trades) * 100, 2) if total_trades else 0.0
    
    _log.info(f"Winners: {win_count} | Losers: {loss_count} | Win Rate: {win_rate}%")
    
    # --- 🔬 Statistical Toxic Pattern Clustering ---
    toxic_rules = []
    toxic_patterns_found = []
    
    # 1. Bearish PGR Rating Check
    # Buying stocks with Bearish ratings (e.g., Bearish, Very Bearish) is a classic toxic habit.
    bearish_losses = [t for t in losers if str(t.get("buy_dna", {}).get("pgr", "")).startswith("Be")]
    bearish_winners = [t for t in winners if str(t.get("buy_dna", {}).get("pgr", "")).startswith("Be")]
    total_bearish = len(bearish_losses) + len(bearish_winners)
    if total_bearish > 0:
        bearish_loss_rate = round((len(bearish_losses) / total_bearish) * 100, 2)
        if bearish_loss_rate >= 50.0 and len(bearish_losses) >= 1:
            toxic_rules.append({
                "id": "TOXIC_BEARISH_PGR",
                "field": "pgr",
                "condition": "startswith_Be",
                "reason": f"Avoid buying assets with Bearish Chaikin ratings. Pattern has a {bearish_loss_rate}% loss rate historically across {total_bearish} trades."
            })
            toxic_patterns_found.append(f"🔴 Buying Bearish PGR: {len(bearish_losses)} losses, {len(bearish_winners)} wins ({bearish_loss_rate}% loss rate)")
            
    # 2. Low Combined Score Check
    # Buying stocks with extremely weak combined momentum scores (e.g., < 5.0)
    low_score_losses = [t for t in losers if float(t.get("buy_dna", {}).get("score", 0.0)) < 5.0]
    low_score_winners = [t for t in winners if float(t.get("buy_dna", {}).get("score", 0.0)) < 5.0]
    total_low_score = len(low_score_losses) + len(low_score_winners)
    if total_low_score > 0:
        low_score_loss_rate = round((len(low_score_losses) / total_low_score) * 100, 2)
        if low_score_loss_rate >= 50.0 and len(low_score_losses) >= 1:
            toxic_rules.append({
                "id": "TOXIC_LOW_SCORE",
                "field": "score",
                "condition": "less_than_5.0",
                "reason": f"Avoid buying assets with weak Combined Scores (< 5.0). Pattern has a {low_score_loss_rate}% loss rate historically across {total_low_score} trades."
            })
            toxic_patterns_found.append(f"🔴 Buying Low Combined Score (< 5.0): {len(low_score_losses)} losses, {len(low_score_winners)} wins ({low_score_loss_rate}% loss rate)")

    # 3. High Bubble Z-Score Check (Bubbles chasing)
    # Buying overextended bubble-chasing stocks with Z-Score > 2.5
    high_z_losses = [t for t in losers if float(t.get("buy_dna", {}).get("z_score", 0.0)) > 2.5]
    high_z_winners = [t for t in winners if float(t.get("buy_dna", {}).get("z_score", 0.0)) > 2.5]
    total_high_z = len(high_z_losses) + len(high_z_winners)
    if total_high_z > 0:
        high_z_loss_rate = round((len(high_z_losses) / total_high_z) * 100, 2)
        if high_z_loss_rate >= 50.0 and len(high_z_losses) >= 1:
            toxic_rules.append({
                "id": "TOXIC_HIGH_Z_SCORE",
                "field": "z_score",
                "condition": "greater_than_2.5",
                "reason": f"Avoid buying overextended bubble assets (Z-Score > 2.5). Pattern has a {high_z_loss_rate}% loss rate historically."
            })
            toxic_patterns_found.append(f"🔴 Chasing High Z-Score (> 2.5): {len(high_z_losses)} losses, {len(high_z_winners)} wins ({high_z_loss_rate}% loss rate)")

    # Save the dynamic toxic rules to file
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(toxic_rules, f, indent=4)
    _log.info(f"Dynamic Toxic Rules updated: {RULES_FILE} ({len(toxic_rules)} rules active)")
    
    # --- 📝 Generating the Human-Readable Retrospective Report ---
    today_str = datetime.date.today().strftime("%A, %B %d, %Y")
    
    report = []
    report.append("======================================================================")
    report.append(f"🛡️ PROJECT AETHER: WEEKLY RETROSPECTIVE & FEEDBACK REPORT")
    report.append(f"Date compiled: {today_str}")
    report.append("======================================================================")
    report.append("")
    report.append(f"📊 OVERALL STANDING:")
    report.append(f"  - Total Paired Completed Trades: {total_trades}")
    report.append(f"  - Winning Trades: {win_count}")
    report.append(f"  - Losing Trades:  {loss_count}")
    report.append(f"  - Realised Win-Rate:   {win_rate}%")
    report.append("")
    report.append("======================================================================")
    report.append("🔬 ACTIVE TOXIC PATTERNS AUDIT (Bad Habits):")
    if toxic_patterns_found:
        for p in toxic_patterns_found:
            report.append(f"  {p}")
    else:
        report.append("  ✅ Excellent! No statistically recurring toxic patterns detected so far.")
    report.append("")
    report.append("======================================================================")
    report.append("🌟 WINNING HABITS AUDIT (Primal Successes):")
    
    # Standard Bullish/High Score check
    bullish_wins = [t for t in winners if str(t.get("buy_dna", {}).get("pgr", "")).startswith("Bu")]
    bullish_losses = [t for t in losers if str(t.get("buy_dna", {}).get("pgr", "")).startswith("Bu")]
    total_bullish = len(bullish_wins) + len(bullish_losses)
    if total_bullish > 0:
        bullish_win_rate = round((len(bullish_wins) / total_bullish) * 100, 2)
        report.append(f"  🟢 Buying Bullish PGR: {len(bullish_wins)} wins, {len(bullish_losses)} losses ({bullish_win_rate}% win rate)")
        
    high_score_wins = [t for t in winners if float(t.get("buy_dna", {}).get("score", 0.0)) >= 9.5]
    high_score_losses = [t for t in losers if float(t.get("buy_dna", {}).get("score", 0.0)) >= 9.5]
    total_high_score = len(high_score_wins) + len(high_score_losses)
    if total_high_score > 0:
        high_score_win_rate = round((len(high_score_wins) / total_high_score) * 100, 2)
        report.append(f"  🟢 Buying Strong Combined Score (>= 9.5): {len(high_score_wins)} wins, {len(high_score_losses)} losses ({high_score_win_rate}% win rate)")

    report.append("")
    report.append("======================================================================")
    report.append("📋 HISTORICAL COMPLETED TRADE LOG (Chronological):")
    for i, t in enumerate(reversed(trades), 1):
        report.append(f"  {i}. {t['symbol']}: Bought {t['buy_date']} @ ${t['buy_price']} | Sold {t['sell_date']} @ ${t['sell_price']} | PnL: {t['pnl_pct']:+.2f}% ({t['holding_days']}d held)")
        report.append(f"     DNA: PGR={t['buy_dna'].get('pgr')} | Score={t['buy_dna'].get('score')} | Setup={t['buy_dna'].get('setup')} | Sector={t['buy_dna'].get('industry')}")
        report.append("")
        
    # Write report file
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
        
    _log.info(f"Human-readable Retrospective Report generated: {REPORT_FILE}")
    _log.info("Feedback analysis completed successfully!")

if __name__ == "__main__":
    analyze()
