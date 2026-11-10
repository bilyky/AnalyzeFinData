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

    def test_long_term_starts_the_day_after_the_anniversary(self):
        lots = [{"acquired": "2025-03-10", "qty": 1, "price": 1.0}]
        self.assertEqual(ov.lots_table(lots, 2.0, D(2026, 3, 10))[0]["term"], "ST")      # the anniversary
        self.assertEqual(ov.lots_table(lots, 2.0, D(2026, 3, 11))[0]["term"], "LT")

    def test_calendar_window_edges(self):
        for day, status in ((D(2026, 10, 31), "now"), (D(2026, 12, 30), "now"),
                            (D(2026, 10, 30), "upcoming"), (D(2026, 12, 31), "past")):
            with self.subTest(day=day):
                self.assertEqual(ov.calendar(["2026-11-30"], day)[0]["status"], status)

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

    def test_research_shows_the_monthly_mean_the_verdict_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "insider_buy_study_2026-11-05_132039.json"), "w", encoding="utf-8") as f:
                json.dump({"verdict": "INCONCLUSIVE", "all": {"x60": {"n": 10, "mean": 0.05, "month_mean": -0.01,
                                                                       "median": 0.0, "t_nw": -0.3}}}, f)
            with open(os.path.join(tmp, "gap_drift_study.json"), "w", encoding="utf-8") as f:
                json.dump({"all": {"x60": {"n": 5, "mean": 0.02, "median": 0.01, "t_month": 0.4}}}, f)
            with mock.patch.object(ov.paths, "data_dir", return_value=tmp):
                r = ov.research()
        self.assertEqual([(x["mean_basis"], x["rows"][0]["mean"]) for x in r],
                         [("monthly mean", -0.01), ("per-event mean", 0.02)])

    def test_plan_check_without_the_account(self):
        days = ["2030-03-04", "2030-03-05"]
        bars = {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": [100.0, 101.0], "o": [100.0, 101.0],
                "h": [100.0, 101.0], "l": [100.0, 101.0], "v": [1.0, 1.0]}
        plan = {"name": "example", "total": 4000, "return_by": "2030-04-30",
                "steps": [{"date": "2030-03-04", "amount": 2000, "buy": "SPY"},
                          {"date": "2030-03-13", "amount": 2000, "buy": "RSP"}],
                "rules": {"index_stop": -0.08}, "fills": [{"date": "2030-03-04", "symbol": "SPY", "shares": 20,
                                                           "price": 100.0, "stop": None, "account": "x1234"}]}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ov.pw, "watch_dir", return_value=tmp):
                self.assertIsNone(ov.plan(D(2030, 3, 6)))                     # no plan file: no card
                with open(os.path.join(tmp, "invest_plan.json"), "w", encoding="utf-8") as f:
                    json.dump(plan, f)
                with mock.patch.object(ov.pw, "load_bars", return_value=bars):
                    p = ov.plan(D(2030, 3, 6))
                with mock.patch.object(ov.pw, "load_bars", return_value=None):
                    self.assertIsNone(ov.plan(D(2030, 3, 6)))                 # no SPY bars: no card
        self.assertEqual([s["state"] for s in p["steps"]], ["done", "later"])
        self.assertNotIn("account", p["fills"][0])
        self.assertAlmostEqual(p["pnl"], 20.0)
        self.assertEqual((p["name"], p["return_by"], p["actions"]), ("example", "2030-04-30", ["Nothing to do today."]))

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
        self.assertIn("plan", s)                                             # no plan file here: None
        self.assertIsNone(s["plan"])


class TestTrimDayAndFileShape(unittest.TestCase):
    def test_trim_day_lots_drop_the_account_and_dict_shaped_lots_file_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "INTC_lots.json"), "w", encoding="utf-8") as f:
                json.dump({"lots": [{"acquired": "2020-01-02", "qty": 2, "price": 10.0, "account": "x"}]}, f)
            rep = {"price": 20.0, "call": {"action": "TRIM"},
                   "lots": {"shares": 1, "est_tax": 1.0,
                            "lots": [{"acquired": "2020-01-02", "sell_qty": 1, "account": "Brokerage 1234"}]}}
            with mock.patch.object(ov.pw, "watch_dir", return_value=tmp), \
                    mock.patch.object(ov.pw, "load_config", return_value=dict(ov.pw.DEFAULTS)), \
                    mock.patch.object(ov.pw, "run", return_value=rep), \
                    mock.patch.object(ov, "research", return_value=[]):
                s = ov.summary()
        self.assertNotIn("account", s["today"]["lots"]["lots"][0])
        self.assertEqual(s["today"]["lots"]["lots"][0]["sell_qty"], 1)
        self.assertEqual(len(s["lots"]), 1)                                  # {"lots": [...]} file shape


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
        for bad in ("../X", "INTC_LOTS", "TOOLONGX"):
            with self.subTest(bad=bad), self.assertRaises(HTTPException) as cm:
                asyncio.run(fn(symbol=bad, authorization="Bearer " + server.make_token("u", "t")))
            self.assertEqual(cm.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
