import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl
import os

xlsx_file = 'Data/state_of_the_day.xlsx'
wb = openpyxl.load_workbook(xlsx_file, data_only=True)
ws = wb['Research']

data = []
# Based on observed data:
# Index 3: Symbol
# Index 6: PGR ('Bu+', 'Bu', 'N', 'Be', 'Be-')
# Index 7: Ind Strength ('Strong', 'Weak')
# Index 10: Price
# Index 17: LT Trend
# Index 18: Money Flow
# Index 19: OB/OS

pgr_map = {'Bu+': 5, 'Bu': 4, 'N': 3, 'Be': 2, 'Be-': 1}

for row in ws.iter_rows(min_row=2):
    symbol = row[3].value
    if not symbol: continue
    
    pgr_str = row[6].value
    pgr_val = pgr_map.get(pgr_str, 0)
    
    ind_str = row[7].value
    lt_trend = row[17].value
    money_flow = row[18].value
    ob_os = row[19].value
    
    data.append({
        'symbol': symbol,
        'pgr': pgr_str,
        'pgr_val': pgr_val,
        'ind_str': ind_str,
        'lt_trend': lt_trend,
        'money_flow': money_flow,
        'ob_os': ob_os
    })

# "Best" usually means high PGR, Strong Money Flow, and maybe Optimal OB/OS.
# Let's filter for Bu+ and Bu first.
best_stocks = [d for d in data if d['pgr_val'] >= 4]

# Sort by PGR then Money Flow
def score(d):
    s = d['pgr_val'] * 10
    if d['money_flow'] == 'Strong': s += 5
    if d['ind_str'] == 'Strong': s += 2
    if d['ob_os'] == 'Optimal': s += 3
    return s

best_stocks.sort(key=score, reverse=True)

print("Potential Best 5 Stocks based on PGR and signals:")
for i, d in enumerate(best_stocks[:10], 1):
    print(f"{i}. {d['symbol']} (PGR: {d['pgr']}, Money Flow: {d['money_flow']}, Ind: {d['ind_str']}, OB/OS: {d['ob_os']})")
