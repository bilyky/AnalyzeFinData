# Position watch: arrow tags no longer crash the report

On 2026-11-06 the position-watch report crashed with `UnicodeEncodeError` on a cp1252 console:
the day's pattern tag was `RBR↑(gap,watch)`. The journal entry had already been written, but
the report never printed. `invest_plan.py` could crash the same way on a plan label.

Both scripts now call `console_safe.install()` at start (UTF-8 output; anything still
unprintable becomes `?`), the same as the other entry points.

## Deploy

Merge, then pull. No restart, no config change.

## Check

```powershell
venv_new\Scripts\python.exe scripts\monitoring\position_watch.py --symbol INTC --no-journal
```

It prints the full report, including any arrow tags, and exits without an error.

## Roll back

Revert the PR.
