"""The trusted-device cookie warning only applies to the legacy Chrome login.

With a TOTP secret configured the nightly login uses a fresh browser and never refreshes
etrade_browser_state.json, so the file only ages and the "expiring soon" email repeated daily
on PROD (2026-10-13 onwards) while the logins kept working.
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests  # hermetic: redirects prod Data/ to temp
from aether import etrade


class TestCookieWarning(unittest.TestCase):
    def _run(self, totp_secret):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "etrade_browser_state.json")
            with open(state, "w") as f:
                f.write("{}")
            old = time.time() - 35 * 24 * 3600
            os.utime(state, (old, old))
            with mock.patch.object(etrade, "_BROWSER_STATE_PATH", state), \
                 mock.patch.object(etrade, "_FAIL_STATE_PATH", os.path.join(d, "fail.json")), \
                 mock.patch.object(etrade.CFG, "etrade_totp_secret", totp_secret), \
                 mock.patch.object(etrade.notify, "send_email") as send, \
                 mock.patch.object(etrade, "_log") as log:
                etrade.check_etrade_cookie_freshness()
        return send, log

    def test_no_warning_when_totp_is_configured(self):
        send, log = self._run("JBSWY3DPEHPK3PXP")
        send.assert_not_called()
        log.warning.assert_not_called()

    def test_legacy_login_still_warns_on_a_35_day_old_file(self):
        send, log = self._run("")
        send.assert_called_once()
        self.assertIn("35.0 days old", log.warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
