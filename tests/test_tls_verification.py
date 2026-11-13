"""No runtime code turns TLS certificate verification off. E*TRADE's token, renew and revoke calls
and the Chaikin session sent credentials with verify=False (and powergauge silenced the warning
for the whole process): anyone able to intercept the connection could read or replace them. With
verification on, both hosts connect through the corporate proxy (checked 2026-11-10). This scan
keeps it that way: it fails on any bypass in tracked .py files outside tests/."""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BYPASS = [
    re.compile(r"verify\s*=\s*False"),                        # requests.get(..., verify=False) / session.verify = False
    re.compile(r"disable_warnings\s*\(\s*urllib3\.exceptions\.InsecureRequestWarning"),
    re.compile(r"InsecureRequestWarning"),                    # silencing it hides an unverified call
    re.compile(r"ignore_https_errors\s*=\s*True"),            # Playwright
    re.compile(r"CERT_NONE|check_hostname\s*=\s*False"),      # ssl module
    re.compile(r"_create_unverified_context"),                # ssl / urllib
    re.compile(r"--ignore-certificate-errors"),               # Chrome / Chromium launch flag
]


def _tracked_py():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if not p.startswith("tests/")]


def scan(text: str) -> list:
    """Line numbers (1-based) of TLS bypasses in `text`."""
    return [i for i, line in enumerate(text.splitlines(), 1) if any(p.search(line) for p in BYPASS)]


class TestNoTlsBypass(unittest.TestCase):
    def test_runtime_code_verifies_tls(self):
        hits = []
        for rel in _tracked_py():
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                hits += [f"{rel}:{n}" for n in scan(f.read())]
        self.assertEqual(hits, [], "TLS verification is turned off here: " + ", ".join(hits))

    def test_scan_catches_each_form(self):
        for bad in ('requests.get(u, verify=False)', 's.verify = False',
                    'urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)',
                    'browser.new_context(ignore_https_errors=True)', 'ctx.check_hostname = False',
                    'ctx.verify_mode = ssl.CERT_NONE', 'urlopen(u, context=ssl._create_unverified_context())',
                    'args=["--ignore-certificate-errors"]'):
            with self.subTest(bad=bad):
                self.assertEqual(scan(bad), [1])
        self.assertEqual(scan("requests.get(u, timeout=10)\nverify_stops()"), [])


class TestPrecheck(unittest.TestCase):
    """scripts/diagnostics/tls_precheck.py, the PROD step before the deploy: any status passes, a
    certificate error fails (1), and a host that doesn't answer is "not checked" (2), never a pass."""

    def _run(self, side_effect):
        import importlib.util
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        spec = importlib.util.spec_from_file_location(
            "tls_precheck", os.path.join(ROOT, "scripts", "diagnostics", "tls_precheck.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = io.StringIO()
        with mock.patch.object(mod.requests, "get", side_effect=side_effect), redirect_stdout(out):
            return mod.check(("https://a.example", "https://b.example")), out.getvalue()

    def test_any_status_passes(self):
        from unittest import mock
        rc, out = self._run(lambda u, timeout: mock.Mock(status_code=401))
        self.assertEqual(rc, 0)
        self.assertIn("All certificate checks passed.", out)

    def test_a_certificate_error_fails(self):
        import requests
        from unittest import mock

        def get(u, timeout):
            if "b." in u:
                raise requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
            return mock.Mock(status_code=200)
        rc, out = self._run(get)
        self.assertEqual(rc, 1)
        self.assertIn("SSL FAIL", out)

    def test_no_answer_is_not_checked_not_passed(self):
        import requests

        def get(u, timeout):
            raise requests.exceptions.ConnectionError("refused")
        rc, out = self._run(get)
        self.assertEqual(rc, 2)                       # not checked is not a pass
        self.assertIn("NO ANSWER", out)
        self.assertNotIn("All certificate checks passed.", out)


class TestTlsFailureIsNotADeadToken(unittest.TestCase):
    """#257 review: with an SSLError in the token check and the renew, get_tokens() used to fall
    through to the automated headless login (['GET list', 'GET renew', 'LOGIN']). A certificate
    failure isn't a dead token: no automated login. A plain network error keeps today's path, and
    a person (allow_browser=True) may still log in."""

    def _run(self, exc, allow_browser=False, only_url=None, saved_at=0):
        import json
        import tempfile
        from unittest import mock
        import requests
        from aether import etrade
        calls = []

        class Session:
            def __init__(self, *a, **k):
                pass

            def get(self, url, **k):
                calls.append("GET")
                if only_url and only_url not in url:
                    return mock.Mock(status_code=200)
                raise exc

        def login(*a, **k):
            calls.append("LOGIN")
            return None
        tmp = tempfile.mkdtemp()
        tok = {"oauth_token": "t", "oauth_token_secret": "s", "env": "production", "saved_at": saved_at,
               "issued_date_et": etrade._et_today()}
        with open(etrade._TOKEN_PATH, "w") as f:
            json.dump(tok, f)
        self.addCleanup(lambda: os.path.exists(etrade._TOKEN_PATH) and os.remove(etrade._TOKEN_PATH))
        with mock.patch.object(etrade, "OAuth1Session", Session),                 mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "p")),                 mock.patch.object(etrade, "_login_headless", side_effect=login),                 mock.patch.object(etrade, "_reauth_cooldown_remaining", return_value=0),                 mock.patch.object(etrade, "_DATA_DIR", tmp),                 mock.patch.object(etrade, "_REAUTH_LOCK_PATH", os.path.join(tmp, "reauth.lock")),                 mock.patch.object(etrade.CFG, "etrade_totp_secret", "x", create=True),                 mock.patch.object(etrade.sys.stdin, "isatty", return_value=False):
            try:
                result = etrade.get_tokens("production", allow_browser=allow_browser)
            except RuntimeError:                     # interactive path refuses without a terminal
                result = "interactive"
        return result, calls, requests

    def test_certificate_failure_stops_before_the_automated_login(self):
        import requests
        with self.assertLogs("aether.etrade", level="ERROR") as cm:
            result, calls, _ = self._run(requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"))
        self.assertIsNone(result)
        self.assertNotIn("LOGIN", calls)
        self.assertTrue(any("skipping automated re-auth: TLS" in line for line in cm.output))

    def test_the_token_check_alone_counts(self):
        import time
        import requests
        with self.assertLogs("aether.etrade", level="ERROR") as cm:   # a fresh token: no renew call
            result, calls, _ = self._run(requests.exceptions.SSLError("x"), saved_at=time.time())
        self.assertEqual(calls, ["GET"])                  # no login; the fresh token is kept, as for any blip
        self.assertEqual(result["oauth_token"], "t")
        self.assertTrue(any("TLS certificate check failed in the token check" in line for line in cm.output))

    def test_the_renew_alone_counts(self):
        import requests
        with self.assertLogs("aether.etrade", level="ERROR"):     # the check answers 200; the renew fails TLS
            result, calls, _ = self._run(requests.exceptions.SSLError("x"), only_url="renew")
        self.assertNotIn("LOGIN", calls)
        self.assertIsNone(result)

    def test_a_network_error_keeps_todays_path(self):
        import requests
        result, calls, _ = self._run(requests.exceptions.ConnectionError("refused"))
        self.assertIn("LOGIN", calls)

    def test_an_earlier_tls_failure_does_not_block_a_later_call(self):
        import requests
        with self.assertLogs("aether.etrade", level="ERROR"):
            self._run(requests.exceptions.SSLError("x"))
        result, calls, _ = self._run(requests.exceptions.ConnectionError("refused"))
        self.assertIn("LOGIN", calls)                    # the failure belonged to the first call only

    def test_a_person_may_still_log_in(self):
        import requests
        result, calls, _ = self._run(requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"), allow_browser=True)
        self.assertIn("LOGIN", calls)


if __name__ == "__main__":
    unittest.main()
