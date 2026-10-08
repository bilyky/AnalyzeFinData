"""watchdog.check_context_pack_health — the OceanView context pack health gate.

The evening daily_task rebuilds the pack; every build writes its verdict to
Data/<oceanview_context.STATUS_NAME>. The watchdog alerts on a "failed" verdict or when the
pack stops being rebuilt (status older than PACK_STATUS_STALE_H), and stays quiet when there
is no status yet (fresh deploy) or the pack is ok / degraded. Alerts go through the existing
per-alert daily throttle with their own subject.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import watchdog
from aether import oceanview_context


def _iso(hours_ago):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago)
    return t.isoformat(timespec="seconds")


class TestContextPackGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = mock.patch.dict(os.environ, {"AETHER_DATA_DIR": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def _status(self, health, hours_ago, warnings=()):
        with open(os.path.join(self._tmp.name, oceanview_context.STATUS_NAME), "w", encoding="utf-8") as f:
            json.dump({"generated_at": _iso(hours_ago), "health": health, "warnings": list(warnings),
                       "source": "cache", "broker_as_of": None, "staleness_hours": None}, f)

    def test_no_status_yet_is_quiet(self):
        self.assertEqual(watchdog.check_context_pack_health(), [])

    def test_fresh_ok_and_degraded_are_quiet(self):
        for health in ("ok", "degraded"):
            self._status(health, hours_ago=2)
            self.assertEqual(watchdog.check_context_pack_health(), [], health)

    def test_failed_is_critical_and_carries_the_reason(self):
        self._status("failed", hours_ago=2, warnings=["no cached broker snapshot"])
        alerts = watchdog.check_context_pack_health()
        self.assertEqual(len(alerts), 1)
        self.assertIn("CRITICAL", alerts[0])
        self.assertIn("no cached broker snapshot", alerts[0])

    def test_stale_status_warns(self):
        self._status("ok", hours_ago=watchdog.PACK_STATUS_STALE_H + 2)
        alerts = watchdog.check_context_pack_health()
        self.assertEqual(len(alerts), 1)
        self.assertIn("WARNING", alerts[0])

    def test_unreadable_timestamp_warns(self):
        with open(os.path.join(self._tmp.name, oceanview_context.STATUS_NAME), "w", encoding="utf-8") as f:
            json.dump({"generated_at": "not a date", "health": "ok"}, f)
        self.assertIn("unreadable", watchdog.check_context_pack_health()[0])

    def test_alert_email_uses_its_own_subject(self):
        self._status("failed", hours_ago=1)
        with mock.patch.object(watchdog.notify, "send_email") as send:
            watchdog.send_data_alerts(watchdog.check_context_pack_health(),
                                      subject="🛑 Project AETHER: OceanView context pack alert",
                                      heading="Project AETHER: OceanView context pack alert")
            watchdog.send_data_alerts(watchdog.check_context_pack_health(),
                                      subject="🛑 Project AETHER: OceanView context pack alert",
                                      heading="Project AETHER: OceanView context pack alert")
        self.assertEqual(send.call_count, 1)                      # same alert: once a day
        self.assertIn("OceanView context pack alert", send.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
