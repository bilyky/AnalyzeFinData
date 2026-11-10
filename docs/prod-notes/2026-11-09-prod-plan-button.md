# OceanView plan-check button + login on task output: what you need to do

Two changes:
- **New button** on the System page, Manual Tasks, group "OceanView": **OceanView — Plan Check**.
  It runs `scripts/monitoring/invest_plan.py` (recommend-only, writes nothing). Admin-only.
- **Login on task output.** `GET /api/tasks/output/{run_id}` used to answer anyone who had the
  run id, so an admin-only task's log could be read without logging in. Admin-only tasks (and
  the E*TRADE re-auth button's run) now need the login to read their output. Tasks open to
  everyone are unchanged.

## 1. Deploy

Merge, pull, restart the dashboard (the route change is read at start):

```powershell
venv_new\Scripts\python.exe server.py restart
```

The button needs `Data\position_watch\invest_plan.json` on PROD (the #246 note, step 2).
Without it the task ends with an error that the file is missing.

## 2. Check

- Logged in: System page → Manual Tasks → OceanView → **Plan Check**. The output panel shows
  the plan report and "done (exit 0)".
- Logged out: the button is greyed out ("Admin login required").
- Any other admin task (e.g. Chaikin — Check Token Runway) still shows its output while you
  are logged in. If the login expires during a run, the panel says "log in to see the output"
  instead of waiting forever.

## Roll back

Revert the PR and restart the dashboard.
