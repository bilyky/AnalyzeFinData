"""Two silent failures found 2026-11-03:
- Chaikin's session file was pinned to powergauge's folder, ignoring AETHER_DATA_DIR, so a
  worktree pointed at the real Data/ found no session and started a browser login;
- E*TRADE fetch_quotes swallowed every error (a 401 came back as a silent {})."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import requests

import powergauge
from aether import etrade

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestChaikinAuthStateFollowsDataDir(unittest.TestCase):
    def test_paths_follow_aether_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import powergauge as pg; print(pg.SESSION_FILE); print(pg._CHAIKIN_PROFILE_DIR); "
                    "print(pg._chaikin_renewer._lock_path)")
            env = dict(os.environ, AETHER_DATA_DIR=tmp)
            out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                                 text=True, timeout=120).stdout.split()[-3:]
            self.assertEqual(out, [os.path.join(tmp, "session.json"), os.path.join(tmp, "chaikin_chrome_profile"),
                                   os.path.join(tmp, "chaikin_reauth.lock")])

    def test_load_and_save_use_session_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "session.json")
            with mock.patch.object(powergauge, "SESSION_FILE", f):
                powergauge._save_session_to_file({"jsessionid": "abc"})
                self.assertEqual(powergauge._load_session_from_file(), {"jsessionid": "abc"})
            with open(f, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"jsessionid": "abc"})


class TestQuoteFailuresAreLogged(unittest.TestCase):
    def test_a_failed_batch_warns_and_the_rest_still_returns(self):
        market = mock.MagicMock()
        calls = []

        def get_quote(batch, resp_format):
            calls.append(list(batch))
            if len(calls) == 1:
                raise requests.HTTPError("401 Client Error: Unauthorized")
            return {"QuoteResponse": {"QuoteData": [{"Product": {"symbol": "ZZ"}, "All": {"lastTrade": 5.0}}]}}

        market.get_quote.side_effect = get_quote
        syms = [f"S{i}" for i in range(25)] + ["ZZ"]
        with mock.patch.object(etrade, "get_market", return_value=market), \
                self.assertLogs(etrade._log.name, level="WARNING") as cm:
            out = etrade.fetch_quotes({"oauth_token": "t"}, syms, "production")
        self.assertEqual(out, {"ZZ": 5.0})
        self.assertIn("E*TRADE quotes failed for 25 symbols (S0, S1, S2, S3, S4 ...): 401", cm.output[0])


if __name__ == "__main__":
    unittest.main()
