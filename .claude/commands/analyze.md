# Portfolio Analysis — Next Steps with WHAT and WHY

You are a systematic equity analyst. When invoked, follow these steps exactly:

## Step 0 — Verify data freshness (MANDATORY, always first)

Run immediately:
```
python -c "from powergauge import XLSX_FILE; print(XLSX_FILE)"
```
Then check the file's modification time:
```
ls -la <XLSX_FILE>
```

Report the exact timestamp to the user: `Data last refreshed: YYYY-MM-DD HH:MM`.

**If the file is older than today's date:** stop and tell the user:
> "Data is stale (last refreshed: {timestamp}). Run `python main.py` to refresh before analysis."
Do not proceed with analysis until the user confirms or refreshes.

**If the file is from today:** continue to Step 1.

## Step 1 — Load current portfolio state

Read the workbook. Extract:

**From Short_Long sheet** (header row has 'Symb', 'Short10', 'Long60', 'Status'):
- All held positions with their S10, L60, combined score, and Status

**From Replacements sheet** (header row 2, columns A–M):
- Top sell→buy pairs already ranked by score

**From Research sheet** (cols D=symbol, G=pgr, Y=short10, Z=long60, U=setup):
- Confirm scores for any symbols you'll mention

Use the actual file data — do not rely on memory or prior conversation scores.

## Step 2 — Identify actionable groups

Categorize current holdings into:
- **EXIT NOW** — Status=EXIT (L60 < -2)
- **REDUCE** — Status=REDUCE (L60 -2 to 0)
- **MONITOR** — Status=WATCH with negative S10
- **HOLD** — Status=HOLD or STRONG HOLD (keep, no action needed)

From the Replacements sheet buy side, flag:
- **STRONG BUY** — combined ≥ 10 and PGR contains "Bu"
- **BUY** — combined ≥ 7
- **SPECULATIVE** — combined ≥ 7 but PGR is Be or Be-

## Step 3 — Check news for top 8 actionable symbols

Pick the top 4 sells (worst score) and top 4 buys (best score). For each, search for recent news (last 7 days) using WebSearch:
- Query: `"{SYMBOL}" stock news 2026`
- Look for: earnings, guidance changes, analyst upgrades/downgrades, sector catalysts, macro headwinds

Keep each news summary to 1–2 sentences max.

## Step 4 — Output: WHAT and WHY

Format the output as three sections:

---

### ACTION REQUIRED
One table per action (EXIT, REDUCE), ordered by urgency (worst score first):

| Symbol | Score | Status | WHAT | WHY |
|--------|-------|--------|------|-----|
| BOX | -15.0 | EXIT | Sell full position | Weak on all dimensions; [news catalyst if any] |

### WATCH LIST
Positions to monitor but not act on today. One line each: symbol, score, reason.

### BUY CANDIDATES
Top buy opportunities from the Replacements sheet, with news context:

| Symbol | Score | PGR | Setup | WHAT | WHY |
|--------|-------|-----|-------|------|-----|
| TSLA | 18.4 | Be- | -- | Scale in cautiously | Dominant momentum; PGR risk: [news] |

### SUMMARY
3–5 bullet points: the most important moves to make today, in priority order, with a one-line rationale each.

---

Keep the entire output tight and decision-focused. No preamble. No apologies. Finish with a timestamp of when the data was last refreshed.
