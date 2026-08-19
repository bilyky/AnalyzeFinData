"""
Tests for external_intel.py — resilient email fetching and early disconnection breaks.
"""
import os
import sys
import unittest
import imaplib
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import external_intel


class TestExternalIntelResilience(unittest.TestCase):
    @mock.patch("external_intel.CFG")
    @mock.patch("external_intel._log")
    @mock.patch("imaplib.IMAP4_SSL")
    def test_imap_connection_lost_during_fetch_aborts_early(self, mock_imap_cls, mock_log, mock_cfg):
        # 1. Setup config
        mock_cfg.mailboxes = [
            {
                "email": "test@example.org",
                "password_env": "TEST_ENV_PASS",
                "imap_server": "imap.example.org"
            }
        ]
        mock_cfg.smtp_password = "dummy_password"
        mock_cfg.ai_max_intel_emails = 20
        
        # 2. Setup mock IMAP instance
        mock_mail = mock.MagicMock()
        mock_imap_cls.return_value = mock_mail
        
        # Mock folder select success
        mock_mail.select.return_value = ("OK", b"1")
        # Mock search to return 5 messages
        mock_mail.search.return_value = ("OK", [b"5 4 3 2 1"])
        
        # Mock fetch to raise connection lost exception on message "4"
        # Since reversed(messages) is processed: 1, 2, 3, 4, 5
        # Fetch for 1, 2, 3 succeeds, fetch for 4 fails with a socket error
        def side_effect(num, query):
            if num == b"4":
                raise Exception("socket error: TLS/SSL connection has been closed (EOF)")
            return ("OK", [(None, b"Message content data here")])
            
        mock_mail.fetch.side_effect = side_effect
        
        # 3. Call fetch_idea_emails
        with mock.patch("external_intel.email.message_from_bytes") as mock_msg_parser:
            mock_msg = mock.MagicMock()
            mock_msg.get.return_value = "" # No msg-id
            mock_msg.__getitem__.return_value = "sender"
            mock_msg.is_multipart.return_value = False
            mock_msg.get_payload.return_value = b"DUMMY BODY"
            mock_msg_parser.return_value = mock_msg
            
            # We mock analyze_email_content and extract_email_intel to prevent network / AI calls
            with mock.patch("external_intel.analyze_email_content", return_value=[]) as mock_analyze, \
                 mock.patch("extract_email_intel.extract", return_value={}) as mock_extract:
                ideas = external_intel.fetch_idea_emails()
                
        # 4. Verify that we stopped fetching and did not try to fetch message 5
        # In reversed order, 1, 2, 3 were called, 4 was called (and failed). 5 should NEVER be called.
        fetch_calls = [args[0][0] for args in mock_mail.fetch.call_args_list]
        self.assertIn(b"1", fetch_calls)
        self.assertIn(b"2", fetch_calls)
        self.assertIn(b"3", fetch_calls)
        self.assertIn(b"4", fetch_calls)
        self.assertNotIn(b"5", fetch_calls)
        
        # 5. Verify the error log is called with connection lost message
        mock_log.error.assert_any_call(
            "IMAP connection lost during message fetch: socket error: TLS/SSL connection has been closed (EOF)"
        )

    @mock.patch("external_intel.ai_client.evaluate")
    @mock.patch("external_intel.get_existing_symbols", return_value={"AAPL"})
    def test_analyze_email_content_truncation(self, mock_get_symbols, mock_evaluate):
        mock_evaluate.return_value = "[]"
        huge_body = "A" * 20000
        external_intel.analyze_email_content("test subject", huge_body)
        
        # Verify that evaluate was called, and the user prompt contained truncated body
        user_arg = mock_evaluate.call_args[0][1]
        self.assertIn("[... content truncated for safety ...]", user_arg)
        self.assertTrue(len(user_arg) < 10000)

    @mock.patch("aether.ai_client.subprocess.run")
    def test_ai_client_gemini_cli_truncation_gate(self, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0, stdout="Mock output")
        from aether import ai_client
        pcfg = {"type": "gemini_cli", "model": "gemini-2.5-flash"}
        system = "system instruction"
        user = "B" * 20000
        ai_client._call_gemini_cli(pcfg, system, user, 200, 0.3)
        
        # Verify that subprocess.run was called, and the prompt was passed inside the input keyword argument
        args = mock_run.call_args[0][0]
        self.assertIn("-p", args)
        # The -p arg carries the analyze instruction. Match a stable prefix rather than the
        # exact element so a punctuation/wording tweak to the prompt doesn't break this gate.
        self.assertTrue(
            any("Please analyze the following data" in a for a in args),
            f"analyze-instruction prompt not found in gemini args: {args}",
        )
        kwargs = mock_run.call_args[1]
        prompt_arg = kwargs.get("input", "")
        # Since we pipe large prompts via stdin, it is no longer truncated.
        self.assertIn("system instruction", prompt_arg)
        self.assertIn("B" * 20000, prompt_arg)

    @mock.patch("external_intel.CFG")
    @mock.patch("external_intel._log")
    @mock.patch("imaplib.IMAP4_SSL")
    def test_fetch_idea_emails_robust_to_unexpected_parsed_ideas_type(self, mock_imap_cls, mock_log, mock_cfg):
        # Setup config
        mock_cfg.mailboxes = [
            {
                "email": "test@example.org",
                "password_env": "TEST_ENV_PASS",
                "imap_server": "imap.example.org"
            }
        ]
        mock_cfg.smtp_password = "dummy_password"
        mock_cfg.ai_max_intel_emails = 20
        mock_cfg.system_cpu_threshold = 95.0
        mock_cfg.system_mem_threshold = 95.0
        mock_cfg.system_max_workers_normal = 2

        # Setup mock IMAP instance
        mock_mail = mock.MagicMock()
        mock_imap_cls.return_value = mock_mail
        mock_mail.select.return_value = ("OK", b"1")
        mock_mail.search.return_value = ("OK", [b"1"])
        mock_mail.fetch.return_value = ("OK", [(None, b"Subject: Test Subject\n\nStock alert: buy $AAPL now!")])

        with mock.patch("external_intel.email.message_from_bytes") as mock_msg_parser:
            mock_msg = mock.MagicMock()
            mock_msg.get.return_value = "unique-msg-id-123" # Trigger deduplication
            mock_msg.__getitem__.return_value = "sender"
            mock_msg.is_multipart.return_value = False
            mock_msg.get_payload.return_value = b"Stock alert: buy $AAPL now!"
            mock_msg_parser.return_value = mock_msg

            # 1. Test when analyze_email_content returns a dictionary instead of a list
            with mock.patch("external_intel.analyze_email_content", return_value={"symbol": "AAPL", "sentiment": "BUY", "thesis": "Strong setup"}) as mock_analyze, \
                 mock.patch("extract_email_intel.extract", return_value={}) as mock_extract:
                ideas = external_intel.fetch_idea_emails()
                self.assertEqual(len(ideas), 1)
                self.assertEqual(ideas[0]["symbol"], "AAPL")
                self.assertEqual(ideas[0]["sentiment"], "BUY")
                self.assertEqual(ideas[0]["thesis"], "Strong setup")

            # 2. Test when analyze_email_content returns a list of strings instead of dicts
            with mock.patch("external_intel.analyze_email_content", return_value=["AAPL", "MSFT"]) as mock_analyze, \
                 mock.patch("extract_email_intel.extract", return_value={}) as mock_extract:
                ideas = external_intel.fetch_idea_emails()
                self.assertEqual(len(ideas), 0)


if __name__ == "__main__":
    unittest.main()
