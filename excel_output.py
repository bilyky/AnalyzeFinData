"""
Excel output helpers for the Research and Picks sheets.

Functions here depend only on openpyxl — no PowerGauge or API imports.
Called by check_from_xls() in powergauge.py.
"""

import os
import re
import shutil
import zipfile
import datetime
from io import BytesIO


# ── Column headers + cell-comment text for Research sheet row 1 ─────────────
# Keys are 0-based column indices. PRESERVE cols A-D (0-3) and I (8) and Q (16).
RESEARCH_HEADERS = {
    4:  ("Industry",    "Sector/industry name from Chaikin metaInfo."),
    5:  ("Prev PGR",    "Corrected PGR from the most recent prior cache file.\n1=Be-  2=Be  3=N  4=Bu  5=Bu+"),
    6:  ("PGR",         "Current Corrected Power Gauge Rating.\n1=Be-  2=Be  3=Neutral  4=Bu  5=Bu+"),
    7:  ("Ind Strength","Industry group signal from Chaikin.\nStrong / Weak / NA"),
    9:  ("Stop",        "Stop price = min(3-day lows) x 0.99.\nZeroed when entry filter (col U) fails."),
    10: ("Price",       "Last price from Chaikin API."),
    11: ("Target",      "Resistance target = highest 10-day high above current price.\nZeroed when entry filter (col U) fails."),
    12: ("R/R",         "Risk/Reward ratio = (Target - Price) / (Price - Stop).\nZeroed when entry filter (col U) fails."),
    13: ("Prev Move%",  "% price move since the previous Chaikin cache snapshot."),
    14: ("Prev %",      "Day-change% recorded in the previous Chaikin snapshot."),
    15: ("Change%",     "Today's price change% from Chaikin."),
    17: ("LT Trend",    "Long-term price trend from Chaikin.\nStrong / Neutral / Weak\n\nNote: Weak = recovery play; Strong = already extended."),
    18: ("Money Flow",  "Institutional money flow signal.\nStrong / Neutral / Weak"),
    19: ("OB/OS",       "Overbought / Oversold zone.\nOptimal / Early / Neutral / Wait"),
    20: ("Setup",       "Entry filter: 1 = passed, 0 = failed.\nPass condition: Price > SMA(20) AND Price > Close[3d ago].\nAffects Stop / Target / R/R display only."),
    21: ("BR Score",    "Buying Ratio: composite entry-quality score -10 to +10.\n\nComponents:\n  PGR (1->-2 ... 5->+2)\n  R/R (0->-1, >=0.5->+0.5, >=1->+1, >=2->+1.5, >=3->+2)\n  LT Trend (Weak->+1, Strong->-1)\n  Money Flow (Strong->+0.75, Weak->-0.75)\n  OB/OS (Optimal->+1, Early->+0.25, Wait->-0.25)\n  Industry (Weak->+0.5, Strong->-0.5)\n  PGR Delta (any change->+0.25)\n  Seasonality (-1 to +1)\n\nThresholds: >=4 strong buy | 2-4 moderate | 0-2 weak | -2-0 avoid | <=-2 strong avoid"),
    22: ("Seasonal",    "Week-of-month seasonality score.\n+1.0 strong tailwind  +0.5 mild tailwind\n 0.0 neutral           -0.5 headwind  -1.0 strong headwind\nBlank = less than 3 years of OHLCV data."),
    23: ("Win% 10d",    "Predicted 10-day win% from backtest (238k obs, 466 symbols, 2023-2025).\nBased on Buying Ratio bucket:\n  BR >=  4  -> 64.3%\n  BR 2-4    -> 57.6%\n  BR 0-2    -> 53.1%\n  BR -2-0   -> 50.3%\n  BR <= -2  -> 46.3%"),
    24: ("Short10",     "10-day entry-quality score: -10 to +10.\nWeights (336k obs, 2023-2025, NA-filtered):\n  Rel Volume (4.4%): High->+2.5, Very High->+0.5, Low->-2\n  OB/OS (4.3%): Optimal->+3, Early->+1, Wait->-2\n  Money Flow (3.5%): Strong->+3, Weak->-2\n  Industry Str (3.1%, contrarian): Weak->+2, Strong->-2\n  LT Trend (2.1%, contrarian): Weak->+1.5, Strong->-1.5\n  Seasonality: +-1.0  |  Regime: +-1.0\nRemoved: PGR, PGR Delta, R/R -- all <2% spread."),
    25: ("Long60",      "60-day position-quality score: -10 to +10.\nWeights (336k obs, 2023-2025, NA-filtered):\n  LT Trend (4.5%, contrarian): Weak->+4, Strong->-3\n  Rel Volume (2.8%): High->+2, Low->-1\n  Money Flow (2.5%): Strong->+2.5, Weak->-2\n  Industry Str (2.4%, contrarian): Weak->+2, Strong->-1.5\n  OB/OS (2.3%): Optimal->+1.5, Early->+0.5, Wait->-0.5\n  Seasonality: +-0.5  |  Regime: +-1.5\nRemoved: PGR, PGR Delta, R/R -- all <2% spread at 60d."),
}


def write_research_headers(ws):
    """Write column labels and cell comments to Research sheet row 1."""
    from openpyxl.comments import Comment
    for col_idx, (label, memo) in RESEARCH_HEADERS.items():
        cell = ws.cell(row=1, column=col_idx + 1)
        cell.value = label
        cell.comment = Comment(memo, "PowerGauge")


def write_picks_sheet(wb, picks_data: list, run_date):
    """Create/refresh the Picks sheet with four Top-5 tables (Short10 + Long60, buy + sell)."""
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    DARK_HDR  = PatternFill("solid", fgColor="2E4057")
    BLUE_HDR  = PatternFill("solid", fgColor="4472C4")
    BUY_FILL  = PatternFill("solid", fgColor="E2EFDA")
    SELL_FILL = PatternFill("solid", fgColor="FCE4D6")
    SETUP_OK  = PatternFill("solid", fgColor="C6EFCE")
    SETUP_NO  = PatternFill("solid", fgColor="D9D9D9")
    WHITE_BOLD  = Font(bold=True, color="FFFFFF")
    BOLD        = Font(bold=True)
    CENTER      = Alignment(horizontal="center", vertical="center")
    LEFT        = Alignment(horizontal="left",   vertical="center")
    THIN_BORDER = Border(
        bottom=Side(style="thin", color="BBBBBB"),
        right=Side(style="thin",  color="BBBBBB"),
    )

    if "Picks" in wb.sheetnames:
        del wb["Picks"]
    ws = wb.create_sheet("Picks")

    col_widths = [6, 9, 30, 8, 7, 7, 10, 10, 10, 7, 9]
    col_labels = ["Rank", "Symbol", "Industry", "Score", "BR", "PGR",
                  "OB/OS", "Money Flow", "LT Trend", "Setup", "Price"]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    regime = picks_data[0]['regime'] if picks_data else "N/A"

    ws.merge_cells("A1:K1")
    title = ws["A1"]
    title.value = f"Top Picks  --  {run_date.strftime('%Y-%m-%d')}    |    Market Regime: {regime}"
    title.fill = DARK_HDR
    title.font = Font(bold=True, color="FFFFFF", size=12)
    title.alignment = CENTER
    ws.row_dimensions[1].height = 22

    def write_table(start_row: int, label: str, rows: list, row_fill):
        ws.merge_cells(f"A{start_row}:K{start_row}")
        sh = ws.cell(start_row, 1)
        sh.value = label
        sh.fill = BLUE_HDR
        sh.font = WHITE_BOLD
        sh.alignment = CENTER
        ws.row_dimensions[start_row].height = 18

        hr = start_row + 1
        for col, lbl in enumerate(col_labels, 1):
            c = ws.cell(hr, col)
            c.value = lbl
            c.font  = BOLD
            c.alignment = CENTER
            c.border = THIN_BORDER
        ws.row_dimensions[hr].height = 15

        for rank, rec in enumerate(rows, 1):
            dr = hr + rank
            vals = [
                rank,
                rec['symbol'],
                rec['industry'],
                rec['score'],
                rec['br'],
                rec['pgr'],
                rec['ob_os'],
                rec['money_fl'],
                rec['lt_trend'],
                "OK" if rec['setup'] == 1 else ("--" if rec['setup'] == 0 else "?"),
                rec['price'],
            ]
            for col, val in enumerate(vals, 1):
                c = ws.cell(dr, col)
                c.value = val
                c.fill  = row_fill
                c.alignment = CENTER if col != 3 else LEFT
                c.border = THIN_BORDER
                if col == 4:
                    c.font = Font(bold=True, color="375623" if (val or 0) >= 0 else "9C0006")
                if col == 10:
                    c.fill = SETUP_OK if rec['setup'] == 1 else SETUP_NO
            ws.row_dimensions[dr].height = 14

    def top5(data, key, reverse):
        ranked = sorted((r for r in data if r.get(key) is not None),
                        key=lambda r: r[key], reverse=reverse)[:5]
        return [dict(r, score=r[key]) for r in ranked]

    write_table(3,  "TOP 5 BUY  --  Short10 (10-day entry score)",     top5(picks_data, 'short10', True),  BUY_FILL)
    write_table(11, "TOP 5 SELL  --  Short10 (10-day entry score)",    top5(picks_data, 'short10', False), SELL_FILL)
    write_table(19, "TOP 5 BUY  --  Long60  (60-day position score)",  top5(picks_data, 'long60',  True),  BUY_FILL)
    write_table(27, "TOP 5 SELL  --  Long60  (60-day position score)", top5(picks_data, 'long60',  False), SELL_FILL)


def fix_comment_shape_ids(xlsx_path: str):
    """
    Fix openpyxl bug where all comment shapeIds are written as "0".
    Reads VML spids and patches comments XML to match — prevents Excel
    "corrupt content" error on open.
    """
    with zipfile.ZipFile(xlsx_path, 'r') as zin:
        names = zin.namelist()
        comment_files = sorted(n for n in names if re.match(r'xl/comments/comment\d+\.xml', n))
        vml_files     = sorted(n for n in names if re.match(r'xl/drawings/commentsDrawing\d+\.vml', n))
        if not comment_files or not vml_files:
            return

        modified = {}
        for cf, vf in zip(comment_files, vml_files):
            vml   = zin.read(vf).decode('utf-8')
            spids = re.findall(r'id="_x0000_s(\d+)"', vml)
            if not spids:
                continue
            comments = zin.read(cf).decode('utf-8')
            it = iter(spids)
            patched = re.sub(r'shapeId="\d+"', lambda _: f'shapeId="{next(it)}"', comments)
            modified[cf] = patched.encode('utf-8')

        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            for name in names:
                zout.writestr(name, modified[name] if name in modified else zin.read(name))

    with open(xlsx_path, 'wb') as f:
        f.write(buf.getvalue())


def backup_xlsx(xlsx_path: str):
    """Copy xlsx to a timestamped backup before overwriting."""
    if not os.path.exists(xlsx_path):
        return
    ts  = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dst = os.path.join(os.path.dirname(xlsx_path), "Backup",
                       f"investment_{ts}.xlsx")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(xlsx_path, dst)
