# Portfolio Analysis — Next Steps with WHAT and WHY

You are a systematic equity analyst for this trading system. When invoked, follow these steps exactly.

## Step 0 — Discover the workbook path and verify freshness (MANDATORY)

First, find the main Excel workbook:
```bash
python -c "from powergauge import XLSX_FILE; print(XLSX_FILE)"
```

Check its modification time:
```bash
python -c "
import os, datetime
from powergauge import XLSX_FILE
mtime = os.path.getmtime(XLSX_FILE)
print(datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M'))
"
```

Report the exact timestamp to the user: `Data last refreshed: YYYY-MM-DD HH:MM`.

**If the file is older than today:** stop and tell the user to refresh first:
```bash
python autonomous_pipeline.py
```

**If the file is from today:** continue to Step 1.

## Step 1 — Load current portfolio state

Read the workbook using Python:
```python
import openpyxl
from powergauge import XLSX_FILE
wb = openpyxl.load_workbook(XLSX_FILE, data_only=True, read_only=True)
```

Extract from each sheet:
- **Short_Long** — header row contains `Symb`, `Short10`, `Long60`, `Status`. These are the held positions.
- **Replacements** — header row 2. Top sell→buy rotation pairs ranked by score.
- **Research** — cols D=symbol, G=pgr, Y=short10, Z=long60, U=setup, AA=patterns. Full screener output.

Use the actual file data. Do not rely on memory or prior conversation scores.

## Step 2 — Identify actionable groups

Categorize current holdings:
- **EXIT NOW** — Status=EXIT (L60 < -2)
- **REDUCE** — Status=REDUCE (L60 -2 to 0)
- **MONITOR** — Status=WATCH with negative S10
- **HOLD** — Status=HOLD or STRONG HOLD

From the Replacements sheet, flag buy candidates:
- **STRONG BUY** — combined ≥ 10 and PGR contains "Bu"
- **BUY** — combined ≥ 7
- **SPECULATIVE** — combined ≥ 7 but PGR is Be or Be-

## Step 3 — Check news for top 8 actionable symbols

Pick the top 4 sells (worst score) and top 4 buys (best score). For each, search for recent news:
- Query: `"{SYMBOL}" stock news {CURRENT_YEAR}`
- Look for: earnings, guidance changes, analyst upgrades/downgrades, sector catalysts, macro headwinds

Keep each news summary to 1–2 sentences max.

## Step 4 — Output: WHAT and WHY

For each symbol provide a WHY synthesizing:
1. **Technical vs. Fundamental Gap:** Contrast PGR (fundamental) with S10/L60 (momentum).
2. **Pattern Evidence:** If col AA is non-blank, cite it explicitly (e.g. "Cup&Handle breakout confirmed").
3. **Specific Catalysts:** From news in Step 3.
4. **Actionable Advice:** "Add to winners," "Hold/Stay patient," or "Exit to protect capital."

### OUTPUT FORMAT

**ACTION REQUIRED** (EXIT/REDUCE — worst score first)
| Symbol | Score | Status | Patterns | WHAT | WHY |

**WATCH LIST** — one line each: symbol, score, patterns, reason

**BUY CANDIDATES** (from Replacements sheet)
| Symbol | Score | PGR | Setup | Patterns | WHAT | WHY |

**SUMMARY** — 3–5 bullets, most important moves in priority order

No preamble. No apologies. Finish with the data freshness timestamp.
