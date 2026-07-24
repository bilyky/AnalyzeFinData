# Extract Structural Intelligence from Email/Newsletter

Reads a financial email or newsletter and extracts hidden structural signal:
dated catalysts, supply chain constraints, overlooked tickers, and R&D ideas —
not just the obvious ticker recommendations.

## When to use

When you receive a financial newsletter or analyst email and want to know:
- What hard-deadline events are buried in the narrative?
- What physical/regulatory constraints does it describe?
- Which tickers does it imply but not pitch?
- What analytical ideas does it surface?

## Usage

```
/extract-intel
<paste email text here>
```

Or reference a saved file:
```
/extract-intel Data/emails/newsletter_2026-07-15.txt
```

## Steps

### 1. Get the email text
Take the text provided inline or read the file:
```bash
cat Data/emails/<filename>.txt
```

### 2. Run the extractor
```python
import extract_email_intel

subject = "<subject line or 'Email' if unknown>"
body = """<full email body>"""

intel = extract_email_intel.extract(subject, body)
print(extract_email_intel.report(intel))
```

### 3. Cross-reference against the screener universe
```python
import data_api

rows = data_api.read_research()["rows"]
universe = {r["symbol"] for r in rows}
missing = [m for m in intel.get("missing_symbols", []) if m["symbol"] not in universe]
print("Not in our 500-symbol universe:", [m["symbol"] for m in missing])
```

### 4. Assess R&D topics
For each item in `intel["rd_topics"]`, consider whether it should be added to
the R&D roadmap in `CLAUDE.md`.

### 5. Check position exposure
For each item in `intel["dated_catalysts"]`, check if any current holding
intersects with the event date.

## Output format

### STRUCTURAL INTEL
- Thesis (pitch_ratio: X/10, confidence: Y/10)
- Dated catalysts with dates and expected impact
- Supply chain / physical constraints named

### SYMBOLS TO INVESTIGATE
Table: symbol | why it matters | in universe? | action

### R&D / ROADMAP IDEAS
Analytical ideas worth adding to `CLAUDE.md`.

Flag the pitch_ratio prominently — a 7+/10 pitch means extract the facts
but treat the BUY recommendations skeptically.
