"""An E*TRADE token file saved on another machine is ignored. A backup copy of PROD's Data/
brought PROD's live token onto dev (2026-11-05 and 2026-11-10); dev and PROD share one app key,
so using or renewing that token from dev can end PROD's session. Each save records the host;
load() and load_any_date() (the renew path) skip a file from another host. Files saved before
the host was recorded still load. The token path is a per-suite temp file (tests/__init__.py)."""
import json
import os
import unittest
from unittest import mock

from aether import etrade
from aether.etrade import store

ENV = "production"


class TestTokenHost(unittest.TestCase):
    def setUp(self):
        self.fs = store.FileTokenStore()
        store._warned_hosts.clear()
        self.addCleanup(store._warned_hosts.clear)
        self.addCleanup(lambda: os.path.exists(etrade._TOKEN_PATH) and os.remove(etrade._TOKEN_PATH))

    def _write(self, **extra):
        tok = {"oauth_token": "t", "oauth_token_secret": "s", "env": ENV, "saved_at": 0,
               "issued_date_et": etrade._et_today(), **extra}
        with open(etrade._TOKEN_PATH, "w") as f:
            json.dump(tok, f)

    def test_save_records_this_host_and_loads_back(self):
        with mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"):
            self.fs.save({"oauth_token": "t", "oauth_token_secret": "s"}, ENV)
            with open(etrade._TOKEN_PATH) as f:
                self.assertEqual(json.load(f)["host"], "dev-pc")
            self.assertEqual(self.fs.load(ENV)["oauth_token"], "t")
            self.assertEqual(self.fs.load_any_date(ENV)["oauth_token"], "t")

    def test_a_token_from_another_host_is_ignored(self):
        self._write(host="prod-pc")
        with mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"), \
                self.assertLogs(etrade._log.name, level="WARNING") as cm:
            self.assertIsNone(self.fs.load(ENV))
            self.assertIsNone(self.fs.load_any_date(ENV))              # nor renewed from here
        self.assertIn("saved on another machine (prod-pc)", cm.output[0])
        self.assertEqual(len(cm.output), 1)                             # two loads, one warning

    def test_warned_once_per_host_but_still_ignored(self):
        self._write(host="prod-pc")
        with mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"),                 self.assertLogs(etrade._log.name, level="WARNING") as cm:
            for _ in range(3):
                self.assertIsNone(self.fs.load(ENV))
            self._write(host="other-pc")
            self.assertIsNone(self.fs.load(ENV))
        self.assertEqual(len(cm.output), 2)                             # prod-pc once, other-pc once
        self.assertIn("(other-pc)", cm.output[1])

    def test_host_match_ignores_case_and_old_files_still_load(self):
        self._write(host="Dev-PC")
        with mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"):
            self.assertEqual(self.fs.load(ENV)["oauth_token"], "t")
        self._write()                                                   # saved before the host field
        with mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"):
            self.assertEqual(self.fs.load(ENV)["oauth_token"], "t")
            self.assertEqual(self.fs.load_any_date(ENV)["oauth_token"], "t")


class TestNoAutomatedLoginOnAForeignToken(unittest.TestCase):
    """With another machine's token skipped, the automated doors must not log this machine in: a new
    session on the shared app key can end the other machine's. A person may still log in."""

    def setUp(self):
        self.calls = []
        self.addCleanup(lambda: os.path.exists(etrade._TOKEN_PATH) and os.remove(etrade._TOKEN_PATH))
        patches = [
            mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"),
            mock.patch.object(etrade, "_login_headless", side_effect=lambda *a, **k: self.calls.append("login")),
            mock.patch.object(etrade, "_probe_token_auth", side_effect=lambda *a, **k: self.calls.append("probe") or True),
            mock.patch.object(etrade, "renew_tokens", side_effect=lambda t, e: self.calls.append("renew") or t),
            mock.patch.object(etrade, "_load_config", return_value=("ck", "cs", "u", "p")),
            mock.patch.object(etrade.CFG, "etrade_totp_secret", "x", create=True),
            mock.patch.object(etrade, "_et_is_weekend", return_value=False),
        ]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)

    def _write(self, host):
        tok = {"oauth_token": "t", "oauth_token_secret": "s", "env": ENV, "saved_at": 0,
               "issued_date_et": etrade._et_today(), "host": host}
        with open(etrade._TOKEN_PATH, "w") as f:
            json.dump(tok, f)

    def test_get_tokens_does_not_log_in_automatically(self):
        self._write("prod-pc")
        self.assertIsNone(etrade.get_tokens(ENV, allow_browser=False))
        self.assertEqual(self.calls, [])

    def test_get_tokens_own_token_still_renews(self):
        self._write("dev-pc")
        self.assertEqual(etrade.get_tokens(ENV, allow_browser=False)["oauth_token"], "t")
        self.assertEqual(self.calls, ["probe", "renew"])

    def test_own_old_token_that_fails_to_renew_still_logs_in(self):
        # This machine's own file keeps the normal ladder: a failed renew goes on to the automated login.
        tok = {"oauth_token": "t", "oauth_token_secret": "s", "env": ENV, "saved_at": 0,
               "issued_date_et": "2000-01-03", "host": "dev-pc"}
        with open(etrade._TOKEN_PATH, "w") as f:
            json.dump(tok, f)
        with mock.patch.object(etrade, "renew_tokens", side_effect=lambda t, e: self.calls.append("renew")):
            etrade.get_tokens(ENV, allow_browser=False)
        self.assertEqual(self.calls, ["renew", "login"])

    def test_a_person_may_still_log_in(self):
        self._write("prod-pc")
        try:
            etrade.get_tokens(ENV, allow_browser=True)
        except RuntimeError:
            pass                                   # no TTY for the interactive step after the mint
        self.assertIn("login", self.calls)

    def test_scheduled_reauth_stops_with_a_reason(self):
        self._write("prod-pc")
        r = etrade.scheduled_reauth(ENV, weekend_mint=False)
        self.assertEqual((r["ok"], r["reason"]), (False, etrade.AuthReason.FOREIGN_TOKEN))
        self.assertNotIn("login", self.calls)

    def test_the_manual_door_may_still_log_in(self):
        self._write("prod-pc")
        etrade.scheduled_reauth(ENV)               # manual callers keep weekend_mint=True
        self.assertIn("login", self.calls)


if __name__ == "__main__":
    unittest.main()
