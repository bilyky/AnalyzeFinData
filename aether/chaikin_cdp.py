"""Reusable Chaikin CDP-attach capture.

The Chaikin ``sessionToken`` has a hard ~7-day life and the ONLY renewal proven to work
(live 2026-09-17) is **CDP-attach to a REAL, human-launched Chrome**: a genuine human passes
Cloudflare Turnstile once, then this module attaches over the DevTools protocol and reads
``sessionToken``/``jsessionId``/``sessionKey``/``email`` straight out of the warm tab's
localStorage. Playwright/CDP-*driven* Chrome trips Turnstile error 600010 even with a human
clicking (automation fingerprint, not IP) — so there is no fully zero-human login.

This is the single capture code path shared by ``scripts/monitoring/chaikin_reauth.py --cdp``
(the 1-click System-page flow) and the local diagnostics capture tool. Token VALUES are never
logged — only non-reversible length+sha fingerprints via :func:`fingerprint`.
"""
import hashlib
import os

from aether_logger import get_logger as _get_logger

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Playwright is only needed for the actual capture, not for import.
    sync_playwright = None

_log = _get_logger("chaikin_cdp")

DEFAULT_CDP = "http://localhost:9222"

# Hosts whose tabs may hold a logged-in Chaikin session (members.* is authoritative).
_CHAIKIN_HOSTS = (
    "members.chaikinanalytics.com",
    "app.chaikinanalytics.com",
    "chaikinanalytics.com",
)
# localStorage keys worth reporting when scoring which tab is "most logged in".
_INTERESTING = (
    "sessionToken", "jsessionId", "sessionKey", "omniSessionKey",
    "sessionId", "email", "beaconStreetJwtToken",
)


class CdpUnreachable(Exception):
    """The Chrome DevTools endpoint could not be reached (debug port not up)."""


class NoChaikinTab(Exception):
    """Attached to Chrome, but found no logged-in Chaikin tab to read."""


def fingerprint(value) -> str:
    """Non-reversible length+sha256 fingerprint of a secret — never the value itself."""
    if value is None:
        return "None"
    text = str(value)
    if text == "":
        return "len=0 (empty)"
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]
    return f"len={len(text)} sha={digest}"


def ensure_localhost_noproxy() -> None:
    """Add localhost/127.0.0.1 to NO_PROXY so the CDP request bypasses any corporate proxy."""
    for var in ("NO_PROXY", "no_proxy"):
        hosts = [h.strip() for h in os.environ.get(var, "").split(",") if h.strip()]
        for host in ("localhost", "127.0.0.1"):
            if host not in hosts:
                hosts.append(host)
        os.environ[var] = ",".join(hosts)


def _read_localstorage(page) -> dict:
    """Return a page's full localStorage as a dict, or {} on failure."""
    try:
        return page.evaluate(
            "() => { const o = {}; for (let i = 0; i < localStorage.length; i++)"
            " { const k = localStorage.key(i); o[k] = localStorage.getItem(k); } return o; }"
        ) or {}
    except Exception as ex:  # noqa: BLE001 - a dead/navigating tab must not abort capture
        _log.warning("localStorage read failed: %s", type(ex).__name__)
        return {}


def connect_and_capture(cdp: str = DEFAULT_CDP, open_tab: bool = False, log=None):
    """Attach to Chrome over CDP and return ``(localStorage_dict, origin_url)`` for the best
    Chaikin tab (members.* preferred).

    Args:
        cdp: DevTools endpoint, e.g. ``http://localhost:9222``.
        open_tab: if no Chaikin tab is found, open members.chaikinanalytics.com (warm cookies)
            rather than raising — used by the diagnostics tool's ``--open`` mode.
        log: optional ``callable(str)`` for progress; defaults to this module's logger. Token
            values are never passed to it — only fingerprints.

    Raises:
        CdpUnreachable: the debug endpoint is down (map to exit 2).
        NoChaikinTab: attached but no logged-in Chaikin tab (map to exit 3).
    """
    emit = log or (lambda m: _log.info("%s", m))
    if sync_playwright is None:
        raise CdpUnreachable("playwright is not installed in this environment")

    ensure_localhost_noproxy()
    emit(f"Attaching to Chrome at {cdp} ...")
    captured: dict = {}
    origin_used = None

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp)
        except Exception as e:  # noqa: BLE001 - normalise every connect failure to CdpUnreachable
            first = next((ln for ln in str(e).splitlines() if ln.strip()), type(e).__name__)
            raise CdpUnreachable(first.strip()) from e

        ctx = browser.contexts[0] if browser.contexts else browser.new_context()

        chaikin_pages = []
        for tab in list(ctx.pages):
            try:
                url = (tab.url or "").lower()
            except Exception:  # noqa: BLE001 - skip a tab whose url can't be read
                continue
            if any(h in url for h in _CHAIKIN_HOSTS):
                chaikin_pages.append(tab)

        created_page = None
        if not chaikin_pages and open_tab:
            emit("No Chaikin tab open — opening members.chaikinanalytics.com (warm cookies).")
            created_page = ctx.new_page()
            try:
                created_page.goto("https://members.chaikinanalytics.com",
                                  wait_until="domcontentloaded", timeout=45000)
                created_page.wait_for_timeout(6000)
            except Exception as ex:  # noqa: BLE001 - navigation is best-effort
                emit(f"  [warn] navigation failed: {type(ex).__name__}")
            chaikin_pages = [created_page]

        if not chaikin_pages:
            raise NoChaikinTab("no logged-in Chaikin tab in the attached Chrome")

        # Prefer the members.* origin (+5) and, within that, the tab carrying the most
        # interesting keys — a stray app.* or logged-out tab must not win.
        best_score = -1
        for tab in chaikin_pages:
            try:
                url = tab.url or ""
            except Exception:  # noqa: BLE001
                url = ""
            store = _read_localstorage(tab)
            present = [k for k in _INTERESTING if store.get(k)]
            emit(f"Tab {url[:90]} - {len(store)} localStorage keys; "
                 f"interesting: {sorted(present)}")
            score = len(present) + (5 if "members." in url else 0)
            if score > best_score:
                best_score = score
                captured = store
                origin_used = url

        if created_page is not None:
            try:
                created_page.close()
            except Exception:  # noqa: BLE001
                pass

    return captured, origin_used
