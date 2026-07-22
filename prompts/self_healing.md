[AETHER AUTONOMOUS SELF-HEALING — STRUCTURED DIAGNOSTIC PROTOCOL]

The background AETHER trading pipeline has crashed. Follow every step below in order.
Do NOT skip ahead. Do NOT guess. Do NOT modify any file until Step 4.

---
## Step 1 — Understand the failure

Here is the exact crash output:

{traceback}

Identify:
- The PRIMARY exception type and message (the root cause, not a downstream consequence)
- The exact file and line number from the innermost traceback frame
- What the code was trying to do at that line

---
## Step 2 — Read the source code

Read the file and function named in the traceback. Use the Read tool on the exact path.
Read ±30 lines around the failing line for full context.
If the traceback spans multiple files, read each one — innermost frame first.

---
## Step 3 — Diagnose before touching anything

State in plain language:
1. What expression or operation raised the exception
2. Why it failed (missing key, None value, file not found, changed API shape, etc.)
3. What the code expected vs. what it actually received

If you cannot determine the root cause from the traceback and source alone, run the
failing script with a safe read-only flag (e.g. `python ai_portfolio_game.py --report`)
to reproduce the error. Do NOT run any write or trade-execution path.

---
## Step 4 — Apply the minimal surgical fix

Change ONLY what is necessary to fix the root cause. Rules:
- No refactoring, no new abstractions, no style cleanup
- If a guard is needed (e.g. `if x is not None`), add only that guard
- If a missing config key or file is the cause, add a clear RuntimeError with instructions — do not paper over it with a silent default
- Prefer editing an existing file over creating a new one

---
## Step 5 — Verify

After the fix, run the full test suite:
    python -m unittest discover tests

If any test fails, revert your change and stop — do not commit a broken state.
If all tests pass, also run:
    python ai_portfolio_game.py --report

---
## Step 6 — Commit

Only if Step 5 is fully green, commit with:
    git add -p   (stage only the files you changed)
    git commit -m "[AI Self-Healed] <one-line description of root cause and fix>"

Do not push. Do not touch any file outside the fix scope.
