"""
Tests for instruments.classify / is_excluded and the exclude_swing routing that
sends leveraged/inverse/crypto ETFs to ATR levels instead of long swing-lows.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import instruments
import risk_utils


class TestClassify(unittest.TestCase):
    def test_leveraged_inverse(self):
        for s in ("SQQQ", "TQQQ", "SOXL", "SOXS", "uvxy"):
            self.assertEqual(instruments.classify(s), "leveraged_inverse", s)
            self.assertTrue(instruments.is_excluded(s))

    def test_crypto(self):
        self.assertEqual(instruments.classify("BITO"), "crypto")
        self.assertTrue(instruments.is_excluded("BITI"))

    def test_normal(self):
        for s in ("AAPL", "INTC", "MSFT", "SPY"):   # SPY is 1x, not excluded
            self.assertEqual(instruments.classify(s), "normal", s)
            self.assertFalse(instruments.is_excluded(s))

    def test_none_safe(self):
        self.assertEqual(instruments.classify(None), "normal")
        self.assertFalse(instruments.is_excluded(""))


class TestExcludeSwingRouting(unittest.TestCase):
    # A series with a clear confirmed swing low at 90 and swing high at 120.
    LOWS = [100, 99, 98, 90, 98, 99, 100, 101, 102, 103]
    HIGHS = [100, 101, 102, 120, 102, 101, 100, 99, 98, 97]
    CLOSES = [100] * 10

    def test_swing_used_when_not_excluded(self):
        d = risk_utils.resolve_stop_detailed(105.0, lows=self.LOWS, highs=self.HIGHS,
                                             closes=self.CLOSES, exclude_swing=False)
        self.assertEqual(d["source"], "support")   # confirmed swing low

    def test_atr_when_excluded(self):
        d = risk_utils.resolve_stop_detailed(105.0, lows=self.LOWS, highs=self.HIGHS,
                                             closes=self.CLOSES, exclude_swing=True)
        self.assertIn(d["source"], ("atr", "pct"))  # swing-low skipped
        self.assertNotEqual(d["source"], "support")

    def test_target_atr_when_excluded(self):
        t = risk_utils.resolve_target_detailed(105.0, lows=self.LOWS, highs=self.HIGHS,
                                               closes=self.CLOSES, exclude_swing=True)
        self.assertNotEqual(t["source"], "resistance")

    def test_scarcity_asset_ai_classification(self):
        import unittest.mock as mock
        # Reset in-process cache so _load_scarcity_cache is actually called
        instruments._scarcity_mem = None
        mock_cache = {}
        with mock.patch("instruments._load_scarcity_cache", return_value=mock_cache), \
             mock.patch("instruments._save_scarcity_cache"), \
             mock.patch("ai_client.evaluate", return_value="YES") as mock_eval:

            is_scarcity = instruments.is_scarcity_asset("GLD", "Gold Mining")
            self.assertTrue(is_scarcity)
            self.assertEqual(instruments._scarcity_mem.get("GLD"), True)  # result in real cache
            mock_eval.assert_called_once()  # AI called once on cache miss

            # Second call: in-process cache hit — AI not called again
            mock_eval.reset_mock()
            is_scarcity_cached = instruments.is_scarcity_asset("GLD", "Gold Mining")
            self.assertTrue(is_scarcity_cached)
            mock_eval.assert_not_called()

    def test_scarcity_asset_ai_classification_negative(self):
        import unittest.mock as mock
        instruments._scarcity_mem = None
        mock_cache = {}
        with mock.patch("instruments._load_scarcity_cache", return_value=mock_cache), \
             mock.patch("instruments._save_scarcity_cache"), \
             mock.patch("ai_client.evaluate", return_value="NO"):

            is_scarcity = instruments.is_scarcity_asset("AAPL", "Technology")
            self.assertFalse(is_scarcity)
            self.assertEqual(instruments._scarcity_mem.get("AAPL"), False)


if __name__ == "__main__":
    unittest.main()
