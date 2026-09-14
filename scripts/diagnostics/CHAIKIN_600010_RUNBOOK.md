# Chaikin / PowerGauge re-auth on a new PC — config runbook (error 600010)

Fixing "PG token refresh failing on another PC with **600010**".

**What 600010 is:** a Cloudflare **Turnstile** challenge failure ("generic
challenge failure / suspected bot"). It is thrown when Chaikin's login page does
not trust the browser — typically a **headless** browser, or a **cold / poisoned
persistent Chrome profile** (stale or flagged `cf_clearance` / `aws-waf` cookie).
It is *not* an expired-token problem; refreshing the JWT will not clear it.

**Why the code change matters (context for this runbook):** the login self-heal
now only backs-up-and-clears the persistent profile on a **headed** failure. A
headless failure keeps the profile intact, because headless can never re-solve a
cold Turnstile and wiping the durable `cf_clearance` cookie would make 600010
*permanent*. So a healthy profile + a headed re-auth path is the whole fix.

---

## Configuration required on the failing PC

### 1. `config.json` MUST carry `chaikin.api_key`  ← most likely root cause of a hard 403, do this first
On this branch (and `main`) `_CHAIKIN_API_KEY` has **no hardcoded default**
(`powergauge.py`: `CFG.chaikin_api_key or os.environ.get("CHAIKIN_API_KEY") or ""`).
If it is empty, every `/api/*` call returns **`403 {"code":"SESSION_EXPIRED",
"message":"Missing required headers"}`** — misleading; it's the missing
`x-api-key`, not an expired session.

The `chaikin` block on the failing PC currently has only `email` + `password`
(same as this PC). Add the key:

```jsonc
// config.json  (gitignored — NEVER commit; it holds the plaintext password)
"chaikin": {
  "email":    "...",
  "password": "...",
  "api_key":  "<64-char OMNI x-api-key>"
}
```
…or set the environment variable `CHAIKIN_API_KEY` for the account that runs the
scheduled tasks. The value is the same OMNI `x-api-key` this PC already uses
(recoverable from git history `2462be4:powergauge.py`, the pre-removal literal).

> `config.json` is **gitignored** (verified) and does not travel between PCs or
> into worktrees — it must be created/edited directly on the failing PC.

### 2. A warm persistent Chrome profile with a valid `cf_clearance`
`_CHAIKIN_PROFILE_DIR = Data/chaikin_chrome_profile` is the durable credential
(the `cf_clearance` cookie lasts ~355 days and auto-passes Turnstile in a headed
real browser). A brand-new PC has **no** profile, so the first login must be
**headed** so a human/desktop can complete Turnstile once:

```
python powergauge.py            # interactive/TTY → headed by default; solve Turnstile once
```
- After that first success the profile is warm and subsequent re-auths are
  hands-free.
- **If 600010 persists** on that PC, the local profile is poisoned: delete
  `Data/chaikin_chrome_profile` (or restore a good one from
  `Data/Backup/chaikin_profile_backup_*`, created automatically on a headed
  self-heal) and log in **headed** once more to mint a fresh `cf_clearance`.
- Do **not** copy a profile from another PC and expect it to pass — `cf_clearance`
  is fingerprint/host-bound and may itself trigger 600010.

### 3. Run the re-auth Scheduled Task HEADED, in an interactive desktop session
`scripts/utils/register_agent_tasks.ps1` registers **`AETHER_Chaikin_Reauth`**
(Weekly, **Sunday 8:00 AM**, `venv_new\Scripts\python.exe
scripts/monitoring/chaikin_reauth.py`) with an **Interactive** principal
(`-LogonType Interactive -RunLevel Limited`) so headed Chrome has a desktop for
Turnstile. On the failing PC:
```
powershell -ExecutionPolicy Bypass -File scripts/utils/register_agent_tasks.ps1
```
- The task **only runs while that user is logged on** (Interactive principal).
  If the PC is used over RDP or sits at the lock screen with no session, the task
  will not have a desktop and headed Chrome cannot render Turnstile.
- Do **not** switch this task to "Run whether user is logged on or not" / a
  Service or S4U principal — that forces a Session-0 (headless-equivalent)
  context and reintroduces 600010.
- The automated ranking fallback (`login(interactive=False)`) is intentionally
  **headless** so it *fast-fails* in ~10s instead of hanging on Turnstile; the
  weekly Interactive task is what actually re-mints the token. Don't set
  `CHAIKIN_HEADLESS_LOGIN=1` globally on this PC — it would force the interactive
  re-auth headless and guarantee 600010.

### 4. Playwright + the Chrome channel must be installed
The login uses `launch_persistent_context(..., channel="chrome")`. On the new PC:
```
python -m playwright install chrome      # or ensure Google Chrome (stable) is installed
```
If Playwright or the `chrome` channel is missing, `_login_via_browser` raises
`ImportError`/launch errors (a different failure than 600010, but blocks re-auth).

### 5. Proxy (Intel network only)
If the PC is on the Intel network, egress to Chaikin/Cloudflare goes through
`http://proxy-dmz.intel.com:911` (auto-resolved by `_resolve_proxy()` and passed
to both Playwright and the `/api/*` calls). On a home/non-Intel network no proxy
is needed and it is skipped automatically — no config change required.

---

## Quick verification after configuring
```
python scripts/monitoring/chaikin_reauth.py --check    # prints token runway, no browser
python -c "import powergauge as pg; print(pg._probe_session(pg._load_session_from_file()))"
# → 'valid'   (401/403 = bad/missing api_key or expired session; 'unreachable' = proxy/network)
```

## One-line triage
| Symptom | Most likely cause | Fix |
|---|---|---|
| `403 Missing required headers` | `chaikin.api_key` unset | §1 |
| Turnstile **600010**, submit stays disabled | cold/poisoned profile, or headless | §2 (headed re-login) / §3 |
| Login hangs ~60s then fails | headed path with no warm profile | §2 first-time headed login |
| `ImportError: Playwright not installed` | missing Playwright/Chrome | §4 |
| `_probe_session` → `unreachable` | proxy/network | §5 |
