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
    24: ("Short10",     "10-day entry-quality score: -10 to +10.\nWeights (336k obs, 2023-2025, NA-filtered):\n  Rel Volume (4.4%): High->+2.5, Very High->+0.5, Low->-2\n  OB/OS (4.3%): Optimal->+3, Early->+1, Wait->-2\n  Money Flow (3.5%): Strong->+3, Weak->-2\n  Industry Str (3.1%, contrarian): Weak->+2, Strong->-2\n  LT Trend (2.1%, contrarian): Weak->+1.5, Strong->-1.5\n  Seasonality: +-1.0  |  Regime: +-1.0  |  Fibonacci: +-1.0\nRemoved: PGR, PGR Delta, R/R -- all <2% spread."),
    25: ("Long60",      "60-day position-quality score: -10 to +10.\nWeights (336k obs, 2023-2025, NA-filtered):\n  LT Trend (4.5%, contrarian): Weak->+4, Strong->-3\n  Rel Volume (2.8%): High->+2, Low->-1\n  Money Flow (2.5%): Strong->+2.5, Weak->-2\n  Industry Str (2.4%, contrarian): Weak->+2, Strong->-1.5\n  OB/OS (2.3%): Optimal->+1.5, Early->+0.5, Wait->-0.5\n  Seasonality: +-0.5  |  Regime: +-1.5  |  Fibonacci: +-0.5\nRemoved: PGR, PGR Delta, R/R -- all <2% spread at 60d."),
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


def _strip_external_formulas(data: bytes) -> bytes:
    """Replace [N] external-reference formula cells with their cached values.

    When external links are stripped from the workbook, cells that reference
    [N]Sheet!Cell become unresolvable and Excel removes them ("Removed Records:
    Formula").  We fix this by converting those cells to plain inline-string or
    empty cells using the cached value already stored in <v>.
    """
    text = data.decode('utf-8')
    if '[' not in text:
        return data  # fast exit — no external refs in this sheet

    def _replace(m):
        full = m.group(0)
        val_m = re.search(r'<v>([^<]+)</v>', full)
        if val_m and val_m.group(1).strip():
            return (f'<c t="inlineStr"><is>'
                    f'<t xml:space="preserve">{val_m.group(1)}</t>'
                    f'</is></c>')
        # No cached value — emit a minimal empty cell preserving r= and s=
        attrs = ''.join(
            f' {k}="{v}"'
            for k, v in re.findall(r'\b(r|s)="([^"]*)"', full)
        )
        return f'<c{attrs}/>'

    return re.sub(
        r'<c[^>]*>\s*<f>\[\d+\][^<]*</f>.*?</c>',
        _replace,
        text,
        flags=re.DOTALL,
    ).encode('utf-8')


def _xml_escape(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _generate_research_comment_xml(base_shape_id: int = 1025) -> bytes:
    """Generate a complete comment XML for the Research sheet row-1 headers."""
    from openpyxl.utils import get_column_letter
    RPR = ('<rPr><sz val="9"/><color indexed="81"/>'
           '<rFont val="Tahoma"/><charset val="1"/></rPr>')
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<authors><author>PowerGauge</author></authors>'
        '<commentList>'
    ]
    for i, (col_idx, (_, memo)) in enumerate(sorted(RESEARCH_HEADERS.items())):
        col_letter = get_column_letter(col_idx + 1)
        shape_id = base_shape_id + i
        text = _xml_escape(memo)
        parts.append(
            f'<comment ref="{col_letter}1" authorId="0" shapeId="{shape_id}">'
            f'<text><r>{RPR}<t xml:space="preserve">{text}</t></r></text>'
            f'</comment>'
        )
    parts.append('</commentList></comments>')
    return ''.join(parts).encode('utf-8')


def _generate_research_vml(base_shape_id: int = 1025) -> bytes:
    """Generate a complete VML drawing for the Research sheet row-1 comment boxes."""
    parts = [
        '<xml xmlns:v="urn:schemas-microsoft-com:vml"'
        ' xmlns:o="urn:schemas-microsoft-com:office:office"'
        ' xmlns:x="urn:schemas-microsoft-com:office:excel">\r\n'
        '<o:shapelayout v:ext="edit">'
        '<o:idmap v:ext="edit" data="1"/>'
        '</o:shapelayout>\r\n'
        '<v:shapetype id="_x0000_t202" coordsize="21600,21600" o:spt="202"'
        ' path="m,l,21600r21600,l21600,xe">'
        '<v:stroke joinstyle="miter"/>'
        '<v:path gradientshapeok="t" o:connecttype="rect"/>'
        '</v:shapetype>\r\n'
    ]
    for i, (col_idx, _) in enumerate(sorted(RESEARCH_HEADERS.items())):
        shape_id = base_shape_id + i
        row = 0
        col = col_idx
        anchor = f'{col + 1}, 15, {row + 1}, 2, {col + 3}, 0, {row + 5}, 3'
        parts.append(
            f'<v:shape id="_x0000_s{shape_id}" type="#_x0000_t202"'
            f' style="position:absolute;margin-left:59.25pt;margin-top:1.5pt;'
            f'width:144pt;height:72pt;z-index:{i + 1};visibility:hidden"'
            f' fillcolor="#ffffe1" o:insetmode="auto">'
            f'<v:fill color2="#ffffe1"/>'
            f'<v:shadow on="t" color="black" obscured="t"/>'
            f'<v:path o:connecttype="none"/>'
            f'<v:textbox style="mso-direction-alt:auto">'
            f'<div style="text-align:left"/>'
            f'</v:textbox>'
            f'<x:ClientData ObjectType="Note">'
            f'<x:MoveWithCells/><x:SizeWithCells/>'
            f'<x:Anchor>{anchor}</x:Anchor>'
            f'<x:AutoFill>False</x:AutoFill>'
            f'<x:Row>{row}</x:Row>'
            f'<x:Column>{col}</x:Column>'
            f'</x:ClientData>'
            f'</v:shape>\r\n'
        )
    parts.append('</xml>')
    return ''.join(parts).encode('utf-8')


def _fix_comment_xml(data: bytes) -> bytes:
    """Add XML declaration and convert plain-text comments to rich-text format.

    openpyxl emits <text><t>plain</t></text>; Excel requires (and generates)
    <text><r><rPr>...</rPr><t xml:space="preserve">plain</t></r></text> plus
    an explicit XML declaration.  Without these, Excel silently removes all
    comments and reports "Removed Records: Comments from /xl/comments/...".
    """
    text = data.decode('utf-8')
    RPR = ('<rPr><sz val="9"/><color indexed="81"/>'
           '<rFont val="Tahoma"/><charset val="1"/></rPr>')

    def _to_rich(m):
        content = m.group(1)
        return f'<text><r>{RPR}<t xml:space="preserve">{content}</t></r></text>'

    # Only convert simple <text><t>…</t></text>; leave already-rich <text><r>… alone
    text = re.sub(r'<text><t>(.*?)</t></text>', _to_rich, text, flags=re.DOTALL)
    result = text.encode('utf-8')
    if not result.startswith(b'<?xml'):
        result = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + result
    return result


def _fix_vml_ns(data: bytes) -> bytes:
    """Rename openpyxl's ns0/ns1/ns2 VML prefixes to standard o/v/x and add
    missing <x:Anchor> elements.

    Two openpyxl artefacts:
    1. Non-standard namespace prefixes (ns0/ns1/ns2) — Excel's legacy VML parser
       may reject arbitrary prefixes even though they are semantically correct.
    2. Missing <x:Anchor> — openpyxl omits the anchor box from its generated VML.
       Without it modern Excel cannot position the comment box and silently removes
       the whole comment set, emitting "Removed Records: Comments …".
    """
    text = data.decode('utf-8')

    # ── 1. Namespace prefix rename ──────────────────────────────────────────────
    text = (text
        .replace('xmlns:ns0="urn:schemas-microsoft-com:office:office"',
                 'xmlns:o="urn:schemas-microsoft-com:office:office"')
        .replace('xmlns:ns1="urn:schemas-microsoft-com:vml"',
                 'xmlns:v="urn:schemas-microsoft-com:vml"')
        .replace('xmlns:ns2="urn:schemas-microsoft-com:office:excel"',
                 'xmlns:x="urn:schemas-microsoft-com:office:excel"')
        .replace('ns0:', 'o:').replace('ns1:', 'v:').replace('ns2:', 'x:')
    )

    # ── 2. Add missing <x:Anchor> to comment shapes ────────────────────────────
    def _add_anchor(m):
        cd = m.group(0)
        if '<x:Anchor>' in cd:
            return cd
        row_m = re.search(r'<x:Row>(\d+)</x:Row>', cd)
        col_m = re.search(r'<x:Column>(\d+)</x:Column>', cd)
        if not row_m or not col_m:
            return cd
        row = int(row_m.group(1))
        col = int(col_m.group(1))
        anchor = (f'<x:Anchor>{col + 1}, 15, {row + 1}, 2, '
                  f'{col + 3}, 0, {row + 5}, 3</x:Anchor>')
        return cd.replace('<x:AutoFill>', anchor + '<x:AutoFill>', 1)

    text = re.sub(
        r'<x:ClientData ObjectType="Note">.*?</x:ClientData>',
        _add_anchor,
        text,
        flags=re.DOTALL,
    )

    return text.encode('utf-8')


def fix_comment_shape_ids(xlsx_path: str, original_xlsx: str = None,
                          touched_sheet_names: set = None):
    """Fix openpyxl-save artefacts in one zip pass.

    1. Untouched-sheet preservation — if original_xlsx is provided, worksheet XML
       files for sheets NOT in touched_sheet_names (plus their associated comment
       and drawing VML files) are restored verbatim from the original to prevent
       openpyxl's load/save cycle from corrupting formulas or comments.
    2. Comment shapeId="0" bug — patches shapeIds from the matching VML for any
       comment XML that was NOT restored from the original.
    3. External-link ghost references — strips xl/externalLinks/* and removes their
       entries from workbook.xml + workbook.xml.rels.
    """
    # ── Build restore map from original ─────────────────────────────────────
    restore_from_orig: dict[str, bytes] = {}

    if original_xlsx:
        try:
            with zipfile.ZipFile(original_xlsx, 'r') as zorig:
                orig_names = set(zorig.namelist())
                name_to_rid: dict[str, str] = {}
                rid_to_target: dict[str, str] = {}

                if 'xl/workbook.xml' in orig_names:
                    _wbx = zorig.read('xl/workbook.xml').decode('utf-8')
                    for chunk in re.findall(r'<sheet\s([^>]*/?>)', _wbx):
                        nm = re.search(r'\bname="([^"]*)"', chunk)
                        ri = re.search(r'\br:id="([^"]*)"', chunk)
                        if nm and ri:
                            name_to_rid[nm.group(1)] = ri.group(1)

                if 'xl/_rels/workbook.xml.rels' in orig_names:
                    _wbr = zorig.read('xl/_rels/workbook.xml.rels').decode('utf-8')
                    for chunk in re.findall(r'<Relationship\s([^>]*/?>)', _wbr):
                        id_m = re.search(r'\bId="([^"]*)"', chunk)
                        tgt  = re.search(r'\bTarget="([^"]*)"', chunk)
                        if id_m and tgt:
                            rid_to_target[id_m.group(1)] = tgt.group(1)

                ts = touched_sheet_names or set()
                touched_xml: set[str] = set()
                for sname, rid in name_to_rid.items():
                    tgt = rid_to_target.get(rid, '')
                    if tgt and sname in ts:
                        xf = ('xl/' + tgt) if not tgt.startswith('/') else tgt.lstrip('/')
                        touched_xml.add(xf)

                for sname, rid in name_to_rid.items():
                    tgt = rid_to_target.get(rid, '')
                    if not tgt:
                        continue
                    xf = ('xl/' + tgt) if not tgt.startswith('/') else tgt.lstrip('/')
                    if xf in touched_xml or xf not in orig_names:
                        continue

                    restore_from_orig[xf] = zorig.read(xf)

                    # Restore associated rels + comment/drawing files
                    parts     = xf.split('/')      # ['xl', 'worksheets', 'sheetN.xml']
                    rels_path = '/'.join(parts[:-1]) + '/_rels/' + parts[-1] + '.rels'
                    if rels_path in orig_names:
                        rels_bytes = zorig.read(rels_path)
                        restore_from_orig[rels_path] = rels_bytes
                        for rel_tgt in re.findall(r'\bTarget="([^"]*)"', rels_bytes.decode('utf-8')):
                            if rel_tgt.startswith('../'):
                                resolved = 'xl/' + rel_tgt[3:]
                            elif rel_tgt.startswith('/'):
                                resolved = rel_tgt.lstrip('/')
                            else:
                                resolved = 'xl/worksheets/' + rel_tgt
                            if resolved in orig_names:
                                restore_from_orig[resolved] = zorig.read(resolved)
        except Exception:
            restore_from_orig.clear()   # on any error, fall back to default behaviour

    with zipfile.ZipFile(xlsx_path, 'r') as zin:
        names = zin.namelist()

        # ── 1. shapeId patch (only for comment XMLs not restored from original) ──
        comment_files = sorted(n for n in names if re.match(r'xl/comments/comment\d+\.xml', n))
        vml_files     = sorted(n for n in names if re.match(r'xl/drawings/commentsDrawing\d+\.vml', n))

        modified: dict[str, bytes] = {}

        # ── 0. Always regenerate Research sheet's comment+VML from scratch ────
        # This bypasses all openpyxl/backup-restore uncertainty: our generated
        # content is guaranteed to be valid Excel XML with matching shapeIds.
        try:
            _wb_xml  = zin.read('xl/workbook.xml').decode('utf-8') if 'xl/workbook.xml' in names else ''
            _wb_rels = zin.read('xl/_rels/workbook.xml.rels').decode('utf-8') if 'xl/_rels/workbook.xml.rels' in names else ''
            _research_rid = None
            for _chunk in re.findall(r'<sheet\s[^>]+/?>', _wb_xml):
                _nm = re.search(r'\bname="([^"]*)"', _chunk)
                _ri = re.search(r'\br:id="([^"]*)"', _chunk)
                if _nm and _nm.group(1) == 'Research' and _ri:
                    _research_rid = _ri.group(1)
                    break
            if _research_rid:
                _research_tgt = None
                for _chunk in re.findall(r'<Relationship\s[^>]+/?>', _wb_rels):
                    _id_m = re.search(r'\bId="([^"]*)"', _chunk)
                    _tgt  = re.search(r'\bTarget="([^"]*)"', _chunk)
                    if _id_m and _id_m.group(1) == _research_rid and _tgt:
                        _research_tgt = _tgt.group(1)
                        break
                if _research_tgt:
                    _rxf = ('xl/' + _research_tgt) if not _research_tgt.startswith('/') else _research_tgt.lstrip('/')
                    _rparts = _rxf.split('/')
                    _rels_p = '/'.join(_rparts[:-1]) + '/_rels/' + _rparts[-1] + '.rels'
                    if _rels_p in names:
                        _rrels = zin.read(_rels_p).decode('utf-8')
                        for _rel_tgt in re.findall(r'\bTarget="([^"]*)"', _rrels):
                            if _rel_tgt.startswith('/'):
                                _res = _rel_tgt.lstrip('/')
                            elif _rel_tgt.startswith('../'):
                                _res = 'xl/' + _rel_tgt[3:]
                            else:
                                _res = 'xl/worksheets/' + _rel_tgt
                            if re.match(r'xl/comments/comment\d+\.xml$', _res):
                                modified[_res] = _generate_research_comment_xml()
                                restore_from_orig.pop(_res, None)
                            elif re.match(r'xl/drawings/commentsDrawing\d+\.vml$', _res):
                                modified[_res] = _generate_research_vml()
                                restore_from_orig.pop(_res, None)
        except Exception:
            pass  # fall back to existing shapeId patch + _fix_comment_xml/_fix_vml_ns

        # ── 1. shapeId patch (only for comment XMLs not restored from original) ──
        for cf, vf in zip(comment_files, vml_files):
            if cf in restore_from_orig or cf in modified:
                continue    # restored from original or already regenerated
            vml   = zin.read(vf).decode('utf-8')
            spids = re.findall(r'id="_x0000_s(\d+)"', vml)
            if not spids:
                continue
            comments = zin.read(cf).decode('utf-8')
            it = iter(spids)
            patched = re.sub(r'shapeId="\d+"', lambda _: f'shapeId="{next(it)}"', comments)
            modified[cf] = patched.encode('utf-8')

        # ── 2. External-link strip ──────────────────────────────────────────
        ext_link_files = {n for n in names if n.startswith('xl/externalLinks/')}

        if ext_link_files:
            wb_rels_name = 'xl/_rels/workbook.xml.rels'
            if wb_rels_name in names:
                rels_xml = zin.read(wb_rels_name).decode('utf-8')
                rels_xml = re.sub(
                    r'<Relationship[^>]+Type="[^"]*externalLinkPath"[^/]*/?>',
                    '', rels_xml,
                )
                modified[wb_rels_name] = rels_xml.encode('utf-8')

            wb_name = 'xl/workbook.xml'
            if wb_name in names:
                wb_xml = zin.read(wb_name).decode('utf-8')
                wb_xml = re.sub(r'<externalReferences\b[^>]*>.*?</externalReferences>', '',
                                wb_xml, flags=re.DOTALL)
                modified[wb_name] = wb_xml.encode('utf-8')

        # ── Rewrite zip ─────────────────────────────────────────────────────
        skip = ext_link_files
        if not modified and not skip and not restore_from_orig:
            return  # nothing to do

        _is_vml     = re.compile(r'xl/drawings/commentsDrawing\d+\.vml').fullmatch
        _is_ws      = re.compile(r'xl/worksheets/sheet\d+\.xml').fullmatch
        _is_comment = re.compile(r'xl/comments/comment\d+\.xml').fullmatch

        def _apply_fixes(name, data):
            if _is_vml(name):
                return _fix_vml_ns(data)
            if _is_ws(name):
                return _strip_external_formulas(data)
            if _is_comment(name):
                return _fix_comment_xml(data)
            return data

        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
            written: set[str] = set()
            for name in names:
                if name in skip:
                    continue
                if name in restore_from_orig:
                    data = restore_from_orig[name]
                elif name in modified:
                    data = modified[name]
                else:
                    data = zin.read(name)
                zout.writestr(name, _apply_fixes(name, data))
                written.add(name)
            # Re-add any original files that openpyxl dropped (e.g. rels for
            # unmodified sheets that openpyxl decided not to write).
            for fname, fbytes in restore_from_orig.items():
                if fname not in written:
                    zout.writestr(fname, _apply_fixes(fname, fbytes))

    with open(xlsx_path, 'wb') as f:
        f.write(buf.getvalue())


def update_short_long_scores(wb, picks_lookup: dict, quotes: dict, positions: list[dict]):
    """Sync the Short_Long sheet with E*TRADE positions and write score columns Q-W.

    - Rows where symbol is no longer in E*TRADE positions → deleted
    - E*TRADE positions not yet in the sheet → new row appended to the relevant table
    - Existing rows → col E (Top) updated from live quotes
    - All data rows → cols Q-W written: Short10, Long60, Win%, Status, Days Green, Days Red, In Profit
    - Rows 51+ (user notes) are never touched.
    """
    import datetime
    from openpyxl.styles import PatternFill, Font, Alignment
    from scoring import predicted_win_pct as _pwp

    if "Short_Long" not in wb.sheetnames:
        return
    ws = wb["Short_Long"]

    # ── Styling constants ────────────────────────────────────────────────────
    GRN_FILL  = PatternFill("solid", fgColor="C6EFCE")
    RED_FILL  = PatternFill("solid", fgColor="FCE4D6")
    YEL_FILL  = PatternFill("solid", fgColor="FFEB9C")
    ORG_FILL  = PatternFill("solid", fgColor="FFCC99")
    LGN_FILL  = PatternFill("solid", fgColor="E2EFDA")
    GRY_FILL  = PatternFill("solid", fgColor="D9D9D9")
    GRN_FONT  = Font(bold=True, color="375623")
    RED_FONT  = Font(bold=True, color="9C0006")
    CTR       = Alignment(horizontal="center", vertical="center")

    STATUS_CFG = [
        (4,    "STRONG HOLD", GRN_FILL),
        (2,    "HOLD",        LGN_FILL),
        (0,    "WATCH",       YEL_FILL),
        (-2,   "REDUCE",      ORG_FILL),
        (-999, "EXIT",        RED_FILL),
    ]

    def _status(score):
        if score is None:
            return "N/A", GRY_FILL
        for thresh, label, fill in STATUS_CFG:
            if score >= thresh:
                return label, fill
        return "EXIT", RED_FILL

    today = datetime.date.today()

    # ── 1. Locate header row and discover data rows (max row 50) ─────────────
    HDR_ROW = None
    for row in ws.iter_rows(min_row=1, max_row=10):
        if row[1].value == "Symb":          # col B
            HDR_ROW = row[0].row
            break

    DATA_MAX = 50   # never touch rows beyond this

    def _is_data_row(r):
        """True if the row looks like a position entry (col B is a non-empty string ≠ 'Symb')."""
        v = r[1].value  # col B (0-based index 1)
        return isinstance(v, str) and v.strip() and v.strip().upper() != "SYMB"

    # Build {sym: row_index} for current data rows
    sheet_rows: dict[str, int] = {}
    for row in ws.iter_rows(min_row=3, max_row=DATA_MAX):
        if _is_data_row(row):
            sym = row[1].value.strip().upper()
            if sym not in sheet_rows:          # keep first occurrence if dup
                sheet_rows[sym] = row[0].row

    etrade_syms  = {p["symbol"] for p in positions}
    etrade_by_sym = {p["symbol"]: p for p in positions}
    to_remove    = set(sheet_rows) - etrade_syms
    to_add       = etrade_syms - set(sheet_rows)

    # ── 2. Remove closed positions (reverse order avoids row-shift bugs) ──────
    for sym in to_remove:
        ws.delete_rows(sheet_rows[sym])

    # Rebuild row map after deletions
    sheet_rows = {}
    for row in ws.iter_rows(min_row=3, max_row=DATA_MAX + len(to_remove)):
        if _is_data_row(row):
            sym = row[1].value.strip().upper()
            if sym not in sheet_rows:
                sheet_rows[sym] = row[0].row

    # ── 3. Add new E*TRADE positions ─────────────────────────────────────────
    for sym in sorted(to_add):
        pos  = etrade_by_sym[sym]
        pick = picks_lookup.get(sym, {})
        s10  = pick.get("short10")
        l60  = pick.get("long60")
        # Prefer Short table if Short10 score is better, else Long table
        # Find last occupied data row in range 3-26 (Table 1) and 27-DATA_MAX (Table 2)
        t1_last = max((r for r in sheet_rows.values() if r <= 26), default=2)
        t2_last = max((r for r in sheet_rows.values() if r >= 27), default=26)
        insert_after = t1_last if (s10 or 0) >= (l60 or 0) else t2_last
        new_row_num  = insert_after + 1
        ws.insert_rows(new_row_num)
        ws.cell(new_row_num, 1).value  = None          # rank — renumbered below
        ws.cell(new_row_num, 2).value  = sym
        ws.cell(new_row_num, 3).value  = pos["qty"]
        ws.cell(new_row_num, 4).value  = pos["cost"]
        ws.cell(new_row_num, 5).value  = pos["price"]  # Top = current price
        ws.cell(new_row_num, 11).value = today          # K = buy date
        ws.cell(new_row_num, 12).value = pick.get("industry", "")
        sheet_rows[sym] = new_row_num

    # ── 4. Renumber col A within each table ──────────────────────────────────
    def _renumber(rows_in_range):
        for rank, rn in enumerate(sorted(rows_in_range), 1):
            ws.cell(rn, 1).value = rank

    all_data_rows = []
    for row in ws.iter_rows(min_row=3, max_row=DATA_MAX + len(to_add) + 5):
        if _is_data_row(row):
            all_data_rows.append(row[0].row)

    t1_rows = [r for r in all_data_rows if r <= 26]
    t2_rows = [r for r in all_data_rows if r >= 27]
    _renumber(t1_rows)
    _renumber(t2_rows)

    # Rebuild row map one final time
    sheet_rows = {}
    for row in ws.iter_rows(min_row=3, max_row=DATA_MAX + len(to_add) + 5):
        if _is_data_row(row):
            sym = row[1].value.strip().upper()
            if sym not in sheet_rows:
                sheet_rows[sym] = row[0].row

    # ── 5. Write header for new score columns ────────────────────────────────
    if HDR_ROW:
        for col, label in enumerate(
            ["Short10", "Long60", "Win%", "Status", "Days G", "Days R", "In Profit"],
            start=17,   # col Q = 17
        ):
            ws.cell(HDR_ROW, col).value = label

    # ── 6. Update Top (col E) and write score columns Q-W ────────────────────
    for sym, rn in sheet_rows.items():
        # Update current price (col E = 5)
        if sym in quotes:
            ws.cell(rn, 5).value = quotes[sym]

        top  = ws.cell(rn, 5).value   # E: Top/current price
        buy  = ws.cell(rn, 4).value   # D: cost basis
        qty  = ws.cell(rn, 3).value   # C: quantity
        kval = ws.cell(rn, 11).value  # K: buy date

        pick = picks_lookup.get(sym, {})
        s10  = pick.get("short10")
        l60  = pick.get("long60")
        br   = pick.get("br")
        winp = round(_pwp(br) * 100, 1) if br is not None else None
        status_label, status_fill = _status(l60)

        # Days held
        if isinstance(kval, datetime.datetime):
            kval = kval.date()
        days = (today - kval).days if isinstance(kval, datetime.date) else None

        top_f = float(top or 0)
        buy_f = float(buy or 0)
        in_profit = top_f > buy_f if (top_f and buy_f) else None

        days_green = days if in_profit else 0
        days_red   = days if (in_profit is False) else 0

        # Q: Short10
        c = ws.cell(rn, 17)
        c.value = s10
        c.font  = GRN_FONT if (s10 or 0) >= 0 else RED_FONT
        c.alignment = CTR

        # R: Long60
        c = ws.cell(rn, 18)
        c.value = l60
        c.font  = GRN_FONT if (l60 or 0) >= 0 else RED_FONT
        c.alignment = CTR

        # S: Win%
        c = ws.cell(rn, 19)
        c.value = f"{winp}%" if winp is not None else None
        c.alignment = CTR

        # T: Status
        c = ws.cell(rn, 20)
        c.value = status_label
        c.fill  = status_fill
        c.alignment = CTR

        # U: Days Green
        c = ws.cell(rn, 21)
        c.value = days_green
        c.fill  = GRN_FILL if in_profit else GRY_FILL
        c.alignment = CTR

        # V: Days Red
        c = ws.cell(rn, 22)
        c.value = days_red
        c.fill  = RED_FILL if (in_profit is False) else GRY_FILL
        c.alignment = CTR

        # W: In Profit
        c = ws.cell(rn, 23)
        if in_profit is True:
            c.value = "YES"
            c.fill  = GRN_FILL
            c.font  = GRN_FONT
        elif in_profit is False:
            c.value = "NO"
            c.fill  = RED_FILL
            c.font  = RED_FONT
        else:
            c.value = None
        c.alignment = CTR


def backup_xlsx(xlsx_path: str) -> str | None:
    """Copy xlsx to a timestamped backup in Backup/{year}/. Returns backup path or None."""
    if not os.path.exists(xlsx_path):
        return None
    now = datetime.datetime.now()
    ts  = now.strftime("%Y-%m-%d_%H%M%S")
    dst = os.path.join(os.path.dirname(xlsx_path), "Backup",
                       str(now.year), f"investment_{ts}.xlsx")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(xlsx_path, dst)
    print(f"Backup saved to {dst}")
    return dst
