import os
import sys
import datetime
import subprocess
import openpyxl
import notify
import traceback
from pathlib import Path

import external_intel

# Custom modules
import risk_utils
import performance_tracker

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
SRC_XLSX  = BASE_DIR / "state_of_the_day.xlsx"
XLSX_FILE = BASE_DIR / "Data" / "state_of_the_day.xlsx"
LOG_FILE_PATH = BASE_DIR / "Data" / "autonomous_run.log"
ACCOUNT_RISK_USD = 500  # Amount to lose if stop is hit

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    with open(LOG_FILE_PATH, "a") as f:
        f.write(formatted_msg + "\n")

def verify_data_freshness():
    if not XLSX_FILE.exists():
        return False, "File does not exist."
    
    mtime = datetime.datetime.fromtimestamp(XLSX_FILE.stat().st_mtime)
    today = datetime.date.today()
    if mtime.date() < today:
        return False, f"Data is stale. Last updated: {mtime.strftime('%Y-%m-%d %H:%M')}"
    
    return True, f"Data is fresh ({mtime.strftime('%Y-%m-%d %H:%M')})"

def validate_sheets():
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        required_sheets = ["Research", "Picks", "Replacements"]
        for sheet in required_sheets:
            if sheet not in wb.sheetnames:
                return False, f"Missing sheet: {sheet}"
            
            ws = wb[sheet]
            if ws.max_row < 2:
                return False, f"Sheet {sheet} appears empty."
        
        return True, "All required sheets validated and rendered."
    except Exception as e:
        return False, f"Validation error: {e}"

def get_top_5_picks():
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        ws = wb["Research"]
        
        candidates = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            sym = row[3]
            if not sym: continue
            
            pgr = str(row[6] or "")
            price = row[10] or 0.0
            stop = row[9] or 0.0
            target = row[11] or 0.0
            setup = str(row[20] or "")
            
            is_setup_ok = (setup == "1" or setup == "OK" or setup == 1)
            
            win_pct = row[23] or 0.0
            s10 = row[24] or 0.0
            l60 = row[25] or 0.0
            pattern_text = str(row[26] or "").strip() if len(row) > 26 else ""
            industry = str(row[4] or "")

            if is_setup_ok:
                atr = risk_utils.calculate_atr(sym)
                shares_atr = risk_utils.get_atr_position_size(price, atr, ACCOUNT_RISK_USD)
                shares_stop = risk_utils.get_position_size(price, stop, ACCOUNT_RISK_USD)

                candidates.append({
                    "Symbol": sym,
                    "PGR": pgr,
                    "Price": price,
                    "Stop": stop,
                    "Target": target,
                    "S10": s10,
                    "L60": l60,
                    "WinPct": win_pct,
                    "Total": s10 + l60,
                    "ATR": atr,
                    "Shares_ATR": shares_atr,
                    "Shares_Stop": shares_stop,
                    "Patterns": pattern_text,
                    "Industry": industry
                })
        
        candidates.sort(key=lambda x: x["Total"], reverse=True)
        return candidates[:5]
    except Exception as e:
        log(f"Error computing picks: {e}")
        traceback.print_exc()
        return []

def check_earnings(symbol):
    return "Check Required"

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
        log(f"Error reading replacements: {e}")
        return []

def get_reasoning(symbol, pgr, s10, l60, industry):
    """Retrieve live LLM reasoning via the GitHub Models API (gpt-4o-mini) with local heuristics fallback."""
    try:
        # Call the live LLM reasoning engine we just implemented
        return external_intel.get_ai_reasoning(symbol, industry, pgr, s10, l60)
    except Exception as e:
        log(f"Warning: Live AI reasoning failed for {symbol}: {e}")
        # Standard Fallback
        return f"<b>Technical Setup Active:</b> Setup OK with total score: {s10+l60:.1f}.<br>🚨 <b>Devil's Advocate:</b> High market volatility could override technical momentum."

def get_market_regime():
    """Detect current market regime based on SPY momentum."""
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        ws = wb["Research"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[3] == "SPY":
                s10, l60 = row[24] or 0, row[25] or 0
                if l60 > 2: return "🚀 BULLISH (Risk-On)", "#2ecc71"
                if l60 < -2: return "⚠️ BEARISH (Risk-Off / Defensive)", "#e74c3c"
                return "⚖️ NEUTRAL (Consolidation)", "#f39c12"
    except:
        pass
    return "Unknown", "#7f8c8d"

def cleanup_orphaned_processes():
    """Ensure no Excel-locking processes are hung."""
    try:
        if sys.platform == "win32":
            subprocess.run(["powershell", "Get-Process | Where-Object { $_.Name -match 'excel|python' -and $_.CommandLine -match 'main.py|daily_task.py' } | Stop-Process -Force"], capture_output=True)
    except:
        pass

def format_html_report(status_msg, picks, replacements, intel_ideas):
    today = datetime.date.today()
    regime, color = get_market_regime()
    
    # 0. Format External Ideas
    intel_section = ""
    if intel_ideas:
        idea_list = ""
        for i in intel_ideas:
            color = "#27ae60" if i["sentiment"] == "BUY" else ("#c0392b" if i["sentiment"] == "SELL" else "#f39c12")
            sentiment_badge = f'<span style="background: {color}; color: white; padding: 2px 6px; border-radius: 3px; font-weight: bold; font-size: 11px;">{i["sentiment"]}</span>'
            
            idea_list += f"""
            <li style="margin-bottom: 15px; border-bottom: 1px dashed #eee; padding-bottom: 10px;">
                <b>Source:</b> {i['from']}<br>
                <b>Topic:</b> {i['subject']}<br>
                <b>Decision:</b> {sentiment_badge} <b>{i['symbol']}</b><br>
                <b>AI Semantic Summary:</b> <span style="color: #555; font-style: italic;">"{i['thesis']}"</span>
            </li>"""
        
        intel_section = f"""
        <div style="background: #fff8e1; border-left: 5px solid #ffc107; padding: 15px; margin-bottom: 30px; border-radius: 4px;">
            <h4 style="margin: 0; color: #ffa000; text-transform: uppercase; font-size: 11px; padding-bottom: 5px; border-bottom: 1px solid #ffe082;">💡 Semantic Newsletter Scraper (External Intelligence)</h4>
            <ul style="margin: 10px 0 0 0; font-size: 13px; padding-left: 20px; list-style-type: none;">{idea_list}</ul>
        </div>
        """
    
    picks_rows = ""
    for i, p in enumerate(picks, 1):
        win_display = f"{p['WinPct'] * 100:.1f}%"
        earnings = check_earnings(p['Symbol'])
        reasoning = get_reasoning(p['Symbol'], p['PGR'], p['S10'], p['L60'], p['Industry'])
        
        pattern_display = p.get('Patterns') or ''
        pattern_cell = (f'<span style="font-size:12px; color:#8e44ad; font-weight:bold;">{pattern_display}</span>'
                        if pattern_display else '<span style="color:#aaa; font-size:11px;">—</span>')
        picks_rows += f"""
        <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 10px;">{i}</td>
            <td style="padding: 10px;"><b>{p['Symbol']}</b><br><small>{p['Industry']}</small></td>
            <td style="padding: 10px;">{p['PGR']}</td>
            <td style="padding: 10px;">{p['S10']:.1f} / {p['L60']:.1f}</td>
            <td style="padding: 10px;"><b>{p['Total']:.1f}</b></td>
            <td style="padding: 10px; font-size: 11px;">{reasoning}</td>
            <td style="padding: 10px;">Stop: ${p['Stop']}<br>Target: ${p['Target']}</td>
            <td style="padding: 10px; background-color: #e8f4fd;">
                ATR: <b>{p['Shares_ATR']}</b> | Stop: <b>{p['Shares_Stop']}</b>
            </td>
            <td style="padding: 10px;">{pattern_cell}</td>
            <td style="padding: 10px; color: {'#e74c3c' if 'Required' in earnings else '#333'};">{earnings}</td>
        </tr>
        """

    replacement_rows = ""
    for pair in replacements:
        reasoning = f"Rotating from {pair['Sell']} (Weakest) to {pair['Buy']} (Strongest institutional accumulation)."
        replacement_rows += f"""
        <tr style="border-bottom: 1px solid #ddd; font-size: 12px;">
            <td style="padding: 8px; color: #c0392b;"><b>{pair['Sell']}</b> ({pair['Sell_Score']:.1f})<br><small>{pair['Sell_Status']}</small></td>
            <td style="padding: 8px; text-align: center;">➡️</td>
            <td style="padding: 8px; color: #27ae60;"><b>{pair['Buy']}</b> ({pair['Buy_Score']:.1f})<br><small>PGR: {pair['Buy_PGR']}</small></td>
            <td style="padding: 8px; font-size: 11px; color: #555;">{reasoning}</td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: 'Segoe UI', Tahoma, sans-serif; color: #333; line-height: 1.6; max-width: 1100px; margin: auto;">
        <div style="background: #2c3e50; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
            <h1 style="margin: 0; font-size: 24px;">Daily Trading Intelligence</h1>
            <p style="margin: 5px 0 0 0; opacity: 0.8;">Autonomous Market Analysis | {today}</p>
        </div>

        <div style="padding: 20px; border: 1px solid #eee; border-top: none;">
            {intel_section}
            <div style="display: flex; gap: 20px; margin-bottom: 30px;">
                <div style="flex: 1; background: #f8f9fa; padding: 15px; border-left: 5px solid {color}; border-radius: 4px;">
                    <h4 style="margin: 0; color: #7f8c8d; text-transform: uppercase; font-size: 11px;">Market Regime</h4>
                    <p style="margin: 5px 0 0 0; font-weight: bold; font-size: 18px; color: {color};">{regime}</p>
                </div>
                <div style="flex: 1; background: #f8f9fa; padding: 15px; border-left: 5px solid #3498db; border-radius: 4px;">
                    <h4 style="margin: 0; color: #7f8c8d; text-transform: uppercase; font-size: 11px;">System Health</h4>
                    <p style="margin: 5px 0 0 0; font-weight: bold;">{status_msg}</p>
                </div>
            </div>
            
            <h3 style="color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 5px;">Top High-Probability Setups</h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 13px; margin-bottom: 40px;">
                <thead>
                    <tr style="background-color: #34495e; color: white; text-align: left;">
                        <th style="padding: 12px;">Rank</th><th style="padding: 12px;">Symbol</th><th style="padding: 12px;">PGR</th>
                        <th style="padding: 12px;">S10/L60</th><th style="padding: 12px;">Total</th><th style="padding: 12px;">Reasoning & Ruthless Audit</th>
                        <th style="padding: 12px;">Levels</th><th style="padding: 12px;">Shares</th><th style="padding: 12px;">Patterns</th><th style="padding: 12px;">Earnings</th>
                    </tr>
                </thead>
                <tbody>{picks_rows if picks else '<tr><td colspan="10">No candidates found today.</td></tr>'}</tbody>
            </table>

            <h3 style="color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 5px;">Portfolio Rotation Strategy</h3>
            <table style="border-collapse: collapse; width: 80%; font-size: 13px;">
                <thead>
                    <tr style="background-color: #f2f2f2; text-align: left;">
                        <th style="padding: 10px;">SELL (Weakest)</th><th style="padding: 10px;"></th><th style="padding: 10px;">BUY (Strongest)</th><th style="padding: 10px;">Rotation Rationale</th>
                    </tr>
                </thead>
                <tbody>{replacement_rows if replacements else '<tr><td colspan="4">No replacement pairs identified.</td></tr>'}</tbody>
            </table>
            
            <div style="margin-top: 40px; border-top: 1px solid #eee; padding-top: 20px; font-size: 11px; color: #95a5a6; text-align: center;">
                <p><b>Risk Management:</b> ATR-based sizing (2*ATR volatility) vs. Stop-based gap. <b>AI Manager:</b> Live tracking at Data/ai_portfolio_performance.xlsx</p>
                <p>Generated by Project AETHER Professional Desk | 5:30 AM PST Execution</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

def main():
    cleanup_orphaned_processes()
    log("Starting Daily Trading Pipeline...")
    
    # 0. Gather External Intel (Emails & News)
    log("Gathering external intelligence...")
    intel_ideas = []
    try:
        intel_ideas = external_intel.fetch_idea_emails()
        log(f"Fetched {len(intel_ideas)} ideas from email.")
    except Exception as e:
        log(f"Warning: Could not fetch external intel: {e}")

    # 1. Sync history (Backfill cache for deltas)
    log("Backfilling 5-day history (run_history.py)...")
    try:
        script_path = str(BASE_DIR / "run_history.py")
        subprocess.run([sys.executable, script_path, "5"], check=True, capture_output=True, text=True)
        log("History backfilled.")
    except subprocess.CalledProcessError as e:
        log(f"Warning: run_history.py failed (will continue): {e.stderr}")

    # 2. Execute main.py
    log("Refreshing workbook (main.py)...")
    try:
        script_path = str(BASE_DIR / "main.py")
        subprocess.run([sys.executable, script_path], check=True, capture_output=True, text=True)
        log("Workbook regenerated.")
    except subprocess.CalledProcessError as e:
        error_msg = f"main.py failed: {e.stderr}"
        log(error_msg)
        notify.send_email("ALERT: Daily Pipeline Failed", f"Pipeline failed during main.py execution.\n\n{error_msg}")
        return

    # 3. Verify data freshness
    fresh, msg = verify_data_freshness()
    log(msg)
    if not fresh:
        notify.send_email("ALERT: Daily Pipeline Stale Data", msg)
        return

    # 4. Validate sheets
    valid, v_msg = validate_sheets()
    log(v_msg)
    if not valid:
        notify.send_email("ALERT: Daily Pipeline Validation Failed", v_msg)
        return

    # 5. Compute top-5 picks & replacements
    log("Computing top-5 picks and replacements...")
    picks = get_top_5_picks()
    replacements = get_replacement_pairs()

    # 6. Log for performance tracking
    if picks:
        log("Logging picks for performance tracking...")
        performance_tracker.log_picks(picks)

    # 7. Send report
    log("Drafting and sending HTML report...")
    html = format_html_report(msg, picks, replacements, intel_ideas)
    notify.send_email(f"Daily Trade Report: {datetime.date.today()}", html, is_html=True)
    
    # 8. Run AI Game Routine
    log("Executing AI Portfolio Manager routine...")
    try:
        game_script = str(BASE_DIR / "ai_portfolio_game.py")
        subprocess.run([sys.executable, game_script, "--run"], check=True, capture_output=True, text=True)
        log("AI Game Routine complete.")
    except Exception as e:
        log(f"AI Game failed: {e}")

    log("Pipeline completed successfully.")

if __name__ == "__main__":
    main()
