"""Check that TLS certificate verification works from this machine to the hosts AETHER calls.

Run it before deploying the change that turned verification back on (PROD note
2026-11-11-prod-tls-verify.md). Any HTTP status, even 401 or 404, means the certificate check
passed. An SSLError means something between this machine and the host intercepts TLS: don't
deploy until REQUESTS_CA_BUNDLE points at that proxy's CA. Read-only: one GET per host, no
credentials. Exit 0 when every host verified, 1 otherwise. It trusts the same roots as AETHER
at runtime (importing aether turns on the OS trust store when `truststore` is installed).
"""
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
try:                                   # in the repo: the runtime's own trust setup (aether/tls.py)
    from aether.tls import trust_source  # noqa: E402
except ImportError:                    # copied out of the repo (PROD note step 1): same rule below
    trust_source = None
try:
    import truststore  # noqa: E402
except ImportError:
    truststore = None

if trust_source is None:
    _SOURCE = "certifi"
    if truststore is not None and os.environ.get("AETHER_TLS_TRUSTSTORE", "1").strip() != "0":
        truststore.inject_into_ssl()
        _SOURCE = "system trust store (truststore)"

    def trust_source() -> str:
        return _SOURCE

HOSTS = (
    "https://api.etrade.com/v1/market/clock.json",            # E*TRADE (token check, renew, clock, quote)
    "https://members-backend.chaikinanalytics.com",           # Chaikin (powergauge's shared session)
    "https://data.sec.gov",                                   # EDGAR (AI-buildout watchlist)
)


def _out(line: str) -> None:
    sys.stdout.write(line + "\n")


def check(urls=HOSTS) -> int:
    """0 when every host verified, 1 when any certificate check failed, 2 when a host didn't answer
    (its certificate wasn't checked, so that isn't a pass)."""
    failed = unanswered = 0
    _out(f"Trusting: {trust_source()}")
    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            _out(f"OK       {r.status_code}  {url}")
        except requests.exceptions.SSLError as e:
            failed += 1
            _out(f"SSL FAIL      {url}: {e.__class__.__name__}: {str(e)[:160]}")
        except requests.exceptions.RequestException as e:
            unanswered += 1
            _out(f"NO ANSWER     {url}: {e.__class__.__name__} (certificate not checked)")
    if failed:
        _out(f"{failed} host(s) failed the certificate check: don't deploy; set REQUESTS_CA_BUNDLE first.")
        return 1
    if unanswered:
        _out(f"{unanswered} host(s) didn't answer, so their certificates weren't checked: fix the network "
             "or proxy settings and run this again.")
        return 2
    _out("All certificate checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(check())
