import openpyxl
import os

xlsx_file = 'Data/investment.xlsx'
if not os.path.exists(xlsx_file):
    print(f"File {xlsx_file} not found")
    exit(1)

wb = openpyxl.load_workbook(xlsx_file, data_only=True)
ws = wb['Research']

data = []
# Assuming headers are in row 1, data starts from row 2
# Columns based on powergauge.py:
# D: Symbol (index 3)
# Y: Short10 (index 24)
# Z: Long60 (index 25)
# G: PGR (index 6)
# U: Entry Filter (index 20) - 1 means OK

for row in ws.iter_rows(min_row=2):
    symbol = row[3].value
    if not symbol: continue
    
    try:
        short10 = float(row[24].value or 0)
        long60 = float(row[25].value or 0)
        pgr = row[6].value
        setup = row[20].value
        
        data.append({
            'symbol': symbol,
            'short10': short10,
            'long60': long60,
            'pgr': pgr,
            'setup': setup
        })
    except (ValueError, TypeError):
        continue

# Filter by setup = 1 (if available)
filtered_data = [d for d in data if d['setup'] == 1]
if not filtered_data:
    filtered_data = data # Fallback if setup not populated

print("Top 5 by Short10 (Entry Score):")
top_short = sorted(filtered_data, key=lambda x: x['short10'], reverse=True)[:5]
for i, d in enumerate(top_short, 1):
    print(f"{i}. {d['symbol']} (Short10: {d['short10']}, PGR: {d['pgr']})")

print("\nTop 5 by Long60 (Position Score):")
top_long = sorted(filtered_data, key=lambda x: x['long60'], reverse=True)[:5]
for i, d in enumerate(top_long, 1):
    print(f"{i}. {d['symbol']} (Long60: {d['long60']}, PGR: {d['pgr']})")
