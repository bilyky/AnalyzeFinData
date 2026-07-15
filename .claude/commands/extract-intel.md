# Extract Structural Intelligence from Email/Newsletter

Reads a financial email or newsletter (pasted as argument or from a file) and extracts the hidden structural signal: dated catalysts, supply chain constraints, overlooked symbols, and R&D topics — not just the obvious ticker recommendations.

## When to Use

Use this whenever you receive a financial newsletter, research pitch, or analyst email and want to know:
- What hard-deadline events are buried in the narrative?
- What physical/regulatory constraints does it describe?
- Which upstream/downstream symbols does it imply but not pitch?
- What analytical ideas does it surface that we could build?

## Usage

Paste the email text directly after the command:
```
/extract-intel
<paste email text here>
```

Or reference a saved file:
```
/extract-intel Data/emails/newsletter_2026-07-15.txt
```

## Steps to Execute

1. Take the text provided (either pasted inline or from the file path).

2. Run the extractor:
```python
import extract_email_intel, json

subject = "<first line or 'Email' if unknown>"
body = """<full email body>"""

intel = extract_email_intel.extract(subject, body)
print(extract_email_intel.report(intel))
```

3. For each item in `intel["missing_symbols"]`, check if it is already in our Research universe:
```python
import data_api
rows = data_api.read_research()["rows"]
existing = {r["symbol"] for r in rows}
missing = [m for m in intel.get("missing_symbols", []) if m["symbol"] not in existing]
print("Not in our universe:", missing)
```

4. For each item in `intel["rd_topics"]`, assess whether it should be added to the CLAUDE.md R&D roadmap.

5. For each item in `intel["dated_catalysts"]`, check if AETHER already has any position or watchlist exposure that intersects with the event.

## Output Format

Report in three sections:

### STRUCTURAL INTEL
- Thesis (pitch_ratio: X/10, confidence: Y)
- Dated catalysts: list with dates and impact
- Supply chain facts: physical constraints named

### SYMBOLS TO INVESTIGATE
Table of missing symbols (not in our 506-symbol universe) with why they matter.

### R&D / ROADMAP
Any analytical ideas implied by the email worth adding to CLAUDE.md.

Keep it tight. Flag the pitch_ratio prominently — a 7+/10 pitch means extract the facts but treat the BUY recommendations skeptically.
