import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl

xlsx_file = 'Data/state_of_the_day.xlsx'
wb = openpyxl.load_workbook(xlsx_file, read_only=True, data_only=True)
ws = wb['Research']

data = []
for row in ws.iter_rows(min_row=2):
    symbol = row[3].value
    if not symbol:
        continue
    
    try:
        short10 = float(row[24].value) if row[24].value is not None else -99.0
        long60 = float(row[25].value) if row[25].value is not None else -99.0
        br = float(row[21].value) if row[21].value is not None else -99.0
        pgr = row[6].value
        setup = row[20].value
        
        data.append({
            'symbol': symbol,
            'short10': short10,
            'long60': long60,
            'br': br,
            'pgr': pgr,
            'setup': setup
        })
    except (ValueError, TypeError):
        continue

# Filter for setup = 1
data = [d for d in data if d['setup'] == 1]

print("Top 5 by Short10 (Setup OK):")
top_short = sorted(data, key=lambda x: x['short10'], reverse=True)[:5]
for i, d in enumerate(top_short, 1):
    print(f"{i}. {d['symbol']} (Short10: {d['short10']}, Long60: {d['long60']}, PGR: {d['pgr']})")

print("\nTop 5 by Long60 (Setup OK):")
top_long = sorted(data, key=lambda x: x['long60'], reverse=True)[:5]
for i, d in enumerate(top_long, 1):
    print(f"{i}. {d['symbol']} (Long60: {d['long60']}, Short10: {d['short10']}, PGR: {d['pgr']})")

print("\nTop 5 by Buying Ratio (BR Score, Setup OK):")
top_br = sorted(data, key=lambda x: x['br'], reverse=True)[:5]
for i, d in enumerate(top_br, 1):
    print(f"{i}. {d['symbol']} (BR: {d['br']}, Short10: {d['short10']}, PGR: {d['pgr']})")
