# TLS certificate checks turned back on: what you need to do

Several calls ran with TLS certificate verification off (`verify=False`), and `powergauge.py`
hid the warning for the whole process. Anyone able to intercept those connections could read or
change them. They carried credentials:

- E*TRADE: the token check, **renew**, **revoke**, and the market clock / quote checks;
- Chaikin: every call through the shared HTTP session (the session token in its headers);
- EDGAR (AI-buildout watchlist): it fell back to an unverified request on a certificate error.

All of them now verify certificates. EDGAR no longer falls back: on a certificate error it logs a
warning and uses the cached data, labelled stale, as it does for any other fetch failure.

**Which certificates are trusted:** with the `truststore` package installed (new in
`requirements.txt`), AETHER trusts the Windows certificate store, the same roots the browser
trusts, including a corporate or antivirus root that inspects HTTPS. That's the most likely reason
certificate checks would fail on a machine, so installing it first removes most of the risk.
Without the package, AETHER uses Python's certifi bundle, as before. Verification is on either way.
To go back to certifi without a code change, set `AETHER_TLS_TRUSTSTORE=0`.

## 1. Before you deploy: check certificates from PROD

If certificate checks fail on PROD, every E*TRADE, Chaikin and EDGAR call fails: no prices, no
token renew. (The token code no longer answers a certificate failure with an automated login; it
logs "TLS certificate check failed" and stops, so there is no login loop, but nothing works until
the certificates are fixed.) So check **before** pulling: after a pull, scheduled jobs run the new
code on their next run even without a restart. Fetch only, and run the check from the fetched
commit (it changes nothing in the working tree):

```powershell
venv_new\Scripts\python.exe -m pip install "truststore>=0.10"
git fetch origin
git show origin/main:scripts/diagnostics/tls_precheck.py | Out-File -Encoding utf8 $env:TEMP\tls_precheck.py
venv_new\Scripts\python.exe $env:TEMP\tls_precheck.py
```

The first line of the output says which roots it trusted (`Trusting: system trust store
(truststore)` or `Trusting: certifi`); the runtime trusts the same. If pip can't install the
package, the check still runs, against certifi.

- `All certificate checks passed.` (exit 0): go on to step 2. Any status code, even 401 or 404,
  counts as a pass.
- `SSL FAIL` on any host (exit 1): stop, don't pull, and see "If a call fails with SSLError" below.
- `NO ANSWER` (exit 2): that host wasn't reached, so its certificate wasn't checked. That isn't a
  pass. Fix the network or proxy settings in that shell and run it again.

PROD's config sets no E*TRADE proxy. Whether anything else intercepts TLS there (a proxy set in
the environment, antivirus HTTPS scanning) is what this step finds out.

## 2. Deploy

Pull, then restart the dashboard (`venv_new\Scripts\python.exe server.py restart`). Scheduled jobs pick it up
on their next run.

## 3. Check

- The next morning run fetches Chaikin and E*TRADE data as usual (no `SSLError` in the log).
- The log no longer shows `InsecureRequestWarning`.
- No `E*TRADE: today's token renewal failed` followed by a headless login in the same hour.

On dev (through the corporate proxy), all of these calls were run once with verification on
(2026-11-11): E*TRADE answered every call (the token was fake, so 401s, as expected), Chaikin and
EDGAR answered 200.

## If a call fails with SSLError

That means something between the machine and the host is intercepting TLS (for example a
proxy that inspects traffic). Don't turn verification off again. First install `truststore` (step
1) if the check said `Trusting: certifi`: the Windows store usually already holds that proxy's
root. Otherwise point `REQUESTS_CA_BUNDLE` at the proxy's CA certificate; every call honors it,
including Chaikin's direct-mode session (it ignores proxy variables, but keeps a CA bundle).

## Roll back

Revert the PR and restart.
