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
# aether.logger opens its file handlers at IMPORT time (module-level
# `log = get_logger("aether")`), so re-pointing _LOG_DIR alone is too late — the
# handlers already target the repo's Data/logs. Close them and rebuild the chain
# under the temp dir. Guarded by tests/test_log_hermetic.py.
import logging as _logging

_aether_root_logger = _logging.getLogger("aether")
for _h in list(_aether_root_logger.handlers):
    _aether_root_logger.removeHandler(_h)
    _h.close()
aether.logger._initialised = False
aether.logger._init()
# daily_task.py attaches its own FileHandler (repo-root daily_task.log) at import,
# but only when its logger has no handlers yet — pre-seed one under the temp dir.
_logging.getLogger("aether.daily_task").addHandler(
    _logging.FileHandler(Path(_test_log_dir.name) / "daily_task.log", encoding="utf-8", delay=True))

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

# ---------------------------------------------------------------------------
# Prod-workbook guard — no test may WRITE the production state_of_the_day.xlsx.
# ---------------------------------------------------------------------------
# The workbook is gitignored live-portfolio state. powergauge.check_from_xls and
# the pipeline savers call wb.save(XLSX_FILE); a save-path test would corrupt
# real trading state. Point every module's XLSX_FILE at a throwaway temp book:
# .exists() and load_workbook still work, every save lands in temp. Every test
# that reads the workbook mocks load_workbook with its own in-memory book, so the
# temp only has to exist — it deliberately does NOT mirror prod (no real data in
# temp, no per-import copy). Unconditional: the live tier opts into the real
# network, never into writing prod state. Per-test overrides (test_breadth_filter)
# still win; they default to temp now.
import openpyxl as _openpyxl

_test_xlsx_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
_test_xlsx_path = Path(_test_xlsx_dir.name) / "state_of_the_day.xlsx"
_seed_wb = _openpyxl.Workbook()
_seed_wb.active.title = "Research"
_seed_wb.save(_test_xlsx_path)

for _mod_name in ("ai_portfolio_game", "powergauge", "workbook_read", "autonomous_pipeline"):
    try:
        _mod = _importlib.import_module(_mod_name)
    except Exception:
        continue
    # autonomous_pipeline.log() also appends to its own legacy run log
    # (Data/autonomous_run.log, read by watchdog and the dashboard) — redirect it too.
    if hasattr(_mod, "LOG_FILE_PATH"):
        _mod.LOG_FILE_PATH = Path(_test_log_dir.name) / "autonomous_run.log"
    _orig_xlsx_const = getattr(_mod, "XLSX_FILE", None)
    if _orig_xlsx_const is None:
        continue
    # powergauge stores a str (os.path.join), the others a Path — preserve each.
    _mod.XLSX_FILE = _test_xlsx_path if isinstance(_orig_xlsx_const, Path) else str(_test_xlsx_path)

# ---------------------------------------------------------------------------
# Learning-ledger guard — no test may write the decision log, the trade-DNA
# ledger, or the failure-DNA rules. Test fixtures (P1..P6, TSCO cost 100/stop 80)
# had leaked into these files, skewing the decision_eval scorecard and the
# retrospective analyzer. Unconditional, like the workbook guard.
# ---------------------------------------------------------------------------
import aether.decision_eval as _decision_eval
import aether.ledgers as _ledgers

_test_ledger_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
_ledger_tmp = Path(_test_ledger_dir.name)
_decision_eval.LOG = _ledger_tmp / "decision_log.jsonl"
_ledgers.TRADE_DNA_FILE = _ledger_tmp / "trade_history_dna.json"
_ledgers.FAILURE_RULES_FILE = _ledger_tmp / "failure_dna_rules.json"
_ledgers.RETRO_REPORT_FILE = _ledger_tmp / "retrospective_report.txt"

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
    _etrade._REAUTH_LOCK_PATH   = str(Path(_test_etrade_dir.name) / "etrade_reauth.lock")

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
        
        # Apply the blocks directly to the module-level local references inside etrade and powergauge
        try:
            _etrade.sync_playwright = _blocked_playwright
        except (ImportError, AttributeError):
            pass
        try:
            _powergauge = _importlib.import_module("powergauge")
            _powergauge.sync_playwright = _blocked_playwright
        except (ImportError, AttributeError):
            pass


    # -- Process side: no test may kill a real process or launch the real game --
    # watchdog.run_watchdog() runs the live process supervisor (taskkill of duplicate
    # server.py / "orphaned" AETHER consoles) and a real `ai_portfolio_game.py --report`
    # child, which logs to the repo's Data/logs because a child never loads this harness.
    # A test that mocks subprocess itself still wins (its patch replaces these wrappers).
    import subprocess as _subprocess

    _FORBIDDEN_PROC_TOKENS = ("taskkill", "stop-process", "ai_portfolio_game.py")

    def _argv_text(args):
        if isinstance(args, (list, tuple)):
            return " ".join(str(a) for a in args)
        return str(args)

    def _guard_proc(args):
        text = _argv_text(args).lower()
        hit = next((t for t in _FORBIDDEN_PROC_TOKENS if t in text), None)
        if hit:
            raise RuntimeError(
                f"Blocked real process side effect in tests ({hit!r} in {_argv_text(args)[:120]!r}). "
                "Mock subprocess at the boundary, or set AETHER_LIVE_TESTS=1."
            )

    _orig_subprocess_run = _subprocess.run
    _orig_subprocess_popen = _subprocess.Popen

    def _guarded_run(args, *a, **k):
        _guard_proc(args)
        return _orig_subprocess_run(args, *a, **k)

    class _GuardedPopen(_orig_subprocess_popen):
        def __init__(self, args, *a, **k):
            _guard_proc(args)
            super().__init__(args, *a, **k)

    _subprocess.run = _guarded_run
    _subprocess.Popen = _GuardedPopen
