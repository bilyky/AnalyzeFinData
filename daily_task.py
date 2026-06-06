import os
import sys
import datetime
import subprocess
import notify
import openpyxl

# --- CONFIGURATION ---
# Ensure these are set in your environment or update them here
# os.environ['CHAIKIN_EMAIL'] = '...'
# os.environ['CHAIKIN_PASSWORD'] = '...'
# os.environ['SMTP_PASSWORD'] = '...'

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_task.log")

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")
    print(msg)

def run_command(command_list):
    log(f"Running: {' '.join(command_list)}")
    result = subprocess.run(command_list, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"Error (exit code {result.returncode}): {result.stderr}")
    else:
        log("Command completed successfully.")
    return result.stdout

def get_symbols_from_xls():
    try:
        wb = openpyxl.load_workbook('Data/state_of_the_day.xlsx', data_only=True, read_only=True)
        ws = wb['Research']
        symbols = []
        for row in ws.iter_rows(min_row=2):
            val = row[3].value
            if val: symbols.append(str(val))
        return symbols
    except Exception as e:
        print(f"Error reading symbols from XLS: {e}")
        return []

def get_all_data(date):
    # Dynamic import to use existing logic
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import powergauge
    import json
    
    powergauge._build_cache_index()
    
    # Get symbols from XLS to ensure we only analyze "Research" symbols
    research_symbols = get_symbols_from_xls()
    if not research_symbols:
        log("Warning: No symbols found in Research sheet. Falling back to all cached symbols.")
        symbol_dir = os.path.join("Data", "Symbol")
        cached_symbols = []
        for root, dirs, files in os.walk(symbol_dir):
            for f in files:
                if f.endswith(f"_{date}.json"):
                    cached_symbols.append(f.rsplit('_', 1)[0])
        research_symbols = list(set(cached_symbols))
    
    results = []
    for symbol in research_symbols:
        try:
            pg = powergauge.get_symbol_data(symbol, date, True, "dummy")
            if pg.price == -1: continue
            
            ohlcv_path = os.path.join("Data", "Symbol_full", f"{symbol}_daily.json")
            ohlcv_ts = None
            if os.path.exists(ohlcv_path):
                with open(ohlcv_path) as _f:
                    ohlcv_ts = json.load(_f).get('Time Series (Daily)')
            
            f = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
            pgr_val = pg.pgr_corrected_value if pg.pgr_corrected_value != 0 else pg.pgr_value

            results.append({
                'symbol': symbol,
                's10': f['short_score'],
                'l60': f['long_score'],
                'br': f['buying_ratio'],
                'pgr': f['pgr'],
                'pgr_val': pgr_val,
                'setup': f['setup_ok']
            })
        except Exception:
            continue
            
    return results

def get_description(r):
    desc = []
    if r['s10'] >= 5: desc.append(f"Strong entry (S10: {r['s10']})")
    elif r['s10'] > 0: desc.append(f"Positive entry (S10: {r['s10']})")
    if r['br'] >= 2.5: desc.append(f"Massive pressure (BR: {r['br']})")
    elif r['br'] >= 1.5: desc.append(f"Accumulation (BR: {r['br']})")
    if r['l60'] >= 3: desc.append(f"Strong trend (L60: {r['l60']})")
    elif r['l60'] > 0: desc.append(f"Healthy trend (L60: {r['l60']})")
    if r['pgr'] == 'Bu+': desc.append("Very Bullish")
    return "; ".join(desc) if desc else "Solid technicals"

def main():
    # Ensure we are in the script's directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    today = datetime.date.today()
    log(f"Starting daily automation for {today}")

    # 1. Sync last 5 days history
    run_command([sys.executable, "run_history.py", "5"])

    # 2. Run main script to populate Data and Excel
    run_command([sys.executable, "main.py"])

    # 3. Get all processed data
    all_symbols_data = get_all_data(today)
    if not all_symbols_data:
        log("Error: No symbol data retrieved for report.")
        return

    # 4. Generate Tables (Same logic as Excel Picks sheet)
    p_map = {'Bu+': 5, 'Bu': 4, 'N': 3, 'Be': 2, 'Be-': 1}
    bullish = [r for r in all_symbols_data if p_map.get(r['pgr'], 0) >= 4 and r['setup'] == 1]
    if not bullish: bullish = [r for r in all_symbols_data if p_map.get(r['pgr'], 0) >= 4]
    
    bearish = [r for r in all_symbols_data if p_map.get(r['pgr'], 0) < 4]
    if not bearish: bearish = all_symbols_data

    top_combined = sorted(bullish, key=lambda x: x['s10'] + x['br'], reverse=True)[:5]
    top_s10      = sorted(bullish, key=lambda x: x['s10'], reverse=True)[:5]
    top_l60      = sorted(bullish, key=lambda x: x['l60'], reverse=True)[:5]
    
    # Aggressive (Unfiltered)
    agg_s10      = sorted(all_symbols_data, key=lambda x: x['s10'], reverse=True)[:5]
    agg_l60      = sorted(all_symbols_data, key=lambda x: x['l60'], reverse=True)[:5]
    
    top_sell     = sorted(all_symbols_data, key=lambda x: x['s10'], reverse=False)[:5]

    # 5. Format and send HTML email
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: sans-serif; color: #333; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
            th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #ddd; font-size: 12px; }}
            th {{ background-color: #2E4057; color: white; }}
            .buy {{ background-color: #E2EFDA; }}
            .agg {{ background-color: #EBF5FB; }}
            .sell {{ background-color: #FCE4D6; }}
            .symbol {{ font-weight: bold; }}
            .score {{ font-weight: bold; color: #27ae60; }}
            .pgr-high {{ color: #27ae60; font-weight: bold; }}
            .pgr-low {{ color: #c0392b; font-weight: bold; }}
            h2 {{ color: #2c3e50; border-bottom: 3px solid #2E4057; padding-bottom: 5px; }}
            h3 {{ color: #2c3e50; background: #eee; padding: 5px 10px; border-left: 5px solid #2E4057; }}
        </style>
    </head>
    <body>
        <h2>Daily Trade Report: {today}</h2>
        
        <h3>SECTION 1: CONSERVATIVE (Bullish + Setup OK)</h3>
        <p><i>Confirmed quality setups with fundamental and technical alignment.</i></p>

        <b>TOP 5 BUY -- Combined (S10 + BR)</b>
        <table>
            <thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>Total</th><th>Rationale</th></tr></thead>
            <tbody>
    """
    
    def add_rows(rows, row_class, use_combined=False):
        res = ""
        for i, r in enumerate(rows, 1):
            pgr_val = p_map.get(r['pgr'], 0)
            pgr_style = "pgr-high" if pgr_val >= 4 else ("pgr-low" if pgr_val <= 2 else "")
            score = round(r['s10'] + r['br'], 1)
            res += f"""
                <tr class="{row_class}">
                    <td>{i}</td>
                    <td class="symbol">{r['symbol']}</td>
                    <td class="{pgr_style}">{r['pgr']}</td>
                    <td>{r['s10']:.1f}</td>
                    <td>{r['br']:.1f}</td>
                    {f'<td class="score">{score}</td>' if use_combined else f'<td>{r["l60"]:.1f}</td>'}
                    <td><small>{get_description(r)}</small></td>
                </tr>
            """
        return res

    html += add_rows(top_combined, "buy", use_combined=True)
    html += "</tbody></table>"

    html += "<b>TOP 5 BUY -- Short10 (Safe 10)</b>"
    html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
    html += add_rows(top_s10, "buy")
    html += "</tbody></table>"

    html += "<b>TOP 5 BUY -- Long60 (Safe 60)</b>"
    html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
    html += add_rows(top_l60, "buy")
    html += "</tbody></table>"

    html += "<h3>SECTION 2: AGGRESSIVE (Raw Momentum - Unfiltered)</h3>"
    html += "<p><i>Highest technical scores regardless of PGR rating. Watch for reversals.</i></p>"
    
    html += "<b>TOP 5 MOMENTUM -- Raw Short10</b>"
    html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
    html += add_rows(agg_s10, "agg")
    html += "</tbody></table>"

    html += "<b>TOP 5 MOMENTUM -- Raw Long60</b>"
    html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
    html += add_rows(agg_l60, "agg")
    html += "</tbody></table>"

    html += "<h3>SECTION 3: WEAKNESS</h3>"
    html += "<table><thead><tr><th>Rank</th><th>Symbol</th><th>PGR</th><th>S10</th><th>BR</th><th>L60</th><th>Rationale</th></tr></thead><tbody>"
    html += add_rows(top_sell, "sell")
    html += "</tbody></table>"

    html += """
        <p><small>S10: 10-day entry score | BR: Buying Ratio | L60: 60-day position score</small></p>
    </body>
    </html>
    """

    log("Generating rich HTML report...")
    notify.send_email(f"Daily Trade Report: {today}", html, is_html=True)

if __name__ == "__main__":
    main()
