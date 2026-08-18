"""
Red-green test for verify_data_contiguity finding #10.

The audit must decide whether E*TRADE is "online" by a REAL, non-interactive session
probe (etrade.get_tokens(..., allow_browser=False)) — not by the mere existence of a
token file on disk. An expired/rejected token file exists but yields no live quotes, so
a file-existence check would falsely enable the E*TRADE-vs-cache pricing audit and emit
spurious mismatches.

Green (current code): get_tokens is invoked with allow_browser=False.
Red  (old code using os.path.exists): get_tokens is never called → assertion fails.

data_api.read_accounts is stubbed to return no holdings so the audit exits cleanly right
after the probe; no filesystem cache fixtures needed.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import verify_data_contiguity


class TestEtradeOnlineProbe(unittest.TestCase):

    def test_online_check_uses_noninteractive_token_probe_not_file_existence(self):
        with mock.patch("verify_data_contiguity.etrade.get_tokens") as m_tokens, \
             mock.patch("verify_data_contiguity.data_api.read_accounts",
                        return_value={"accounts": []}):
            m_tokens.return_value = None  # expired/invalid session
            result = verify_data_contiguity.audit_database()

        # The genuine session probe must have run, non-interactively (no browser launch).
        m_tokens.assert_called_once_with("production", allow_browser=False)
        # No held symbols → audit passes vacuously; we only assert it did not crash.
        self.assertTrue(result)

    def test_probe_exception_is_swallowed_and_treated_as_offline(self):
        # A failing probe must degrade to "offline", never propagate out of the audit.
        with mock.patch("verify_data_contiguity.etrade.get_tokens",
                        side_effect=RuntimeError("token store unreadable")), \
             mock.patch("verify_data_contiguity.data_api.read_accounts",
                        return_value={"accounts": []}):
            result = verify_data_contiguity.audit_database()
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
