"""Earnings history from SEC 8-K item 2.02: acceptance time, the first session, the filter and the build."""
import json
import os
import tempfile
import unittest
from unittest import mock

from aether import ai_buildout
from aether import earnings_history as eh

SESSIONS = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"]


class TestTimes(unittest.TestCase):
    def test_accepted_et_handles_daylight_saving(self):
        self.assertEqual(eh.accepted_et("2026-10-29T11:02:04.000Z").isoformat(), "2026-10-29T07:02:04-04:00")
        self.assertEqual(eh.accepted_et("2026-11-03T21:05:00.000Z").isoformat(), "2026-11-03T16:05:00-05:00")
        self.assertIsNone(eh.accepted_et("garbage"))
        self.assertIsNone(eh.accepted_et(None))

    def test_first_session(self):
        before_open = eh.accepted_et("2026-10-29T11:02:04.000Z")      # 07:02 ET
        at_open = eh.accepted_et("2026-10-29T13:30:00.000Z")          # 09:30 ET
        after_close = eh.accepted_et("2026-10-29T20:05:00.000Z")      # 16:05 ET
        friday_evening = eh.accepted_et("2026-10-30T20:10:00.000Z")
        self.assertEqual(eh.first_session(before_open, SESSIONS), "2026-10-29")
        self.assertEqual(eh.first_session(at_open, SESSIONS), "2026-10-30")
        self.assertEqual(eh.first_session(after_close, SESSIONS), "2026-10-30")
        self.assertEqual(eh.first_session(friday_evening, SESSIONS), "2026-11-02")
        self.assertIsNone(eh.first_session(eh.accepted_et("2026-11-03T22:00:00.000Z"), SESSIONS))


def _page(rows):
    keys = ("form", "items", "filingDate", "acceptanceDateTime", "accessionNumber")
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


class TestEvents(unittest.TestCase):
    def test_filter_dedupe_and_order(self):
        recent = _page([
            ("8-K", "2.02,9.01", "2026-10-29", "2026-10-29T11:02:04.000Z", "a1"),
            ("8-K", "5.02", "2026-10-20", "2026-10-20T20:00:00.000Z", "a2"),        # not a report
            ("8-K/A", "2.02", "2026-10-30", "2026-10-30T12:00:00.000Z", "a3"),      # amendment
            ("10-Q", "", "2026-10-30", "2026-10-30T12:00:00.000Z", "a4"),
            ("8-K", "12.02", "2026-10-21", "2026-10-21T12:00:00.000Z", "a5"),       # not item 2.02
        ])
        older = _page([("8-K", "2.02", "2026-10-28", "2026-10-28T20:30:00.000Z", "a6"),
                       ("8-K", "2.02,9.01", "2026-10-29", "2026-10-29T11:02:04.000Z", "a1")])   # duplicate
        ev = eh.events_from_pages([recent, older], SESSIONS)
        self.assertEqual([(e["filed"], e["first_session"]) for e in ev],
                         [("2026-10-28", "2026-10-29"), ("2026-10-29", "2026-10-29")])
        self.assertEqual(ev[1]["accepted_et"], "2026-10-29T07:02:04-04:00")

    def test_missing_items_column(self):
        self.assertEqual(eh.events_from_pages([{"form": ["8-K"]}], SESSIONS), [])

    def test_page_name_is_validated(self):
        with self.assertRaises(ValueError):
            ai_buildout.submissions_file("../etc/passwd")
        with self.assertRaises(ValueError):
            ai_buildout.submissions_file("CIK1/x.json")


class TestBuild(unittest.TestCase):
    def test_build_lists_what_it_could_not_date(self):
        pages = {1: {"filings": {"recent": _page([("8-K", "2.02", "2026-10-29", "2026-10-29T11:02:04.000Z", "x")]),
                                 "files": [{"name": "CIK0000000001-submissions-001.json"}]}},
                 2: {"filings": {"recent": _page([("10-K", "", "2026-02-01", "2026-02-01T12:00:00.000Z", "y")])}},
                 3: None}
        older = _page([("8-K", "2.02", "2026-10-28", "2026-10-28T20:30:00.000Z", "z")])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(eh, "trading_sessions", return_value=SESSIONS), \
             mock.patch.object(ai_buildout, "ticker_cik_map", return_value={"AAA": 1, "BBB": 2, "CCC": 3, "BRK-B": 4}), \
             mock.patch.object(ai_buildout, "submissions", side_effect=lambda cik, ttl_hours=0: pages.get(cik)), \
             mock.patch.object(ai_buildout, "submissions_file", return_value=older) as sf:
            out_path = os.path.join(tmp, "h.json")
            data = eh.build(["AAA", "BBB", "CCC", "ZZZ"], path=out_path)
            with open(out_path, encoding="utf-8") as f:
                saved = json.load(f)
        sf.assert_called_once_with("CIK0000000001-submissions-001.json")
        self.assertEqual([e["filed"] for e in data["symbols"]["AAA"]["events"]], ["2026-10-28", "2026-10-29"])
        self.assertEqual((data["no_reports"], data["fetch_failed"], data["no_cik"]), (["BBB"], ["CCC"], ["ZZZ"]))
        self.assertEqual(saved["symbols"].keys(), data["symbols"].keys())
        st = eh.status(saved)
        self.assertEqual((st["symbols_with_reports"], st["events"], st["earliest"]), (1, 2, "2026-10-28"))
        self.assertTrue(st["complete"])

    def test_stale_blocked_and_missing_pages_are_reported(self):
        sub = {"filings": {"recent": _page([("8-K", "2.02", "2026-10-29", "2026-10-29T11:02:04.000Z", "x")]),
                           "files": [{"name": "CIK0000000001-submissions-001.json"}]}}
        with tempfile.TemporaryDirectory() as tmp,              mock.patch.object(eh, "trading_sessions", return_value=SESSIONS),              mock.patch.object(ai_buildout, "ticker_cik_map", return_value={"AAA": 1}),              mock.patch.object(ai_buildout, "submissions", return_value=sub),              mock.patch.object(ai_buildout, "submissions_file", return_value=None),              mock.patch.object(ai_buildout, "stale_served", return_value=frozenset({"sub_1.json"})),              mock.patch.object(ai_buildout, "sec_blocked", return_value=True):
            data = eh.build(["AAA"], path=os.path.join(tmp, "h.json"))
        self.assertEqual((data["stale"], data["incomplete"], data["sec_blocked"]), (["AAA"], ["AAA"], True))
        self.assertFalse(eh.status(data)["complete"])

    def test_dotted_ticker_maps_to_the_sec_dash_form(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(eh, "trading_sessions", return_value=SESSIONS), \
             mock.patch.object(ai_buildout, "ticker_cik_map", return_value={"BRK-B": 4}), \
             mock.patch.object(ai_buildout, "submissions", return_value={"filings": {"recent": {}}}) as sub:
            eh.build(["BRK.B"], path=os.path.join(tmp, "h.json"))
        sub.assert_called_once_with(4, ttl_hours=24 * 7)


if __name__ == "__main__":
    unittest.main()
