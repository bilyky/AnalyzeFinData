# E*TRADE Authentication — Restrictions & Correct Procedure (Anti-Ban)

> **Purpose.** E*TRADE fronts its login with **Akamai Bot Manager**. Replaying a stale
> saved browser session from an automation-flagged Chrome, in a tight retry loop, is what
> gets our **IP banned**. This document is the single source of truth for how AETHER must
> authenticate and renew E*TRADE OAuth tokens so that never happens again. Treat every rule
> here as load-bearing.

---

## 1. What actually broke (2026-08-18 incident)

- A production `server.py` (started **Aug 13**) was running **stale in-memory code from before
  the 15-min cooldown was committed (Aug 14)**, so it had *no throttle at all*.
- Over the weekend the OAuth token expired (see §2). On Monday the process tried to renew,
  failed (HTTP 401), and fell through to an **automated headless browser re-auth** that
  replayed an **Aug-14 saved session with expired Akamai cookies**.
- Akamai answered with a **silent spinner-hang** (a soft bot-block), not the login page.
  The process retried this **4 times in 8 minutes** (02:29–02:37), driving straight toward
  an IP ban.
- Root cause was **not** a selector/credential bug — the login form filled fine. The failure
  is server-side bot detection, and the danger is **repeated automated attempts**.

**Fix shipped:** an escalating circuit breaker at the single automated choke point, a
renew-only keep-alive for schedulers, and the hard rules below.

---

## 2. E*TRADE OAuth semantics (the non-negotiable facts)

| Fact | Consequence |
|------|-------------|
| Access tokens are valid **until midnight US-Eastern**. | A token **cannot** survive into the next calendar day. Every morning needs a fresh token. |
| A token goes **inactive after ~2 h of no use**; `renew_access_token` **reactivates** it. | Intraday you must renew at least every 2 h to keep a session warm — but renewal **cannot** revive a token that has already crossed midnight. |
| Renewal is **pure HTTP** (no browser). | Keeping a live session warm is 100 % ban-safe. Creating a *new* session requires a browser + MFA. |
| A **weekend** always crosses midnight ET (Fri→Mon). | Monday **always** needs a brand-new browser login. There is no way to "keep alive" across a weekend. This is expected, not a bug. |

**Corollary:** the browser login is a **human, low-frequency, clean-context** event. The
machine's only job is to keep a *human-created* session warm during the day.

---

## 3. The hard restrictions (rules)

1. **Scheduled code logs in only through two gated paths, and never opens an interactive browser.**
   It always tries a renew first and opens at most one headless browser:
   - **`etrade.scheduled_reauth(env)`**. Used by the daily `AnalyzeFinData_ETrade_Reauth` task (05:15,
     runs `server.py etrade-reauth --scheduled`, set up by `scripts/etrade_totp_wizard.py`), the hourly
     watchdog, the nightly preflight (ET weekdays only), `goal_sentry`, `scripts/etrade_reauth.py`, and
     the admin `POST /api/etrade/scheduled-reauth` endpoint. That endpoint is not the manual
     `POST /api/etrade/reauth` in §4.1. It calls `keep_alive` first, and only logs in if the profile is
     trusted (or a TOTP secret is set), the breaker is clear, and it gets the single-flight lock. On ET weekends the watchdog, the
     05:15 task and preflight only renew; they don't log in (`reason=weekend`). The web "+" button,
     `scripts/etrade_reauth.py` and `goal_sentry` can still log in on a weekend. The
     login is headless unless `AETHER_ETRADE_SCHEDULED_HEADLESS` / `CFG.etrade_scheduled_headless` says
     otherwise.
   - **The automated step inside `get_tokens(env, allow_browser=False)`**. A caller that finds the
     token dead can log in through the same breaker and lock, if saved browser state or a TOTP secret
     exists. It uses the caller's `headless` flag, which defaults to `False`, so scheduled code should
     use `scheduled_reauth` instead.

   `keep_alive(env)` only renews and never opens a browser. With no token from today it returns `None`
   without calling E*TRADE. If E*TRADE rejects today's token, it tries one renew (a token idle for 2
   hours stays inactive until renewed, see §2) and otherwise returns `None`. Never call
   `get_tokens(..., allow_browser=True)` from scheduled code.
2. **Only a person at a terminal uses the interactive login.** Run **`aether etrade-login`** (add
   `--bootstrap` for the one-time supervised OTP that re-seeds device trust). It calls
   `etrade.reauthenticate()` (`get_tokens(..., allow_browser=True)`), see §4.1. The older
   `python scripts/diagnostics/test_etrade.py production` does the same thing.
3. **The automated browser path is circuit-broken.** `_login_headless` is the *single choke point*
   for any automated browser re-auth and it enforces an **escalating cooldown** that doubles on each
   consecutive failure — 15 → 30 → 60 → 120 → 240 → 480 → 960 min — pinned at a hard **1440 min (24 h)**
   ceiling from the 8th failure on. After **3 failures in a row** (`CFG.etrade_reauth_alert_threshold`)
   automated login is blocked (`reason=blocked`) wherever the cooldown is, and a person is alerted.
   A sustained streak means the saved session is dead or the IP is being watched, so only a
   human re-auth should revive the automated path. The breaker clears **only on a successful login**. It
   cannot be bypassed by a retry loop or a second process.
4. **Never hammer after a failure.** One failed automated re-auth = back off. Repeated failures = the
   IP is likely being watched; stop and re-auth manually from a **clean context**.
5. **If you suspect a ban, stop all automated E*TRADE contact and let the IP cool** (hours). Then do a
   single manual re-auth from a **different network/IP** if possible.
6. **`get_tokens()` fails soft** (`-> dict | None`) on the automated path. Callers that use the return
   value **must guard it** (`if not tokens: ...`) — do not assume a dict.
7. **Never commit secrets or the saved session.** `config.json`, `Data/etrade_tokens.json`,
   `Data/etrade_browser_state.json`, and the `Data/etrade_chrome_profile/` profile jar contain
   credentials / cookies / PII and are gitignored (all of `Data/` is). Keep it that way.

---

## 4. How the code enforces this

| Piece | File | Role |
|-------|------|------|
| `keep_alive(env)` | `aether/etrade/__init__.py` | **Renew-only** session keeper for scheduled code. Never opens a browser. No E*TRADE call when there's no token from today; one renew if E*TRADE rejects today's token (#144). |
| Circuit breaker | `aether/etrade/__init__.py` (`_reauth_cooldown_remaining` / `_record_reauth_attempt` / `reset_reauth_circuit_breaker`) | Escalating anti-ban throttle, enforced inside `_login_headless`. State in `Data/etrade_reauth_state.json`. |
| `_login_headless` | `aether/etrade/__init__.py` | The **only** automated browser path. Self-gates on the breaker, then **pessimistically arms** it (`_record_reauth_attempt`) *before* opening the browser and retracts (`reset_reauth_circuit_breaker`) only on a confirmed success — so an attempt killed/hung mid-browser still leaves the breaker engaged instead of silently at 0. |
| `_get_tokens_via_playwright` | `aether/etrade/__init__.py` | Drives a **persistent real-Chrome profile** (`_CHROME_PROFILE_DIR`), not a throwaway browser seeded with a static cookie snapshot — see §4.1. |
| `reauthenticate(env, bootstrap, headless)` | `aether/etrade/__init__.py` | The single **human-initiated** re-auth core behind all three front doors (§4.1). Fails soft to a JSON result; a minted token that can't fetch a live quote is a FAILURE. |
| `get_tokens(env, allow_browser=False)` | `aether/etrade/__init__.py` | `allow_browser` gates the **human interactive** login only. Automated headless re-auth still runs (breaker-guarded) then fails soft to `None`. |
| Watchdog keeper | `watchdog.py` §0 | Uses `keep_alive` + emails a human on a dead token. Never launches Playwright. |
| Manual re-auth | `aether etrade-login` / `POST /api/etrade/reauth` / web button | The sanctioned human login (§4.1). On success it **resets the breaker**. |

**State files (all under `Data/`, gitignored).** The `Data/` location is resolved once through
`aether.paths.data_dir()` — `$AETHER_DATA_DIR` if set, else `<checkout>/Data` — shared by
`aether.etrade` and `aether.trash` so a re-auth launched from any checkout (or git worktree)
writes the token, its trash, and the breaker to the **same** dir prod reads. Set `AETHER_DATA_DIR`
to an absolute path in prod to make that pinning explicit (the 2026-08-19 "couldn't locate the
token file" incident was a worktree writing to its own dead `Data/`):
- `etrade_tokens.json` — the OAuth token. On a 401/403 (or an explicit revoke) it is **soft-deleted**:
  moved to `Data/.trash/` (never `os.remove`d in the hot path), so a token wrongly invalidated by a
  transient/edge rejection stays recoverable. See the garbage-can note below.
- `etrade_browser_state.json` — a Playwright cookie **snapshot**, now only a secondary backup
  (the persistent profile below is primary; see §4.1).
- `etrade_chrome_profile/` — the **persistent real-Chrome profile jar** (§4.1). Holds Akamai's
  rolling sensor cookies and E*TRADE's device-trust across runs. A directory, not a file.
- `.trash/` — the auth-state **garbage can**. Files land here as `<UTC-ish stamp>.<reason>.<name>`
  (`reason` = `rejected-401` / `revoked`) and are physically deleted only by `trash.purge_trash()`
  (default retention **30 days ≈ 1 month**), which the watchdog runs each cycle. Recovery = copy the
  file back out. The single place auth-state files are truly removed.
- `etrade_reauth_state.json` — **production** circuit-breaker state (`consecutive_failures`,
  `cooldown_until`). The breaker state is **per-environment**: any non-production env writes a
  separate `etrade_reauth_state_<env>.json` (e.g. `_sandbox`), so a sandbox or test re-auth
  failure can never cool down the production gate. Tests redirect this path to a tempfile, so a
  `python -m unittest discover tests` run never reads or writes the real files.
- `etrade_reauth.lock` / `etrade_renew.lock` — cross-process mutexes (auto-cleaned).

**Testing is separated from production** at three layers: (1) the breaker state is per-env (above);
(2) `tests/__init__.py` installs a hermeticity guard that blocks all non-loopback sockets and
Playwright browser launches for the whole suite — a test that forgets to mock a network boundary
fails loudly instead of contacting a live broker; (3) the same file redirects every module's
`XLSX_FILE` constant to a throwaway temp workbook, so no test can **write** the production
`Data/state_of_the_day.xlsx` (existence checks and reads still succeed; every `wb.save()` lands in
temp). Layers (1)–(2) opt back in under `AETHER_LIVE_TESTS=1`; the workbook guard (3) is
**unconditional** — the live tier still must never mutate prod trading state. `tests/test_prod_workbook_isolation.py`
red-green-guards it: remove the redirect and it fails.

### 4.1 The persistent-profile re-auth engine (the "one magic button")

The old automated path replayed a **static cookie snapshot** (`etrade_browser_state.json`) into a
throwaway browser. Akamai's rolling `_abck`/`bm_sz` sensors invalidate a frozen snapshot the moment
JS revalidation is due, so the replay drew the spinner-hang — the §1 failure. The engine now drives a
**persistent real-Chrome profile** (`Data/etrade_chrome_profile/`) via
`launch_persistent_context`: the profile dir **is** the state, so the rolling cookies and E*TRADE's
device-trust live in a real jar and persist across runs.

**One core, three human front doors** (all call `etrade.reauthenticate()`, so there is exactly one
execution path):

| Front door | How |
|-----------|-----|
| CLI | `aether etrade-login` (daily) · `aether etrade-login --bootstrap` (one-time OTP) |
| HTTP | `POST /api/etrade/reauth` (admin-only; subprocesses the CLI into `Data/task_runs/`, polled via `GET /api/tasks/output/{run_id}`) |
| Web | 🔐 **Re-authenticate E*TRADE** button in the dashboard Actions card |

`reauthenticate()` returns a JSON-serializable result (`ok`, `has_token`, `quote_ok`, `issued_date_et`,
`breaker_state`, `message`); a token that mints but **cannot fetch a live AAPL quote is a FAILURE**
(`ok=False`). `--bootstrap` forces a **headed** browser for the rare one-time SMS OTP that re-seeds
device trust (check "remember this device"); the daily path expects no OTP.

**None of this relaxes the §3 rules.** A person starts each of these three doors. Scheduled code uses
`scheduled_reauth()` instead (§3 rule 1), which goes through the same `_login_headless` step and
breaker.

> **Status (2026-10-02):** automated login is wired in and tested offline. Whether it keeps working on
> the PROD machine day after day only shows in PROD's own runs: check the preflight and watchdog logs
> for the `reason`. `renewed` or `reauthed` is fine; `sms_required` or `blocked` needs a person.

---

## 5. Correct procedures

### Daily (normal weekday)
1. The token expires at midnight ET. A scheduled run logs in for the new day with nobody present: the
   nightly preflight at 21:30 PT (00:30 ET), the 05:15 `AnalyzeFinData_ETrade_Reauth` task, or the
   hourly watchdog. Whichever runs first logs in; the others just renew.
2. During the day, `keep_alive` renews the token, including after a 2-hour idle gap. No browser.
3. A person only acts on an alert. For `sms_required` or `unseeded`, run `aether etrade-login
   --bootstrap`. For `blocked`, fix the cause, then run `aether etrade-login` to clear the breaker.

### Weekends
- On ET Saturdays and Sundays nothing scheduled logs in. Preflight only renews and reports
  `WAIVED`. The watchdog and the 05:15 task call `scheduled_reauth(..., weekend_mint=False)`, which
  only renews and returns `reason=weekend`. The 05:15 task exits 0 in that case, so it doesn't show
  as failed.
- Monday's token comes from the first run after midnight ET on Monday: Sunday's 21:30 PT preflight,
  or the watchdog.
- You can still log in by hand on a weekend (`aether etrade-login` or the web re-auth button). If
  Monday starts with an alert, do step 3 above.

### If automated re-auth failed / a ban is suspected
1. **Stop all automated E*TRADE contact** (scheduled tasks, `server.py`). Nothing should be hitting
   the login endpoint.
2. **Let the IP cool down** (hours). Repeated attempts reset the clock.
3. Re-authenticate manually, ideally **from a different network/IP** (the user can "connect from
   another system"): `python scripts/diagnostics/test_etrade.py production`.
4. A successful login **auto-clears the circuit breaker**. To force a retry sooner, call
   `etrade.reset_reauth_circuit_breaker()` or delete `Data/etrade_reauth_state.json`.

### Refreshing the browser session (when MFA starts reappearing)
- The trusted-device cookies now live in the persistent Chrome profile `Data/etrade_chrome_profile/`
  (§4.1), which a real browser keeps revalidating in place — not in a frozen `etrade_browser_state.json`
  snapshot (kept only as a secondary backup). When device trust lapses and E*TRADE forces an SMS OTP,
  re-seed it with one headed bootstrap: `aether etrade-login --bootstrap`, enter the OTP, and check
  "remember this device." The daily `aether etrade-login` then rides that profile with no OTP.
- Never replay a stale static snapshot automatically — that is the original Akamai trigger (§1) the
  persistent profile replaces.

### Validating the auth stack (tests)

The auth state machine is covered at two tiers. **Neither the default suite nor the live tier ever
opens a browser** — the ban-sensitive new-session step is only ever a human at a TTY.

**Tier 1 — hermetic scenarios (`tests/test_etrade_auth_scenarios.py`, always run).** Drives the real
`get_tokens` / `keep_alive` code through the true I/O seams (`_probe_token_auth`, `renew_tokens`, the
Playwright choke point) with token files in a temp dir. Covers the lifecycle the breaker tests don't:
same-day token reuse, yesterday-token-renewed-across-midnight, **dead token → soft `None`, no browser**,
weekend keep-alive, explicit-401-invalidates-cache, **transient-blip-keeps-a-good-token**, the Akamai
spinner-hang (Playwright *raising* → breaker engages), and a cooling breaker suppressing the
`get_tokens` browser attempt. Every scenario asserts the ban-safety invariants:
1. the automated path never launches a browser;
2. automated `get_tokens`/`keep_alive` fail **soft** to `None`, never raise;
3. a transient/network probe blip never destroys an otherwise-good token (only an explicit 401/403 does);
4. the breaker clears only on success; per-env state keeps sandbox/test failures off the prod gate;
5. no test reads or writes the production auth-state files.

**Tier 2 — live, opt-in, ban-safe HTTP only (`tests/test_etrade_live.py`, gated by `AETHER_LIVE_TESTS=1`).**
Skips by default. When enabled it contacts the **real** broker over pure HTTP only — `_probe_token_auth`
(a GET on the public market clock) and `keep_alive` (the renew endpoint). Both are 100% ban-safe. A
missing/expired session **skips** (that is the correct contract), never fails. It asserts no browser is
launched. Run it:
```
AETHER_LIVE_TESTS=1 python -m unittest tests.test_etrade_live -v
```
`AETHER_LIVE_TESTS=1` is a single switch: it disables the `tests/__init__.py` hermeticity guard so the
live tier sees the real token files **and** a real network; a normal run sees neither.

### Different-IP interactive validation (the only ban-sensitive step, done safely)

Creating a **new** session is the one path that can trip Akamai. Validate it from a **clean context on a
different IP** so that even if bot-detection fires, the primary IP is untouched and recovery is clean:
1. From the other machine/network, run the sanctioned human login: `python scripts/diagnostics/test_etrade.py production`.
   Complete MFA if prompted. On success this writes today's token and **resets the breaker**.
2. Then confirm the ban-safe machine paths work against that fresh session:
   `AETHER_LIVE_TESTS=1 python -m unittest tests.test_etrade_live -v` (renew + probe, no browser).
3. Do **not** re-run automated jobs against the primary IP until a clean same-day token exists and the
   breaker is clear. A different IP gives a clean *recovery* path — it does **not** make an automated
   browser re-auth loop safe. That loop is never allowed, on any IP.

---

## 6. Do / Don't (quick reference)

**Do**
- Use `scheduled_reauth()` to keep the session alive from scheduled code. Use `keep_alive()` where a
  job must never log in.
- Leave the interactive login to a person (`aether etrade-login`, `--bootstrap` for OTP).
- Back off on failure; the breaker does this for you — don't defeat it.
- Guard `get_tokens()` return values (`if not tokens: fall back`).

**Don't**
- Don't call `get_tokens(..., allow_browser=True)` from any scheduled/headless job.
- Don't loop automated browser re-auth. Don't shorten or remove the cooldown.
- Don't replay a stale `etrade_browser_state.json` repeatedly.
- Don't run long-lived processes across a deploy without restarting them (stale in-memory code was
  the 2026-08-18 root cause).
- Don't commit `config.json`, tokens, or browser state.
