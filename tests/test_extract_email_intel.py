"""
Tests for extract_email_intel — structural intelligence extraction from emails.
ai_client is mocked; no real AI calls.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import extract_email_intel as ex

_FAKE_RESPONSE = json.dumps({
    "summary": "Nuclear power for AI data centers has a uranium supply deficit.",
    "dated_catalysts": [
        {"date": "2028-01-01", "event": "Russian uranium ban waivers expire",
         "impact": "US loses ~25% of enriched uranium supply overnight"}
    ],
    "supply_chain_facts": [
        "World mined 173M lb uranium in 2024, burned 204M lb — structural deficit exists"
    ],
    "missing_symbols": [
        {"symbol": "LEU", "reason": "Only US HALEU enricher; direct 2028 catalyst"}
    ],
    "tickers_mentioned": [
        {"symbol": "CCJ", "sentiment": "BUY", "thesis": "Largest listed uranium miner"}
    ],
    "rd_topics": [
        "Track DOE HALEU waiver utilization as a leading indicator"
    ],
    "confidence": "HIGH",
    "pitch_ratio": "7"
})


class TestParse(unittest.TestCase):
    def test_valid_json(self):
        out = ex._parse(_FAKE_RESPONSE)
        self.assertEqual(out["confidence"], "HIGH")
        self.assertEqual(out["missing_symbols"][0]["symbol"], "LEU")

    def test_markdown_fenced(self):
        out = ex._parse("```json\n" + _FAKE_RESPONSE + "\n```")
        self.assertEqual(out["pitch_ratio"], "7")

    def test_empty(self):
        self.assertEqual(ex._parse(""), {})

    def test_invalid_json(self):
        self.assertEqual(ex._parse("not json"), {})


class TestExtract(unittest.TestCase):
    def test_returns_structured_intel(self):
        verify_resp = json.dumps({"dated_catalysts": [
            {"claim": "Russian uranium ban waivers all expire", "status": "VERIFIED", "note": "Confirmed by legislation"}
        ], "supply_chain_facts": []})
        with mock.patch("ai_client.primary", return_value="gpt"), \
             mock.patch("ai_client.evaluate", side_effect=[_FAKE_RESPONSE, verify_resp]), \
             mock.patch("data_api.read_research", return_value={"rows": []}):
            result = ex.extract("Test Subject", "Test body about uranium.")
        self.assertEqual(result["confidence"], "HIGH")
        self.assertEqual(len(result["dated_catalysts"]), 1)
        self.assertEqual(result["dated_catalysts"][0]["date"], "2028-01-01")
        self.assertEqual(result["_provider"], "gpt")
        self.assertIn("_verification", result)
        self.assertIn("_validation", result)
        self.assertIn("LEU", result["_validation"]["missing_from_universe"])

    def test_no_provider_returns_empty(self):
        with mock.patch("ai_client.primary", return_value=None):
            self.assertEqual(ex.extract("s", "b"), {})

    def test_ai_failure_returns_empty(self):
        with mock.patch("ai_client.primary", return_value="gpt"), \
             mock.patch("ai_client.evaluate", return_value=""):
            self.assertEqual(ex.extract("s", "b"), {})


class TestReport(unittest.TestCase):
    def test_empty_intel(self):
        self.assertIn("no intel", ex.report({}))

    def test_full_report_contains_key_fields(self):
        intel = ex._parse(_FAKE_RESPONSE)
        report = ex.report(intel)
        self.assertIn("2028-01-01", report)
        self.assertIn("LEU", report)
        self.assertIn("Pitch ratio", report)
        self.assertIn("HALEU", report)


if __name__ == "__main__":
    unittest.main()
