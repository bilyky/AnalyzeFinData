"""Tests for oceanview_api.py (the OceanView tab's data) and its server route: lot table
(no account field, gain, term, days to long-term), the wash-sale calendar, the research
summary, summary() wiring (read-only position-watch run), and the login gate."""
import asyncio
import datetime
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from fastapi import HTTPException

import oceanview_api as ov
import server

D = datetime.date


class TestLotsAndCalendar(unittest.TestCase):
    def test_lots_table(self):
        lots = [{"acquired": "2025-11-10", "qty": 10, "price": 50.0, "account": "Brokerage 1234"},
                {"acquired": "2024-02-29", "qty": 5, "price": 70.0, "account": "Stock plan"}]
        t = ov.lots_table(lots, 60.0, D(2025, 12, 1))
        self.assertTrue(all("account" not in x for x in t))                  # never returned
        self.assertEqual([x["acquired"] for x in t], ["2024-02-29", "2025-11-10"])
        feb29, recent = t
        self.assertEqual((feb29["term"], feb29["gain"]), ("LT", -50.0))
        self.assertEqual((recent["term"], recent["gain"]), ("ST", 100.0))
        self.assertEqual(recent["days_to_lt"], (D(2026, 11, 11) - D(2025, 12, 1)).days)   # LT the day after the anniversary

    def test_calendar_windows(self):
        c = ov.calendar(["2026-11-30", "2026-08-19", "2027-02-19"], D(2026, 11, 5))
        self.assertEqual([(x["date"], x["status"]) for x in c],
                         [("2026-08-19", "past"), ("2026-11-30", "now"), ("2027-02-19", "upcoming")])
        self.assertEqual((c[1]["window_from"], c[1]["window_to"]), ("2026-10-31", "2026-12-30"))


class TestResearchAndSummary(unittest.TestCase):
    def test_research_reads_the_latest_study(self):
        with tempfile.TemporaryDirectory() as tmp:
            for day, verdict in (("2026-11-01", "OLD"), ("2026-11-05", "INCONCLUSIVE")):
                with open(os.path.join(tmp, f"insider_buy_study_{day}.json"), "w", encoding="utf-8") as f:
                    json.dump({"verdict": verdict, "verdict_drop": "INCONCLUSIVE",
                               "all": {"x60": {"n": 10, "mean": 0.01, "median": -0.01, "t_nw": 1.2}}}, f)
            with mock.patch.object(ov.paths, "data_dir", return_value=tmp):
                r = ov.research()
        self.assertEqual((len(r), r[0]["verdict"]), (1, "INCONCLUSIVE"))
        self.assertEqual(r[0]["rows"], [{"group": "all", "n": 10, "mean": 0.01, "median": -0.01, "t": 1.2}])

    def test_summary_runs_position_watch_without_writing_the_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "INTC_lots.json"), "w", encoding="utf-8") as f:
                json.dump([{"acquired": "2020-01-02", "qty": 1, "price": 10.0, "account": "x"}], f)
            with open(os.path.join(tmp, "INTC_journal.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"date": "2026-11-04", "action": "TRIM"}) + "\n")
            rep = {"price": 20.0, "call": {"action": "WAIT"}}
            with mock.patch.object(ov.pw, "watch_dir", return_value=tmp), \
                    mock.patch.object(ov.pw, "load_config", return_value={**ov.pw.DEFAULTS,
                                                                         "acquisition_dates": ["2026-11-30"]}), \
                    mock.patch.object(ov.pw, "run", return_value=rep) as run, \
                    mock.patch.object(ov, "research", return_value=[]):
                s = ov.summary()
        run.assert_called_once_with("INTC", live=False, journal=False)
        self.assertEqual((s["symbol"], s["today"], len(s["journal"])), ("INTC", rep, 1))
        self.assertEqual(s["lots"][0]["gain"], 10.0)
        self.assertNotIn("account", s["lots"][0])
        self.assertEqual(s["calendar"][0]["date"], "2026-11-30")


class TestRoute(unittest.TestCase):
    def test_summary_route_needs_the_login(self):
        cfg = types.SimpleNamespace(web_secret="t", admins=[])
        with mock.patch.object(server, "_cfg", return_value=cfg):
            app = server.create_app()
        fn = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/oceanview/summary")
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(fn(symbol="", authorization=""))
        self.assertEqual(cm.exception.status_code, 401)
        with mock.patch.object(server.oceanview_api, "summary", return_value={"ok": 1}) as summ:
            self.assertEqual(asyncio.run(fn(symbol="intc", authorization="Bearer " + server.make_token("u", "t"))),
                             {"ok": 1})
        summ.assert_called_once_with("INTC")


if __name__ == "__main__":
    unittest.main()
