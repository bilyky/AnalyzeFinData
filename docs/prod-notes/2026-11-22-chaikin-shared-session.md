# Shared Chaikin session (plan T0.7)

## Why
Each machine kept its own `Data/session.json`. Dev had none, so a dev run that needed Chaikin opened a browser login
or failed. On 2026-11-22 one renewed session worked on both machines at once: dev used PROD's session read-only at
10:12:57 PT and PROD's probe still said `valid` afterwards. So one shared copy is safe.

## What this PR adds
- `aether/chaikin_store.py`: one encrypted row (`chaikin_session`) in the same database as the shared E*TRADE token.
  The key is derived from the E*TRADE production consumer secret, which both machines have and the database doesn't.
- `powergauge._load_session_from_file()` / `_save_session_to_file()` go through it, so every caller is covered:
  the renewal (`chaikin_reauth`), the data API and preflight.
- **Off until opted in:** only `AETHER_CHAIKIN_STORE=db` turns it on. A database URL alone doesn't.
- `session.json` stays: every save writes it first. On load, the newer of the file and the row wins, so a stale row
  never overrides a fresher renewal.
- Database down: it uses `session.json` and logs one warning per process.

## PROD steps (in this order)
1. Pull `main`.
2. In a NEW PowerShell window on PROD, set the opt-in for the user, then open another new window:
   `[Environment]::SetEnvironmentVariable("AETHER_CHAIKIN_STORE", "db", "User")`
3. In the new window: `python -m aether.chaikin_store --seed`. It copies PROD's `session.json` into the row.
4. `python -m aether.chaikin_store --status` should show `"enabled": true`, a `row` with a small `age_h`, and
   `"load_uses"` either value (both hold the same session).
5. The scheduled tasks pick up the user variable the next time they start (they run in new processes).
6. Dev: the same user variable is set on dev; `--status` there should show the row and no file.

Note: the shared database is the NAS Postgres. On 11-20 PROD lost its LAN path to it at 02:17 (PROD review 11-20
item 4). While PROD can't reach it, PROD keeps working from `session.json`, and dev keeps the last row it can read.

Rollback: remove the user variable (`[Environment]::SetEnvironmentVariable("AETHER_CHAIKIN_STORE", $null, "User")`);
`session.json` is unchanged. Or revert the PR.
