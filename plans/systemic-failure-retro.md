# Project AETHER: Operational Failure Retrospective
**Date of Audit:** Monday, September 14, 2026

> Scope: this is a **retrospective (diagnosis only)**. Remediation that gets adopted is
> tracked as numbered items in [`plans/roadmap.md`](./roadmap.md) — see the cross-references in
> §4 so the two files don't drift.

## 1. The Core Diagnostic Paradox (The Nighttime Mirage)
For months we faced a recurring cycle:
1.  **The nighttime mirage:** we run manual diagnostics before bed and everything passes green.
2.  **The morning collapse:** a few hours later the 5:30 AM pipeline fails, or the 7:00 AM trading desk misses.
3.  **The reactive band-aid:** we patch a localized symptom (a file lock, an enum, a typo), catch up, and declare victory — only to fail the next morning.

The green nighttime pre-flight is a static illusion: the tokens are fresh from our manual
interactions, but the system **silently decays** overnight.

---

## 2. The Unmocked Forensic Discoveries

An audit of the raw system outputs and files on disk revealed two operational blind spots.

### A. The "Interactive Only" Logon Constraint (verified — the real overnight killer)
In a live `schtasks` query of the watchdog and scheduler tasks, Windows reported:
`Logon Mode: Interactive only`
*   **The mechanism:** because the tasks are registered without stored credentials or a persistent
    Service context, Windows Task Scheduler will not run them unless the user session is actively
    logged on and unlocked.
*   **The collapse:** the moment the computer is locked, logged off, or asleep for the night, the
    scheduler suspends the hourly `AETHER_Watchdog`.
*   **The consequence:** with the watchdog paralyzed overnight, no keep-alive renew requests are sent
    to E*TRADE for hours, and the same-day token lapses (E*TRADE tokens also expire at midnight ET
    regardless — see the open question in §5).

### B. The Manual Token Handshake Blind Spot (corrected)
When we run the manual re-authentication script at night — typing credentials, completing MFA, and
entering the verifier code — the interactive prompt path does not necessarily persist the browser
**profile / state** that the *automated* path later relies on.
*   **What is actually load-bearing:** the persistent browser **profile** is the *primary*
    trusted-device state that lets the software-TOTP re-auth run zero-touch
    (`aether/etrade/__init__.py:94`). `Data/etrade_browser_state.json` is only a **secondary**
    snapshot — the code calls it *"a frozen cookie dump … a harmless secondary backup (the profile
    is primary)"* (`aether/etrade/__init__.py:91,700`).
*   **The consequence:** if the primary profile never gets seeded/refreshed by a supervised run, the
    automated path has no trusted-device context to reuse the next morning. (Note: an *automated*
    `get_tokens` that can't mint fails **soft to `None`** by design and the pipeline falls back — it
    does not hang; see `ETRADE_AUTH.md`.)

> Correction vs. an earlier draft of this doc: the failure is **not** caused by a missing
> `etrade_browser_state.json` gating re-login. That JSON is the secondary backup; the primary state
> is the profile. Re-derived from `aether/etrade/`.

---

## 3. Our Flaws in Engineering Discipline
*   **Reactive band-aids vs. structural hardening:** we fixated on trailing syntax errors and local
    code lines instead of auditing the OS-level scheduling environment and token lifecycle.
*   **The mock-testing illusion:** unit tests that mock the filesystem and network pass green while
    remaining blind to OS execution locks, timezone rollovers, and Akamai browser-fingerprinting.
    Diagnosis has to reach the unmocked seams.

---

## 4. Diagnosis-Driven Remediation (status against current main)

Adopted items are tracked in [`plans/roadmap.md`](./roadmap.md); this section records their status
so the retrospective stays honest.

### Step 1 — Persistent non-interactive scheduling *(OPEN — the real fix for root-cause A)*
Re-register the schedulers to run **whether the user is logged on or not** so the hourly keep-alive
watchdog is never suspended overnight. Prior scheduler work rejected elevated/admin `schtasks`
changes over UAC friction, so the S4U vs. stored-credential trade-off must be reconciled with that
finding in `plans/roadmap.md` before adopting — do not treat this as settled.

### Step 2 — Keep new-session minting ban-safe *(CONSTRAINT, not "full autonomy")*
This must **reaffirm**, not weaken, the `ETRADE_AUTH.md` invariants:
*   Automated / scheduled contexts use **`keep_alive`** (renew-only, HTTP, **zero browser**) — never
    a browser login. HTTP renewal is ban-safe.
*   A new session is created only by a **human at a TTY**, or by the shipped **software-TOTP** path
    running **behind the escalating circuit breaker** at the single `_login_headless` choke point.
*   An unsupervised, generic Playwright browser-login loop to "bridge" overnight gaps is **never
    allowed, on any IP** (`ETRADE_AUTH.md`) — that is the exact Akamai-ban anti-pattern the breaker
    exists to prevent. The goal is a *warm token via renewal*, not autonomous re-login.

### Step 3 — Off-market skip gates *(DONE — shipped)*
Already implemented on main: the **Night-Hours Sentry Optimization (R&D #36)** shipped in PR #83
(`watchdog.py:756-785`) runs an active window of `300 <= current_minutes <= 810` (5:00 AM–1:30 PM
PST) and skips the heavy process supervisor / port sentry outside it. No further action.

---

## 5. Open Question (not yet resolved)
Whether the overnight token loss is driven primarily by **server-side inactivity** (no keep-alive
while the scheduler is suspended) or by the **midnight-ET expiry** that happens regardless. The two
call for different fixes (Step 1 keep-alive persistence vs. a pre-midnight renew), so this needs a
measured answer before over-investing in either.
