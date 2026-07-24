# Watchdog — Analyze Errors and Propose Fix

Diagnoses errors in the AETHER trading pipeline, reads the relevant source code,
and proposes a minimal fix. Follow these steps exactly.

## Step 1 — Collect all recent errors

Scan the plain-text and structured logs:
```bash
python -c "import watchdog; errors = watchdog.check_logs(); [print(e) for e in errors]"
```

Extract the latest traceback from the autonomous pipeline log:
```bash
python -c "import watchdog; tb = watchdog.extract_latest_traceback(); print(tb or '(no traceback found)')"
```

Tail the structured JSON log for the last 30 ERROR entries:
```bash
python -c "
import json
from pathlib import Path
p = Path('Data/logs/aether.jsonl')
if p.exists():
    lines = p.read_text(encoding='utf-8', errors='ignore').splitlines()
    errors = [json.loads(l) for l in lines if l.strip() and json.loads(l).get('level','').upper()=='ERROR']
    for e in errors[-30:]:
        print(e.get('ts','')[:19], e.get('module',''), e.get('msg',''), e.get('exc','')[:200] if e.get('exc') else '')
else:
    print('(aether.jsonl not found)')
"
```

If all three sources are empty: **"No errors found in the last hour. All systems nominal."** — stop.

## Step 2 — Identify the root error

From the collected errors identify:
- The **primary error** (root exception, not a downstream consequence)
- The **exact file and line number** from the traceback
- The **error type** (KeyError, AttributeError, FileNotFoundError, etc.)

## Step 3 — Read the relevant source code

Read the exact file and function where the error originates.
Use the line number ±20 lines for context.
Read any directly called functions in the traceback chain.

## Step 4 — Diagnose

State clearly:
1. **What failed** — the exact expression or operation that raised the exception
2. **Why it failed** — root cause (missing key, None value, changed API shape, file not found)
3. **What the code expected vs. what it got**

3–5 sentences. No speculation beyond what the code and traceback show.

## Step 5 — Propose a fix

Write the minimal surgical fix:

```
FILE: <path/to/file.py>  LINE: ~<N>

BEFORE:
    <exact current code>

AFTER:
    <fixed code>
```

Rules:
- Change only what is necessary to fix the root cause
- Do not refactor surrounding code
- If a guard is needed (`if x is not None`), add only that guard
- If the root cause is a missing config key, explain what must be set — do not silently default

## Step 6 — Ask before applying

Present the diagnosis and fix. Ask: **"Apply this fix? (yes / no / modify)"**

If confirmed, apply the edit and run:
```bash
python -m unittest discover tests 2>&1 | tail -5
```

If tests fail, do not commit — show the failure and stop.

## Step 7 — Verify

```bash
python -c "import watchdog; errors = watchdog.check_logs(); print(f'{len(errors)} errors remaining') if errors else print('All clear.')"
```

If errors remain, repeat from Step 2 with the next error.
