# Globally mock notify.send_email during all unit test executions
# to completely prevent test email spam and keep production code clean.
import unittest.mock as _mock
import aether.notify as _notify
import notify as _root_notify

_notify.send_email = _mock.MagicMock(return_value=True)
_root_notify.send_email = _mock.MagicMock(return_value=True)

# Globally redirect all test log outputs to a temporary directory
# to prevent tests from writing to or polluting production logs.
import tempfile
from pathlib import Path
import aether.logger

_test_log_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
aether.logger._LOG_DIR = Path(_test_log_dir.name)

# ---------------------------------------------------------------------------
# Hermeticity guard — no test may touch prod state, contact a live host, or
# open a real browser. This whole block is one switch: AETHER_LIVE_TESTS=1
# disables it for the explicitly live-gated contract tests (test_live_api_contract,
# test_game_pricing, test_etrade_live), which INTEND to touch the real token files
# and reach the real broker over ban-safe HTTP. Everything below stays gated
# together so a live run sees real files AND a real network, and a hermetic run
# sees neither.
# ---------------------------------------------------------------------------
# A plain `python -m unittest discover tests` must NEVER reach E*TRADE, Chaikin,
# Google, or any external API, nor read/write the production auth-state files. On
# 2026-08-18 an unmocked requalify_symbol("AAPL") in the suite fell through to a
# live E*TRADE re-auth + a real Chaikin browser login, tripping Akamai Bot Manager
# and driving toward an IP ban.
import os as _os
import socket as _socket
import importlib as _importlib
import aether.etrade as _etrade
import aether.instruments as _instruments

# playwright may be absent in some environments; import it dynamically (no
# top-of-block import statement, so the pre-commit import linter stays happy)
# and fail soft to None if it isn't installed.
try:
    _pw_sync = _importlib.import_module("playwright.sync_api")
except Exception:
    _pw_sync = None

if not _os.getenv("AETHER_LIVE_TESTS"):
    # -- State side: redirect prod auth-state / cache files to a throwaway temp dir --
    # A test that reaches get_tokens() finds no saved browser state (so the automated
    # headless path is skipped entirely — never even attempted) and any breaker
    # bookkeeping lands in temp, never polluting the production files. Tests that need
    # their own state file still patch these constants per-test (that override wins).
    _test_etrade_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    _etrade._TOKEN_PATH         = str(Path(_test_etrade_dir.name) / "etrade_tokens.json")
    _etrade._BROWSER_STATE_PATH = str(Path(_test_etrade_dir.name) / "etrade_browser_state.json")
    _etrade._REAUTH_STATE_PATH  = str(Path(_test_etrade_dir.name) / "etrade_reauth_state.json")

    # Scarcity-classification cache → temp, so a buy-path test that classifies a real
    # symbol never writes the production Data/scarcity_cache.json.
    _instruments._SCARCITY_CACHE_FILE = Path(_test_etrade_dir.name) / "scarcity_cache.json"

    # -- Wire side: block outbound sockets (except loopback) + Playwright launches --
    _ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

    def _host_of(address):
        # AF_INET -> (host, port); AF_UNIX/other -> str/path (local, allow).
        if isinstance(address, tuple):
            return address[0]
        return None

    def _blocked_net(host):
        raise RuntimeError(
            f"Blocked live network in tests (attempted connect to {host!r}). "
            "This would contact a production host — a real unmocked call once drove "
            "an Akamai bot-block toward an E*TRADE IP ban. Mock the network boundary, "
            "or set AETHER_LIVE_TESTS=1 to run the explicitly live-gated contract tests."
        )

    _orig_connect    = _socket.socket.connect
    _orig_connect_ex = _socket.socket.connect_ex

    def _guard_connect(self, address, *a, **k):
        host = _host_of(address)
        if host is None or host in _ALLOWED_HOSTS:
            return _orig_connect(self, address, *a, **k)
        _blocked_net(host)

    def _guard_connect_ex(self, address, *a, **k):
        host = _host_of(address)
        if host is None or host in _ALLOWED_HOSTS:
            return _orig_connect_ex(self, address, *a, **k)
        _blocked_net(host)

    _socket.socket.connect    = _guard_connect
    _socket.socket.connect_ex = _guard_connect_ex

    # Forbid launching a real browser under tests (E*TRADE / Chaikin auth path).
    if _pw_sync is not None:
        def _blocked_playwright(*_a, **_k):
            raise RuntimeError(
                "Blocked Playwright browser launch in tests (E*TRADE/Chaikin auth). "
                "Mock the browser path, or set AETHER_LIVE_TESTS=1."
            )

        _pw_sync.sync_playwright = _blocked_playwright

