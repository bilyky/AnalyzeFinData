# 🔬 Project AETHER: Unsparing Operational Failure Retrospective
**Date of Audit:** Monday, September 14, 2026

## 🚨 1. The Core Diagnostic Paradox (The Nighttime Mirage)
For months, we have faced a frustrating cycle:
1.  **The Nighttime Mirage:** We run manual diagnostics before bed, and everything passes **100% green**.
2.  **The Morning Collapse:** A few hours later, the 5:30 AM pipeline fails, or the 7:00 AM trading desk misses.
3.  **The Reactive Band-Aid:** We patch a localized symptom (a file lock, an enum, a typo), catch up, and declare victory, only to fail the next morning.

This is a static illusion. Nighttime pre-flight passes because the tokens are fresh from our manual interactions, but the system **silently decays** during the night.

---

## 🛠️ 2. The True, Unmocked Forensic Discoveries

An audit of the raw system outputs and files on disk reveals two devastating operational blind spots:

### A. The "Interactive Only" Logon Constraint (The OS Suspension)
In our live `schtasks` query of the watchdog and scheduler tasks, Windows reported:
`Logon Mode: Interactive only`
*   **The Mechanism:** Because tasks are registered without explicitly storing credentials or utilizing a persistent Service context, Windows Task Scheduler is **strictly forbidden** from running them unless your user session is actively logged on and unlocked.
*   **The Collapse:** The moment you lock your computer, log off, or the system enters a standard sleep state for the night, the Task Scheduler **completely suspends and blocks the hourly `AETHER_Watchdog` from executing**.
*   **The Consequence:** With the watchdog paralyzed overnight, there are zero active keep-alive requests submitted to E*TRADE for over 6 hours, allowing the token to naturally expire on the server due to inactivity.

### B. The Manual Token Handshake Blind Spot
When we run our manual re-authentication script at night, we type our credentials, complete the SMS, and manually enter the 5-character verifier code.
*   **The Gap:** Because this is an interactive prompt, the script never executes or saves the automated, headless browser profile backup (`Data/etrade_browser_state.json`).
*   **The Consequence:** The next morning at 5:30 AM, when the pipeline wakes up and finds the token expired, it checks `os.path.exists(_BROWSER_STATE_PATH)`. Since the state file was never created, the headless Playwright re-authenticator **defensively bypasses re-login** and returns `None` to prevent background hangs. Pre-flight sees the offline gateway and aborts the entire pipeline.

---

## 🧠 3. Our Flaws in Engineering Discipline
*   **Reactive Band-Aids vs. Structural Hardening:** We have fixated on trailing syntax errors and local code lines instead of auditing the OS-level scheduling environment and token lifecycle properties.
*   **The Mock-Testing Illusion:** We write unit tests that cleanly mock out the file system and network. They pass 100% green, giving us a false sense of security, but they are completely blind to OS execution locks, timezone rollovers, and Akamai browser-fingerprinting.

---

## 🧭 4. The Path to Permanent, Unsparing Hardening

To transform the AETHER desk into an indestructible, autonomous quantitative machine, we must immediately deploy three structural, zero-trust changes:

### Step 1: Force Persistent Non-Interactive Execution (S4U)
We must re-register the task schedulers to run **whether the user is logged on or not** (using S4U or persistent Service credentials). This ensures the hourly watchdog is never suspended overnight, keeping our tokens warm 24/7.

### Step 2: Establish Headless Browser State Portability
We must execute a supervised, headed-to-headless Playwright synchronization to ensure `Data/etrade_browser_state.json` is successfully written to disk. This gives the background headless worker its trusted-device state, enabling it to auto-re-authenticate 100% autonomously during overnight broker resets.

### Step 3: Implement Off-Market Skip Gates
We must separate active trading hours from night hours. During the night (1:30 PM PST to 5:00 AM PST), the watchdog must strictly run lightweight session-renewers and exit instantly, avoiding all slow, heavy diagnostics to prevent task timeouts.
