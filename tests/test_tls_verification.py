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
                    'ctx.verify_mode = ssl.CERT_NONE'):
            with self.subTest(bad=bad):
                self.assertEqual(scan(bad), [1])
        self.assertEqual(scan("requests.get(u, timeout=10)\nverify_stops()"), [])


if __name__ == "__main__":
    unittest.main()
