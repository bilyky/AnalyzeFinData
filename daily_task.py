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
        wb = openpyxl.load_workbook('Data/investment.xlsx', data_only=True, read_only=True)
        ws = wb['Research']
        symbols = []
        for row in ws.iter_rows(min_row=2):
            val = row[3].value
            if val: symbols.append(str(val))
        return symbols
    except Exception as e:
        print(f"Error reading symbols from XLS: {e}")
        return []

def get_top_10(date):
    # Dynamic import to use existing logic
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import powergauge
    import json
    
    powergauge._build_cache_index()
    
    # Get symbols from Data/Symbol directory for the date
    # Alternatively, get symbols from XLS to ensure we only analyze "Research" symbols
    research_symbols = get_symbols_from_xls()
    if not research_symbols:
        print("Warning: No symbols found in Research sheet. Falling back to all cached symbols.")
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
            
    # Filter: Bullish & Setup OK
    filtered = [r for r in results if r['pgr_val'] >= 4 and r['setup'] == True]
    if not filtered: filtered = [r for r in results if r['pgr_val'] >= 4]
    
    # Rank by combined S10 + BR
    top_10 = sorted(filtered, key=lambda x: x['s10'] + x['br'], reverse=True)[:10]
    return top_10

def get_description(r):
    desc = []
    if r['s10'] >= 5:
        desc.append(f"Strong entry signal (Short10: {r['s10']})")
    elif r['s10'] > 0:
        desc.append(f"Positive entry signal (Short10: {r['s10']})")
        
    if r['br'] >= 2.5:
        desc.append(f"Massive buying pressure (BR: {r['br']})")
    elif r['br'] >= 1.5:
        desc.append(f"Solid accumulation (BR: {r['br']})")
        
    if r['l60'] >= 3:
        desc.append(f"Strong 2-month trend (Long60: {r['l60']})")
    elif r['l60'] > 0:
        desc.append(f"Healthy trend (Long60: {r['l60']})")
        
    if r['pgr'] == 'Bu+':
        desc.append("Maximum Power Gauge Rating (Very Bullish)")
        
    return "; ".join(desc) if desc else "Solid technical setup"

def main():
    # Ensure we are in the script's directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    today = datetime.date.today()
    print(f"Starting daily automation for {today}")

    # 1. Sync last 5 days history
    run_command([sys.executable, "run_history.py", "5"])

    # 2. Run main script to populate Data and Excel
    run_command([sys.executable, "main.py"])

    # 3. Get predictions for tomorrow (Top 10)
    top_10 = get_top_10(today)

    # 4. Format and send HTML email
    html = f"""
    <html>
    <head>
        <style>
            table {{ border-collapse: collapse; width: 100%; font-family: sans-serif; }}
            th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid #ddd; }}
            th {{ background-color: #f2f2f2; color: #333; }}
            tr:hover {{ background-color: #f5f5f5; }}
            .symbol {{ font-weight: bold; color: #2c3e50; }}
            .score {{ font-weight: bold; color: #27ae60; }}
            .pgr {{ font-weight: bold; }}
            .bu-plus {{ color: #27ae60; }}
            .bu {{ color: #2ecc71; }}
        </style>
    </head>
    <body>
        <h2>Daily Trade Report: {today}</h2>
        <p>Top 10 Recommended Stocks for Tomorrow's Session:</p>
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>PGR</th>
                    <th>S10</th>
                    <th>BR</th>
                    <th>L60</th>
                    <th>Total Score</th>
                </tr>
            </thead>
            <tbody>
    """
    
    for r in top_10:
        pgr_class = "bu-plus" if r['pgr'] == 'Bu+' else "bu"
        score = r['s10'] + r['br']
        html += f"""
                <tr>
                    <td class="symbol">{r['symbol']}</td>
                    <td class="pgr {pgr_class}">{r['pgr']}</td>
                    <td>{r['s10']:.1f}</td>
                    <td>{r['br']:.1f}</td>
                    <td>{r['l60']:.1f}</td>
                    <td class="score">{score:.1f}</td>
                </tr>
        """
        
    html += """
            </tbody>
        </table>
        
        <h3>Technical Rationale</h3>
        <ul>
    """
    
    for r in top_10: # Detailed descriptions for all 10
        html += f"<li><b>{r['symbol']}</b>: {get_description(r)}</li>"
        
    html += """
        </ul>
        <p><small>S10: 10-day entry score | BR: Buying Ratio | L60: 60-day position score</small></p>
    </body>
    </html>
    """

    print("Generating HTML report...")
    notify.send_email(f"Daily Trade Report: {today}", html, is_html=True)

if __name__ == "__main__":
    main()
