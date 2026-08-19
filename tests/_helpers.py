"""Shared builders for the unit-test suite.

Single source of truth for the fake Research-sheet schema that several suites
otherwise construct by hand. Not a test module (leading underscore, no ``test``
prefix), so unittest discovery ignores it.
"""
import openpyxl

# The Research-sheet column layout ai_portfolio_game reads. Only a handful of
# columns carry meaning (Ticker, PGR, Price, Setup, Win%, Short10, Long60); the
# rest are positional filler. Kept here so the 26-column literal lives once.
RESEARCH_HEADER = [
    "Rank", "Symbol", "Industry", "Ticker", "Sector", "Other", "PGR",
    "Other", "Other", "Other", "Price", "Other", "Other", "Other", "Other",
    "Other", "Other", "Other", "Other", "Other", "Setup", "Other", "Other",
    "Win%", "Short10", "Long60",
]


def research_workbook(*rows):
    """In-memory workbook with a 'Research' sheet: header row + the given data rows.

    Backs a mocked ``openpyxl.load_workbook`` return value. Call with no rows for a
    header-only sheet.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Research"
    ws.append(RESEARCH_HEADER)
    for row in rows:
        ws.append(row)
    return wb
