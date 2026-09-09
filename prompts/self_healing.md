[AETHER DIAGNOSTIC ADVISORY & RECOVERY PROTOCOL]

The background AETHER trading pipeline has crashed. Follow every step below in order.
This is a READ-ONLY diagnostic session. You are strictly forbidden from modifying any files or executing code edits. Your goal is to analyze the failure and output a clear, highly precise surgical recommendation for the developer to review.

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
## Step 3 — Diagnose the root cause

State in plain language:
1. What expression or operation raised the exception
2. Why it failed (missing key, None value, file not found, changed API shape, etc.)
3. What the code expected vs. what it actually received

---
## Step 4 — Formulate the Recommended Surgical Fix

Formulate a minimal, precise surgical fix to address the root cause. Rules:
- No refactoring, no new abstractions, no style cleanup.
- Focus strictly on safety and stability (e.g. adding guards or handling edge cases).
- Describe the exact lines to change.

---
## Step 5 — Present the Recommendation

Output your final diagnosis and the exact recommended code changes inside a clear Markdown code block (preferably using unified diff format) so the developer can inspect, approve, and apply it in one glance.
