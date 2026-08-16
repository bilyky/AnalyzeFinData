import os
import sys


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
        pass
