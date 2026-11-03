"""The email pick lists (aether/pick_lists.py) and their scorecard (aether/pick_eval.py).

PROD 11-02: the 05:30 Top 5 had one pick (HESM, S10 -2.7) because its R:R floor had no elite
waiver and a leader at new highs reads ~0 reward/risk to its own resistance; its "A-Reserves"
were a hard-coded EIX, AMAT, URI, VLO, RS; and no list's picks were ever scored.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import openpyxl

import workbook_read
from aether import pick_eval, pick_lists


def _row(sym, s10, l60, setup="1", price=100.0, stop=90.0, target=101.0, pgr="Bu", br=1.0):
    return {"symbol": sym, "s10": s10, "l60": l60, "setup": setup, "price": price, "stop": stop,
            "target": target, "pgr": pgr, "br": br}


def _book(path, rows):
    """A Research sheet in the column layout workbook_read reads (D symbol, G PGR, J stop,
    K price, L target, U setup, V BR, Y S10, Z L60)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Research"
    ws.append(["hdr"] * 27)
    for r in rows:
        line = [None] * 27
        line[3], line[6], line[9], line[10], line[11] = r["symbol"], r["pgr"], r["stop"], r["price"], r["target"]
        line[20], line[21], line[24], line[25] = r["setup"], r["br"], r["s10"], r["l60"]
        ws.append(line)
    wb.save(path)


class TestMorningLists(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.book = Path(self._td.name) / "state_of_the_day.xlsx"
        p = mock.patch.object(workbook_read, "XLSX_FILE", self.book)
        p.start()
        self.addCleanup(p.stop)
        for name in ("calculate_atr", "get_atr_position_size", "get_position_size"):
            q = mock.patch.object(workbook_read.risk_utils, name, return_value=1)
            q.start()
            self.addCleanup(q.stop)

    def test_leaders_at_new_highs_qualify_through_the_elite_waiver(self):
        # LEAD: S10 8.7, total 17.9, target 1% above price (R:R 0.1) -> elite, admitted.
        # LAG: S10 -2.7 with a distant target (R:R 2) -> admitted on R:R, ranked below LEAD.
        _book(self.book, [_row("LEAD", 8.7, 9.2), _row("LAG", -2.7, 1.4, target=120.0),
                          _row("WEAK", 1.0, 1.0), _row("NOSETUP", 9.0, 9.0, setup="0")])
        self.assertEqual([p["Symbol"] for p in workbook_read.get_top_5_picks()], ["LEAD", "LAG"])

    def test_reserves_are_the_next_five_not_a_fixed_list(self):
        _book(self.book, [_row(f"S{i:02d}", 3.0 + i, 6.0, target=200.0) for i in range(12)])
        top = [p["Symbol"] for p in workbook_read.get_top_5_picks()]
        res = [p["Symbol"] for p in workbook_read.get_reserves_data()]
        self.assertEqual(top, ["S11", "S10", "S09", "S08", "S07"])
        self.assertEqual(res, ["S06", "S05", "S04", "S03", "S02"])
        self.assertNotIn("EIX", res)


class TestEveningLists(unittest.TestCase):
    def test_pgr_bullish_lists_need_setup_and_are_empty_rather_than_relaxed(self):
        rows = [_row("A", 5, 5, setup=False), _row("B", 4, 4, setup=False, pgr="Bu+")]
        self.assertEqual(pick_lists.EVENING_COMBINED.pick(rows), [])        # no silent fallback
        rows.append(_row("C", 1, 1, setup=True))
        self.assertEqual([r["symbol"] for r in pick_lists.EVENING_COMBINED.pick(rows)], ["C"])

    def test_combined_ranks_by_s10_plus_br(self):
        rows = [_row("X", 5, 9, br=0.0, setup=True), _row("Y", 4, 0, br=3.0, setup=True)]
        self.assertEqual([r["symbol"] for r in pick_lists.EVENING_COMBINED.pick(rows)], ["Y", "X"])

    def test_every_list_has_criteria_text(self):
        for pl in pick_lists.ALL:
            with self.subTest(pl.key):
                text = pick_lists.criteria(pl)
                self.assertNotIn("{", text)
                self.assertGreater(len(text), 20)


def _series(closes, start="2026-09-01"):
    import datetime
    d = datetime.date.fromisoformat(start)
    out = {}
    for c in closes:
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        out[d.isoformat()] = {"1. open": str(c), "2. high": str(c * 1.01), "3. low": str(c * 0.99),
                              "4. close": str(c), "5. volume": "1000"}
        d += datetime.timedelta(days=1)
    return out


class TestScorecard(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.dir = Path(self._td.name)
        (self.dir / "Symbol_full").mkdir()
        p = mock.patch.dict(os.environ, {"AETHER_CACHE_DIR": str(self.dir)})
        p.start()
        self.addCleanup(p.stop)

    def _write(self, sym, closes):
        with open(self.dir / "Symbol_full" / f"{sym}_daily.json", "w") as f:
            json.dump({"Time Series (Daily)": _series(closes)}, f)

    def test_entry_basis_and_excess_over_spy(self):
        self._write("SPY", [100] * 30)
        self._write("AAA", [100, 110] + [121] * 28)          # 09-01 100, 09-02 110, then 121
        rows = [{"date": "2026-09-02", "list": "L", "symbol": "AAA", "entry": "prior_close"},
                {"date": "2026-09-02", "list": "M", "symbol": "AAA", "entry": "same_close"}]
        card = pick_eval.scorecard(rows, horizons=(5,))
        self.assertAlmostEqual(card["L"][5]["mean_excess"], 21.0)   # from 09-01's close, 100
        self.assertAlmostEqual(card["M"][5]["mean_excess"], 10.0)   # from 09-02's close, 110

    def test_dates_are_averaged_before_the_mean(self):
        self._write("SPY", [100] * 30)
        self._write("UP", [100] + [110] * 29)
        self._write("FLAT", [100] * 30)
        rows = [{"date": "2026-09-02", "list": "L", "symbol": s, "entry": "prior_close"} for s in ("UP", "FLAT", "FLAT", "FLAT")]
        rows.append({"date": "2026-09-03", "list": "L", "symbol": "UP", "entry": "prior_close"})
        m = pick_eval.scorecard(rows, horizons=(5,))["L"][5]
        self.assertEqual((m["picks"], m["dates"]), (5, 2))
        # 09-02: mean(10, 0, 0, 0) = 2.5; 09-03: UP from 110 -> 110 = 0  =>  (2.5 + 0) / 2
        self.assertAlmostEqual(m["mean_excess"], 1.25)

    def test_unmatured_picks_are_not_scored(self):
        self._write("SPY", [100] * 8)
        self._write("AAA", [100] * 8)
        rows = [{"date": "2026-09-08", "list": "L", "symbol": "AAA", "entry": "same_close"}]
        self.assertEqual(pick_eval.scorecard(rows, horizons=(5,)), {})

    def test_a_split_is_not_a_loss(self):
        self._write("SPY", [100] * 30)
        self._write("SPL", [400] * 5 + [100] * 25)             # 4:1 split on day 5
        rows = [{"date": "2026-09-02", "list": "L", "symbol": "SPL", "entry": "prior_close"}]
        self.assertAlmostEqual(pick_eval.scorecard(rows, horizons=(10,))["L"][10]["mean_excess"], 0.0)


class TestLog(unittest.TestCase):
    def test_last_run_of_the_day_wins_and_legacy_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as td:
            log, legacy = os.path.join(td, "picks.jsonl"), os.path.join(td, "perf.json")
            rows = [_row("AAA", 5, 5), _row("BBB", 4, 4)]
            pick_eval.log_picks(pick_lists.MORNING_TOP5, rows, day="2026-09-02", path=log)
            pick_eval.log_picks(pick_lists.MORNING_TOP5, rows[1:], day="2026-09-02", path=log)   # re-run
            pick_eval.log_picks(pick_lists.MORNING_TOP5, [{"Symbol": "ccc", "S10": 1, "L60": 2}],
                                day="2026-09-03", path=log)
            with open(legacy, "w") as f:
                json.dump({"2026-09-02": [{"symbol": "OLD"}], "2026-09-01": [{"symbol": "KEEP"}]}, f)
            got = sorted((r["date"], r["list"], r["symbol"]) for r in pick_eval.read_logged(log, legacy))
        self.assertEqual(got, [("2026-09-01", "morning_top5_legacy", "KEEP"),
                               ("2026-09-02", "morning_top5", "BBB"),
                               ("2026-09-03", "morning_top5", "CCC")])

    def test_render_has_a_row_per_list(self):
        card = {"morning_top5": {5: {"picks": 10, "dates": 2, "mean_ret": 1.0, "mean_excess": 0.5, "beat_spy_dates": 0.5}}}
        html = pick_eval.render_html(card, {"morning_top5": "Top High-Probability Setups"})
        self.assertIn("Top High-Probability Setups", html)
        self.assertIn("+0.50%", html)
        self.assertIn("No logged pick", pick_eval.render_html({}))


if __name__ == "__main__":
    unittest.main()


class TestEveningReport(unittest.TestCase):
    """daily_task.main() end to end (subprocess, data and email mocked, as test_daily_task_step_failures)."""
    from tests.test_daily_task_step_failures import TestMainReportsStepFailures as _Harness
    setUp, _run = _Harness.setUp, _Harness._run

    def _body(self, rows):
        import daily_task
        from aether import ledgers
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(ledgers, "PICK_LOG_FILE", os.path.join(td, "picks.jsonl")), \
             mock.patch.object(daily_task, "get_all_data", return_value=rows):
            daily_task.main()
            logged = []
            if os.path.exists(ledgers.PICK_LOG_FILE):
                with open(ledgers.PICK_LOG_FILE) as f:
                    logged = [json.loads(line) for line in f]
        return self.emails[-1][1], logged

    def test_lists_show_their_criteria_log_their_picks_and_the_scorecard(self):
        body, logged = self._body([{"symbol": "AAA", "pgr": "Bu", "setup": True, "s10": 5.0, "br": 2.0, "l60": 3.0}])
        for pl in (pick_lists.EVENING_COMBINED, pick_lists.EVENING_S10, pick_lists.RAW_L60):
            self.assertIn(pick_lists.criteria(pl), body)
        self.assertIn("How past picks did", body)
        self.assertEqual({r["list"] for r in logged},
                         {"evening_s10_br", "evening_s10", "evening_l60", "raw_s10", "raw_l60"})

    def test_no_setup_means_an_empty_bullish_list_not_a_relaxed_one(self):
        body, logged = self._body([{"symbol": "AAA", "pgr": "Bu", "setup": False, "s10": 5.0, "br": 2.0, "l60": 3.0}])
        self.assertIn("None qualified today.", body)
        self.assertEqual({r["list"] for r in logged}, {"raw_s10", "raw_l60"})


class TestMorningEmail(unittest.TestCase):
    def test_morning_report_shows_both_criteria_and_an_empty_reserves_row(self):
        import autonomous_pipeline as ap
        with mock.patch.object(ap, "get_reserves_data", return_value=[]), \
             mock.patch.object(ap, "get_reasoning", return_value=""):
            html = ap.format_html_report("ok", [], [], [])
        import html as _html
        self.assertIn(_html.escape(pick_lists.criteria(pick_lists.MORNING_TOP5)), html)
        self.assertIn(_html.escape(pick_lists.criteria(pick_lists.MORNING_RESERVES)), html)
        self.assertIn("No reserves qualified today.", html)
