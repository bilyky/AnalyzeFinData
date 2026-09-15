"""Eastern-timezone loading fallback (aether.etrade._load_eastern_tz).

PR #15 hardened the module-level `_ET` resolution against a missing OS tz database. Two
DST-aware tiers: ``zoneinfo`` (OS tzdata) first, pytz's bundled zone database as the
fallback. There is deliberately NO fixed UTC-5 tier — a fixed offset is EST year-round and
would be an hour wrong for the ~8 months Eastern is on EDT (and it is unreachable anyway,
since pytz bundles the zone). These tests force the zoneinfo tier to fail and assert the
fallback is (a) reached, (b) announced (not a silent downgrade), and (c) genuinely
DST-aware, not a frozen offset — which is exactly what the deleted fixed-EST tier got wrong.
"""
import datetime
import unittest
from unittest import mock

import aether.etrade as etrade


class TestEasternTimezoneFallback(unittest.TestCase):
    def test_zoneinfo_tier_used_when_available(self):
        # Happy path: zoneinfo resolves, so pytz is never consulted.
        with mock.patch.object(etrade, "pytz") as pytz_mock:
            tz = etrade._load_eastern_tz()
        pytz_mock.timezone.assert_not_called()
        # A real America/New_York tzinfo is DST-aware (EST in Jan, EDT in Jul).
        jan = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=tz)
        jul = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=tz)
        self.assertEqual(jan.utcoffset(), datetime.timedelta(hours=-5))
        self.assertEqual(jul.utcoffset(), datetime.timedelta(hours=-4))

    def test_falls_back_to_pytz_when_zoneinfo_missing(self):
        # Force the OS-tzdata tier to fail (stripped-down Docker image / minimal OS).
        with mock.patch.object(etrade, "ZoneInfo", side_effect=Exception("no tzdata")), \
                self.assertLogs("aether.etrade", level="WARNING") as cm:
            tz = etrade._load_eastern_tz()
        # The downgrade is announced, not silent (finding: no log on the pytz fallback).
        self.assertTrue(any("falling back to pytz" in m for m in cm.output))
        # pytz zone objects require .localize(); the fallback must stay DST-aware.
        jan = tz.localize(datetime.datetime(2026, 1, 15, 12, 0))
        jul = tz.localize(datetime.datetime(2026, 7, 15, 12, 0))
        self.assertEqual(jan.utcoffset(), datetime.timedelta(hours=-5))  # EST
        self.assertEqual(jul.utcoffset(), datetime.timedelta(hours=-4))  # EDT
        # Crucially NOT a fixed offset — the deleted UTC-5 tier would fail this assertion.
        self.assertNotEqual(jan.utcoffset(), jul.utcoffset())


if __name__ == "__main__":
    unittest.main()
