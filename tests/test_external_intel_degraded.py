"""
Red-green tests for external_intel.fetch_idea_emails() degraded-state signalling.

The contract under test (review finding #5): an empty result caused by EVERY configured
mailbox failing must be distinguishable from a genuine "no ideas today". A total failure
raises (so the pipeline's error handler surfaces it); zero configured mailboxes returns []
normally; the list return type is preserved for the normal/partial path.

All IMAP is mocked; no network.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import external_intel


def _mb(addr):
    return {"email": addr, "password_env": "SMTP_PASSWORD", "imap_server": "imap.example.test"}


class TestExternalIntelDegradedState(unittest.TestCase):

    def _patch_cfg(self, mailboxes, smtp_password=""):
        cfg = mock.MagicMock()
        cfg.mailboxes = mailboxes
        cfg.smtp_password = smtp_password
        return mock.patch.object(external_intel, "CFG", cfg)

    def test_all_mailboxes_failing_raises_not_silent_empty(self):
        # Two real mailboxes, both error at IMAP connect → total failure → RuntimeError,
        # NOT a silent [] that reads like "no ideas today".
        with self._patch_cfg([_mb("a@x.test"), _mb("b@x.test")]), \
             mock.patch.dict(os.environ, {"SMTP_PASSWORD": "pw"}), \
             mock.patch("external_intel.imaplib.IMAP4_SSL",
                        side_effect=OSError("no route to host")):
            with self.assertRaises(RuntimeError):
                external_intel.fetch_idea_emails()

    def test_no_mailboxes_configured_returns_empty_list(self):
        # attempted == 0 → the "all failed" guard must NOT fire; a plain [] is correct here.
        with self._patch_cfg([]):
            out = external_intel.fetch_idea_emails()
        self.assertEqual(out, [])

    def test_placeholder_only_mailboxes_return_empty_list(self):
        # example.com placeholders are skipped before the attempt counter → not a failure.
        with self._patch_cfg([{"email": "you@example.com"}]):
            out = external_intel.fetch_idea_emails()
        self.assertEqual(out, [])

    def test_missing_password_counts_as_failure_not_hard_raise_midloop(self):
        # A mailbox with no available password must be counted + skipped (resilient), and
        # because it is the ONLY mailbox and it failed, the post-loop guard raises.
        with self._patch_cfg([_mb("a@x.test")], smtp_password=""), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("external_intel.imaplib.IMAP4_SSL") as m_imap:
            with self.assertRaises(RuntimeError):
                external_intel.fetch_idea_emails()
            m_imap.assert_not_called()  # never even attempted the connection


if __name__ == "__main__":
    unittest.main()
