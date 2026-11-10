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

    def test_host_match_ignores_case_and_old_files_still_load(self):
        self._write(host="Dev-PC")
        with mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"):
            self.assertEqual(self.fs.load(ENV)["oauth_token"], "t")
        self._write()                                                   # saved before the host field
        with mock.patch.object(store.socket, "gethostname", return_value="DEV-PC"):
            self.assertEqual(self.fs.load(ENV)["oauth_token"], "t")
            self.assertEqual(self.fs.load_any_date(ENV)["oauth_token"], "t")


if __name__ == "__main__":
    unittest.main()
