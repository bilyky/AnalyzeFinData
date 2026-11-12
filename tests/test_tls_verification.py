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


if __name__ == "__main__":
    unittest.main()
