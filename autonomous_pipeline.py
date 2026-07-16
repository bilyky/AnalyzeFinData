import os
import sys
import datetime
import subprocess
import openpyxl
import notify
import traceback
import json
from pathlib import Path

# --- Windows UTF-8 Hardening ---
# Prevents UnicodeEncodeError when printing emojis (🤖, 🚨) in headless environments
class SafeStreamWrapper:
    def __init__(self, stream):
        self._stream = stream
    def write(self, s):
        try:
            return self._stream.write(s)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, 'encoding', 'cp1252') or 'cp1252'
            safe_s = s.encode(encoding, errors='replace').decode(encoding)
            return self._stream.write(safe_s)
    def __getattr__(self, name):
        return getattr(self._stream, name)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.stdout = SafeStreamWrapper(sys.stdout)
    sys.stderr = SafeStreamWrapper(sys.stderr)

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
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(formatted_msg + "\n")
    except Exception as e:
        print(f"Failed to write to log file: {e}")

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
        seen_symbols = set()
        for row in ws.iter_rows(min_row=2, values_only=True):
            sym = row[3]
            if not sym: continue
            
            # De-duplicate: Ensure each symbol is only processed once
            sym_clean = str(sym).strip().upper()
            if sym_clean in seen_symbols:
                continue
            seen_symbols.add(sym_clean)
            
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

def get_reserves_data():
    """Extract today's scores for our dynamic A-Reserves (Backup Players) list loaded from the central JSON."""
    reserves_syms = ['EIX', 'AMAT', 'URI', 'VLO', 'RS'] # Standard Fallback
    try:
        game_file = BASE_DIR / "Data" / "ai_portfolio_game.json"
        if game_file.exists():
            with open(game_file, "r", encoding="utf-8") as f:
                state = json.load(f)
                reserves_syms = state.get("reserves", reserves_syms)
    except Exception as e:
        log(f"Warning: Could not load dynamic reserves from JSON (using fallback): {e}")

    reserves_data = []
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, read_only=True, data_only=True)
        ws = wb["Research"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            sym = row[3]
            if sym in reserves_syms:
                reserves_data.append({
                    "Symbol": sym,
                    "Industry": row[4],
                    "PGR": row[6],
                    "S10": row[24] or 0,
                    "L60": row[25] or 0,
                    "Total": (row[24] or 0) + (row[25] or 0),
                    "Price": row[10] or 0
                })
        # Sort to preserve priority order
        reserves_data.sort(key=lambda x: reserves_syms.index(x["Symbol"]))
    except Exception as e:
        log(f"Error loading reserves data: {e}")
    return reserves_data

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
    
    # 0. Format External Ideas + Structural Intel
    # All email-sourced strings (from/subject) and AI-extracted text (event/impact/rd_topics)
    # are HTML-escaped before insertion to prevent injection into the email report.
    import html as _html
    def _e(v): return _html.escape(str(v or ""), quote=True)

    intel_section = ""
    if intel_ideas:
        idea_list = ""
        for i in intel_ideas:
            if i.get("symbol") and i.get("sentiment"):
                c = "#27ae60" if i["sentiment"] == "BUY" else ("#c0392b" if i["sentiment"] == "SELL" else "#f39c12")
                badge = f'<span style="background:{c};color:white;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">{_e(i["sentiment"])}</span>'
                idea_list += f"""
                <li style="margin-bottom:12px;border-bottom:1px dashed #eee;padding-bottom:10px;">
                    <b>Source:</b> {_e(i['from'])}<br>
                    <b>Topic:</b> {_e(i['subject'])}<br>
                    <b>Decision:</b> {badge} <b>{_e(i['symbol'])}</b>
                    {f"<br><b>Thesis:</b> <i>{_e(i['thesis'])}</i>" if i.get('thesis') else ''}
                </li>"""

        # Structural intel: aggregate catalysts, missing symbols, R&D across all emails
        all_catalysts, all_missing, all_rd = [], [], []
        for i in intel_ideas:
            iv = i.get("intel") or {}
            all_catalysts.extend(iv.get("dated_catalysts", []))
            all_missing.extend(iv.get("missing_symbols", []))
            all_rd.extend(iv.get("rd_topics", []))

        structural = ""
        if all_catalysts:
            rows = "".join(
                f"<tr><td style='padding:4px 8px;color:#555;'>{_e(c.get('date','?'))}</td>"
                f"<td style='padding:4px 8px;'>{_e(c.get('event',''))}</td>"
                f"<td style='padding:4px 8px;color:#777;font-style:italic;'>{_e(c.get('impact',''))}</td></tr>"
                for c in all_catalysts)
            structural += f"""
            <p style="font-weight:bold;margin:12px 0 4px;color:#555;font-size:11px;text-transform:uppercase;">
                Dated Catalysts</p>
            <table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:10px;">
                <thead><tr style="background:#f5f5f5;">
                    <th style="padding:4px 8px;text-align:left;">Date</th>
                    <th style="padding:4px 8px;text-align:left;">Event</th>
                    <th style="padding:4px 8px;text-align:left;">Why it matters</th>
                </tr></thead><tbody>{rows}</tbody></table>"""

        if all_missing:
            seen = set()
            badges = ""
            for m in all_missing:
                sym = m.get("symbol", "")
                if sym and sym not in seen:
                    seen.add(sym)
                    tip = _e(m.get("reason", ""))
                    badges += (f'<span title="{tip}" style="display:inline-block;margin:2px 4px;'
                               f'padding:2px 8px;background:#fff3e0;border:1px solid #ffb74d;'
                               f'border-radius:3px;font-size:12px;cursor:help;"><b>{_e(sym)}</b></span>')
            structural += f"""
            <p style="font-weight:bold;margin:12px 0 4px;color:#555;font-size:11px;text-transform:uppercase;">
                Not in Our Watchlist (hover for reason)</p>
            <div style="margin-bottom:10px;">{badges}</div>"""

        if all_rd:
            items = "".join(f"<li style='margin-bottom:4px;font-size:12px;color:#555;'>{_e(r)}</li>" for r in all_rd[:4])
            structural += f"""
            <p style="font-weight:bold;margin:12px 0 4px;color:#555;font-size:11px;text-transform:uppercase;">
                R&D Topics Implied</p>
            <ul style="margin:0;padding-left:18px;">{items}</ul>"""

        intel_section = f"""
        <div style="background:#fff8e1;border-left:5px solid #ffc107;padding:15px;margin-bottom:30px;border-radius:4px;">
            <h4 style="margin:0;color:#ffa000;text-transform:uppercase;font-size:11px;padding-bottom:5px;border-bottom:1px solid #ffe082;">
                External Intelligence ({len(intel_ideas)} emails scanned)</h4>
            {f'<ul style="margin:10px 0 0;font-size:13px;padding-left:20px;list-style:none;">{idea_list}</ul>' if idea_list else ''}
            {structural}
        </div>"""
    
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

    # 0.5. Format A-Reserves Audit
    reserves_data = get_reserves_data()
    reserves_rows = ""
    for r in reserves_data:
        reasoning = get_reasoning(r['Symbol'], r['PGR'], r['S10'], r['L60'], r['Industry'])
        color_total = '#27ae60' if r['Total'] >= 0 else '#c0392b'
        reserves_rows += f"""
        <tr style="border-bottom: 1px solid #ddd; font-size: 13px;">
            <td style="padding: 10px; font-weight: bold; color: #2c3e50;">{r['Symbol']}</td>
            <td style="padding: 10px;">{r['Industry']}</td>
            <td style="padding: 10px; font-weight: bold;">{r['PGR']}</td>
            <td style="padding: 10px;">{r['S10']:.1f} / {r['L60']:.1f}</td>
            <td style="padding: 10px; font-weight: bold; color: {color_total};">{r['Total']:.1f}</td>
            <td style="padding: 10px; font-size: 11px;">{reasoning}</td>
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
            <table style="border-collapse: collapse; width: 80%; font-size: 13px; margin-bottom: 40px;">
                <thead>
                    <tr style="background-color: #f2f2f2; text-align: left;">
                        <th style="padding: 10px;">SELL (Weakest)</th><th style="padding: 10px;"></th><th style="padding: 10px;">BUY (Strongest)</th><th style="padding: 10px;">Rotation Rationale</th>
                    </tr>
                </thead>
                <tbody>{replacement_rows if replacements else '<tr><td colspan="4">No replacement pairs identified.</td></tr>'}</tbody>
            </table>

            <h3 style="color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 5px; margin-top: 45px;">🛡️ A-Reserves Sentinel & AI Risk Audit</h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
                <thead>
                    <tr style="background-color: #2c3e50; color: white; text-align: left;">
                        <th style="padding: 10px;">Symbol</th><th style="padding: 10px;">Industry / Theme</th><th style="padding: 10px;">PGR</th>
                        <th style="padding: 10px;">S10/L60</th><th style="padding: 10px;">Total Score</th><th style="padding: 10px;">AI Ruthless Audit & Strategic Catalyst</th>
                    </tr>
                </thead>
                <tbody>{reserves_rows}</tbody>
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
        subprocess.run([sys.executable, script_path, "5"], check=True, capture_output=True, encoding="utf-8", errors="replace")
        log("History backfilled.")
    except subprocess.CalledProcessError as e:
        log(f"Warning: run_history.py failed (will continue): {e.stderr}")

    # 2. Execute main.py (writes today's closes into OHLCV JSON via _append_ohlcv_entry)
    log("Refreshing workbook (main.py)...")
    try:
        script_path = str(BASE_DIR / "main.py")
        subprocess.run([sys.executable, script_path], check=True, capture_output=True, encoding="utf-8", errors="replace")
        log("Workbook regenerated.")
    except subprocess.CalledProcessError as e:
        error_msg = f"main.py failed: {e.stderr}"
        log(error_msg)
        notify.send_email("ALERT: Daily Pipeline Failed", f"Pipeline failed during main.py execution.\n\n{error_msg}")
        return

    # 2b. OHLCV recovery pass — repair missing/corrupted/stale Symbol_full files via RapidAPI.
    #     Today's closes are already written by main.py (Chaikin). This only touches symbols
    #     with gaps > 30 days. Non-fatal: pipeline continues even if RapidAPI is unavailable.
    log("OHLCV recovery pass (rapidapi.py)...")
    try:
        import rapidapi
        from run_history import load_symbols
        _ohlcv_syms = load_symbols()
        _today_str = str(datetime.date.today())
        _ohlcv_result = rapidapi.repair_missing(_ohlcv_syms, _today_str)
        log(f"OHLCV: {_ohlcv_result['updated']} recovered, "
            f"{_ohlcv_result['skipped']} already current, "
            f"{len(_ohlcv_result['errors'])} errors")
    except Exception as e:
        log(f"Warning: OHLCV recovery failed (non-fatal, pipeline continues): {e}")

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

    log("Pipeline completed successfully.")


if __name__ == "__main__":
    main()
