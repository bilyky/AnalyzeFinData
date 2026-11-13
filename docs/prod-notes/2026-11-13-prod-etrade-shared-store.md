# PROD + dev: one shared E*TRADE login (2026-11-13)

**Why:** PROD and the dev machine use the same E*TRADE consumer key, and each one's login killed the other's token.
On 11-11/12: dev 08:02 → PROD rejected 08:12; dev 09:38 → PROD 09:45; PROD 09:45 → dev 10:06; dev 10:15 → PROD 10:45,
where the stop monitor fell back to Google prices. With the shared store both machines use one token from the
`database.url` Postgres. A machine whose token is gone loads the other's instead of logging in.

**What it stores:** the token (encrypted, with a key derived from the consumer secret, which is not in the database), the
re-auth circuit-breaker counters, and the renew/re-auth locks. The browser cookies stay on each machine.
**If the database can't be reached,** a machine uses its own files as today, may log in, and logs
`E*TRADE shared store unavailable (...)` once per operation. After a failure the process skips the database for 5 minutes,
so an outage costs one ~5 s connect timeout, not 5 s per call. Lock leases use the database's clock, so the machines'
clocks don't need to agree.
**Without `AETHER_ETRADE_STORE=db`,** nothing changes.

## Steps (PROD first, then dev)
1. **Pull, then install the new dependency** (`cryptography==45.0.4`):
   ```powershell
   venv_new\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **Check that the shared database is reachable and usable** (creates the three `etrade_*` tables on first use; prints no
   secrets):
   ```powershell
   venv_new\Scripts\python.exe -c "from aether.config import CFG; from aether.etrade import store_db; s=store_db.make_db_store(CFG.database_url, CFG.etrade_production_secret); s.reauth.load(); s.tokens.load_any_date('production'); print('shared E*TRADE store OK')"
   ```
   It must print `shared E*TRADE store OK`. A `StoreUnavailable` error means the database can't be reached from this
   machine. Fix that first: on dev, Tailscale must be up.
3. **Opt in for this user,** so scheduled tasks and the dashboard inherit it:
   ```powershell
   [Environment]::SetEnvironmentVariable("AETHER_ETRADE_STORE", "db", "User")
   ```
   Then restart the dashboard (`venv_new\Scripts\python.exe server.py restart`). Tasks pick the variable up on their next
   start. Check: `venv_new\Scripts\python.exe -c "import aether.etrade as e; print(e.make_etrade_store().backend)"` in a
   **new** PowerShell window prints `db`.
4. **On PROD, after the next E*TRADE use,** the log shows `token saved to the shared store` or `Shared E*TRADE token found`.
5. **Dev:** do steps 1–3 on the dev machine, with Tailscale up. Until dev has done step 3, don't use E*TRADE on dev.

## After both machines are on it
- No more `E*TRADE rejected the cached token (401/403) even after a renew` pairs that follow a login on the other machine.
- Any `E*TRADE shared store unavailable` line means that machine ran on its own files for that operation. If it logged
  in then, the other machine's token may die once, as before.

## Rollback
```powershell
[Environment]::SetEnvironmentVariable("AETHER_ETRADE_STORE", $null, "User")
```
Restart the dashboard. Each machine uses its own `Data\etrade_tokens.json` again (it was kept up to date the whole time).
The tables can stay.
