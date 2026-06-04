import openpyxl

xlsx_file = 'Data/investment.xlsx'
wb = openpyxl.load_workbook(xlsx_file, read_only=True, data_only=True)
ws = wb['Research']

syms = ['F', 'SNPS', 'ALLT', 'TROW', 'TLN', 'RPD']
for row in ws.iter_rows(min_row=2):
    symbol = row[3].value
    if symbol in syms:
        # Col D=3, G=6, U=20, V=21, Y=24, Z=25
        print(f"Symbol: {symbol}")
        print(f"  PGR (G): {row[6].value}")
        print(f"  Setup (U): {row[20].value}")
        print(f"  BR (V): {row[21].value}")
        print(f"  Short10 (Y): {row[24].value}")
        print(f"  Long60 (Z): {row[25].value}")
        print("-" * 20)
