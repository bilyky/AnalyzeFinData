import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import openpyxl

xlsx_file = 'Data/state_of_the_day.xlsx'
wb = openpyxl.load_workbook(xlsx_file, read_only=True, data_only=True)
ws = wb['Research']

syms = ['F', 'SNPS', 'ALLT', 'TROW', 'TLN', 'RPD']
for row in ws.iter_rows(min_row=2):
    symbol = row[3].value
    if symbol in syms:
        # Col D=3, G=6, U=20, V=21, Y=24, Z=25
        sys.stdout.write(f"Symbol: {symbol}\n")
        sys.stdout.write(f"  PGR (G): {row[6].value}\n")
        sys.stdout.write(f"  Setup (U): {row[20].value}\n")
        sys.stdout.write(f"  BR (V): {row[21].value}\n")
        sys.stdout.write(f"  Short10 (Y): {row[24].value}\n")
        sys.stdout.write(f"  Long60 (Z): {row[25].value}\n")
        sys.stdout.write("-" * 20 + "\n")
