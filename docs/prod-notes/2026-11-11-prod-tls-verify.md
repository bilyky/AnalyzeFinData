# TLS certificate checks turned back on: what you need to do

Several calls ran with TLS certificate verification off (`verify=False`), and `powergauge.py`
hid the warning for the whole process. Anyone able to intercept those connections could read or
change them. They carried credentials:

- E*TRADE: the token check, **renew**, **revoke**, and the market clock / quote checks;
- Chaikin: every call through the shared HTTP session (the session token in its headers);
- EDGAR (AI-buildout watchlist): it fell back to an unverified request on a certificate error.

All of them now verify certificates. EDGAR no longer falls back: on a certificate error it logs a
warning and uses the cached data, labelled stale, as it does for any other fetch failure.

## 1. Deploy

Merge, pull, restart the dashboard (`venv_new\Scripts\python.exe server.py restart`). Scheduled
jobs pick it up on their next run.

## 2. Check

- The next morning run fetches Chaikin and E*TRADE data as usual (no `SSLError` in the log).
- The log no longer shows `InsecureRequestWarning`.

On dev (through the Intel proxy), all of these calls were run once with verification on
(2026-11-11): E*TRADE answered every call (the token was fake, so 401s, as expected), Chaikin and
EDGAR answered 200. PROD connects directly, without a proxy, which is the normal case for
certificate checks.

## If a call fails with SSLError

That means something between the machine and the host is intercepting TLS (for example a
proxy that inspects traffic). Don't turn verification off again. Point `REQUESTS_CA_BUNDLE` at
the proxy's CA certificate instead, or use a network path that doesn't intercept.

## Roll back

Revert the PR and restart.
