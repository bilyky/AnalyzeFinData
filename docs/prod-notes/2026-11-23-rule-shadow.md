# Buy rules in shadow (plan T0.4)

## What it does
- For every game buy candidate that passes the game's own rules, one line in `Data/rule_shadow.jsonl` says whether the
  R&D #113 loss filter would hold it back, and why. The game's decision doesn't change.
- For every idea the Oracle checks (the Oracle applies #113 for real), one line with the same fields.
- `aether/rule_shadow.py` is the only writer. The log is append-only and bounded to 20,000 lines; a write failure is
  only a warning.
- The scorer (`rule_shadow.score`) gives the forward return after 5 and 20 sessions for "would hold back" vs
  "admitted", with n. The weekly reflection reads it.

## PROD check
After the pull, the next game run with a buy candidate, and the next Oracle email, should each add lines to
`Data\rule_shadow.jsonl` (source `game` / `oracle`, rule `R&D #113`). On two trading days in a row the file should
grow; the trades themselves don't change.

Rollback: revert the PR. The log file can be deleted; nothing reads it except the scorer.
