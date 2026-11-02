# PROD notes

Step-by-step PROD instructions written per PR or review, oldest first by file name. Each note says what
to back up, the exact commands, what to check after, and how to roll back. They are point-in-time:
a later note or merged PR can supersede an earlier one (e.g. `patch_ai_models_2026_10_20.py` is
superseded by `2026-10-12-ai-tiers-192-enable.md` once #192 is deployed).

A note ships in the same PR as the change it deploys (`.claude/commands/ship-pr.md` §2), named
`YYYY-MM-DD-prod-<topic>-<PR>.md`, and the PR body's deploy section points to it.

Run the commands from the repo root on PROD. The `.py` helpers default to dry runs and back up before
writing.

Not in the repo (kept only in the private `Data/notes/` on the dev machine):
- `2026-10-08-prod-oceanview-170.md`: it holds real account last-4 digits. Three notes here refer to it.

Before adding a note: this repo is public. No account digits, balances, keys, tokens, email
addresses, internal IPs or share paths.
