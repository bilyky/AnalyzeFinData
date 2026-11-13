"""TLS trust for every HTTPS call AETHER makes: verify certificates against the operating system's
trust store (Windows: the same roots the browser trusts, including a corporate or antivirus root
that inspects HTTPS) through the optional `truststore` package.

Without it, Python's requests checks against the certifi bundle only, so a machine whose network
substitutes its own certificate (an inspecting proxy, antivirus HTTPS scanning) would fail every
E*TRADE, Chaikin and EDGAR call. Verification stays ON in every case: this changes which roots are
trusted, never whether certificates are checked.

- The package is missing: nothing changes (certifi, as before).
- AETHER_TLS_TRUSTSTORE=0: opt out (certifi), e.g. to roll back without a code change.
Stdlib only: aether/__init__.py imports this before anything else.
"""
import os

try:
    import truststore as _truststore
except ImportError:          # optional: without it, requests keeps using certifi
    _truststore = None

STATE = {"source": "certifi"}


def use_system_trust_store() -> bool:
    """Make ssl (and so requests/urllib3) trust the OS store. True when it is in use."""
    if os.environ.get("AETHER_TLS_TRUSTSTORE", "1").strip() == "0" or _truststore is None:
        STATE["source"] = "certifi"
        return False
    _truststore.inject_into_ssl()
    STATE["source"] = "system trust store (truststore)"
    return True


def trust_source() -> str:
    """Which roots this process trusts, for logs and the pre-check."""
    return STATE["source"]
