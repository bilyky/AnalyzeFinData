"""Tests for aether/data_completeness.py: the day-over-day completeness check of the Chaikin
snapshot cache. Synthetic snapshots in a temp dir; no network."""
import json
import os
import tempfile
import unittest
from unittest import mock

import daily_task
from aether import data_completeness as dc

# Mon 2026-08-24 .. Fri 2026-08-28, Mon 2026-08-31, Tue 2026-09-01 (all sessions).
DAYS = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01"]


def _snap(factors=True, signals="000000001000", pgr=4, strength=2):
    return {"status": "ok",
            "pgr": [{"PGR Value": pgr}, {"Financials": [{"Value": 3}] if factors else []},
                    {"Earnings": [{"Value": 3}] if factors else []}, {"Technicals": []}, {"Experts": []},
                    {"Corrected PGR Value": pgr}, {"is_etf_symbol": False}],
            "metaInfo": [{"Last": 10.0, "signals": signals}],
            "checklist_stocks": {"industry": "Weak", "strengthCount": strength}}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self._tmp.name, "Symbol")
        self.ohlcv = os.path.join(self._tmp.name, "Symbol_full")
        os.makedirs(self.ohlcv)
        self._p = [mock.patch.object(dc.paths, "ohlcv_dir", return_value=self.ohlcv),
                   mock.patch.object(dc.paths, "cache_dir", return_value=self._tmp.name)]
        for p in self._p:
            p.start()
        dc._bars_cache.clear()

    def tearDown(self):
        for p in self._p:
            p.stop()
        dc._bars_cache.clear()
        self._tmp.cleanup()

    def write(self, sym, day, snap):
        os.makedirs(os.path.join(self.base, sym), exist_ok=True)
        with open(os.path.join(self.base, sym, f"{sym}_{day}.json"), "w", encoding="utf-8") as f:
            json.dump(snap, f)

    def bars(self, sym, days):
        with open(os.path.join(self.ohlcv, f"{sym}_daily.json"), "w", encoding="utf-8") as f:
            json.dump({"Time Series (Daily)": {d: {"4. close": "1"} for d in days}}, f)

    def history(self, syms=("AAA", "BBB", "CCC", "DDD"), upto=6):
        for s in syms:
            self.bars(s, DAYS)
            for d in DAYS[:upto]:
                self.write(s, d, _snap())


class TestFields(unittest.TestCase):
    def test_fields_and_value_problems(self):
        f = dc.fields(_snap(factors=False))
        self.assertFalse(f["pgr.Financials"])
        self.assertTrue(f["pgr.PGR Value"])
        self.assertTrue(f["checklist.strengthCount"])
        self.assertTrue(dc.fields(_snap())["pgr.Financials.Value"])
        self.assertEqual(dc.value_problems(_snap()), [])
        self.assertEqual(dc.value_problems(_snap(pgr=-1)), [])                 # old-API unrated
        self.assertEqual(len(dc.value_problems(_snap(signals="-1-1-1-1-1-1-1-1-1-1-1-1", pgr=9))), 3)


class TestCheck(Base):
    def test_quiet_on_a_normal_day(self):
        self.history(upto=7)
        r = dc.check("2026-09-01", self.base)
        self.assertEqual((r["alarms"], r["snapshots"], r["baseline_sessions"]), ([], 4, 6))

    def test_the_2026_09_01_case_fields_go_empty(self):
        self.history()
        for s in ("AAA", "BBB", "CCC", "DDD"):
            self.write(s, "2026-09-01", _snap(factors=False))
        r = dc.check("2026-09-01", self.base)
        self.assertEqual(len(r["alarms"]), 1)
        self.assertIn("fields went empty", r["alarms"][0])
        self.assertIn("pgr.Financials 0% (usually 100%)", r["alarms"][0])

    def test_a_field_that_disappears_counts_as_empty(self):
        self.history()
        for s in ("AAA", "BBB", "CCC", "DDD"):
            snap = _snap()
            del snap["checklist_stocks"]["strengthCount"]
            self.write(s, "2026-09-01", snap)
        self.assertIn("checklist.strengthCount 0%", dc.check("2026-09-01", self.base)["alarms"][0])

    def test_fewer_snapshots_bad_values_and_missing_bars(self):
        self.history()
        self.write("AAA", "2026-09-01", _snap(signals="xx"))
        r = dc.check("2026-09-01", self.base)
        text = " | ".join(r["alarms"])
        self.assertIn("1 snapshots, usually 4", text)
        self.assertIn("bad values (AAA: signals 'xx')", text)
        self.bars("AAA", DAYS[:6])                                   # no bar for 09-01
        dc._bars_cache.clear()
        self.assertIn("no OHLCV bar", " | ".join(dc.check("2026-09-01", self.base)["alarms"]))

    def test_non_trading_day_and_raw_copy(self):
        self.history(upto=7)
        for s in ("AAA", "BBB", "CCC", "DDD"):
            self.write(s, "2026-09-07", _snap())                     # Labor Day 2026 (a Monday)
            self.write(s, "2026-09-05", _snap())                     # a Saturday
        holiday = dc.check("2026-09-07", self.base)
        self.assertEqual((holiday["alarms"], holiday["notes"]),
                         ([], ["4 snapshots on 2026-09-07, a weekday market holiday"]))
        self.write("AAA", "2026-09-07", _snap(signals="-1-1-1-1-1-1-1-1-1-1-1-1"))  # junk still alarms
        self.assertIn("bad values (AAA", dc.check("2026-09-07", self.base)["alarms"][0])
        empty = dc.check("2026-04-03", self.base)                   # Good Friday, nothing written
        self.assertEqual(empty["alarms"], [])
        self.assertEqual(dc.check("2026-09-08", self.base)["alarms"][0], "0 snapshots, usually 4")  # a session
        weekend = dc.check("2026-09-05", self.base)                  # no bars that day: no bar alarm
        self.assertEqual((weekend["alarms"], weekend["notes"]), ([], ["4 snapshots on 2026-09-05, a weekend day"]))
        os.makedirs(os.path.join(self._tmp.name, "Symbol_raw", "AAA"))
        open(os.path.join(self._tmp.name, "Symbol_raw", "AAA", "AAA_2026-09-01.json.gz"), "w").close()
        r = dc.check("2026-09-01", self.base)
        self.assertEqual(r["alarms"], ["3 snapshots without a raw copy (Data/Symbol_raw)"])

    def test_screen_symbols_missing_today_are_named(self):
        self.history(syms=("AAA", "BBB", "CCC", "DDD", "EEE"))
        for s in ("AAA", "BBB", "CCC", "DDD"):
            self.write(s, "2026-09-01", _snap())
        self.write("NEW", "2026-08-31", _snap())                       # in only 1 of 6 prior sessions
        r = dc.check("2026-09-01", self.base, expected=["aaa", "eee", "NEW", "NEVER"])
        self.assertEqual(r["alarms"], ["4 snapshots, usually 5", "1 screen symbols missing today: EEE"])
        self.assertEqual(dc.check("2026-09-01", self.base)["alarms"], ["4 snapshots, usually 5"])  # no list

    def test_holiday_without_snapshots_is_quiet(self):
        self.history(upto=7)                                         # sessions up to 09-01, a baseline
        r = dc.check("2026-09-07", self.base)                        # Labor Day, nothing written
        self.assertEqual((r["alarms"], r["baseline_sessions"]), ([], 7))

    def test_keeps_up_edge(self):
        syms = ("AAA", "BBB", "CCC", "DDD")
        self.history(syms=syms, upto=7)                             # 6 earlier sessions + 09-01

        def screen_alarms():
            r = dc.check("2026-09-01", self.base, expected=["EEE"])
            return [a for a in r["alarms"] if "screen symbols" in a]
        for d in DAYS[2:6]:                                         # EEE in 4 of 6 earlier sessions (67%)
            self.write("EEE", d, _snap())
        self.assertEqual(screen_alarms(), [])
        self.write("EEE", DAYS[1], _snap())                         # 5 of 6 = 83%, over KEEPS_UP 0.8
        self.assertEqual(screen_alarms(), ["1 screen symbols missing today: EEE"])

    def test_thresholds_at_their_edges(self):
        syms = [f"S{i:02d}" for i in range(10)]
        self.history(syms=syms)
        for s in syms[:9]:                                          # 9 of 10 = exactly COUNT_DROP
            self.write(s, "2026-09-01", _snap())
        self.assertEqual(dc.check("2026-09-01", self.base)["alarms"], [])
        os.remove(os.path.join(self.base, "S08", "S08_2026-09-01.json"))   # 8 of 10
        self.assertEqual(dc.check("2026-09-01", self.base)["alarms"], ["8 snapshots, usually 10"])

    def test_fill_rate_edges(self):
        syms = [f"S{i:02d}" for i in range(10)]
        for s in syms:
            self.bars(s, DAYS)
        for d in DAYS[:6]:                                          # strengthCount usually in 6/10 (60%)
            for i, s in enumerate(syms):
                self.write(s, d, _snap() if i < 6 else {**_snap(), "checklist_stocks": {"industry": "Weak"}})
        def today(n_with):
            for i, s in enumerate(syms):
                self.write(s, "2026-09-01", _snap() if i < n_with else
                           {**_snap(), "checklist_stocks": {"industry": "Weak"}})
            return dc.check("2026-09-01", self.base)["alarms"]
        self.assertEqual(today(3), [])                              # 30% = exactly half of 60%: no alarm
        two = today(2)
        self.assertEqual(len(two), 1)
        self.assertIn("checklist.strengthCount 20% (usually 60%)", two[0])
        for d in DAYS[:6]:                                          # usually 4/10 (40%): below USUAL_FILL
            for i, s in enumerate(syms):
                self.write(s, d, _snap() if i < 4 else {**_snap(), "checklist_stocks": {"industry": "Weak"}})
        self.assertEqual(today(0), [])

    def test_bar_share_edge_and_status_filter(self):
        syms = [f"S{i:02d}" for i in range(10)]
        self.history(syms=syms, upto=7)
        self.bars("S00", DAYS[:6])                                  # 1 of 10 without a bar = 10%: a note
        dc._bars_cache.clear()
        r = dc.check("2026-09-01", self.base)
        self.assertEqual((r["alarms"], len(r["notes"])), ([], 1))
        self.bars("S01", DAYS[:6])                                  # 2 of 10 = 20%: an alarm
        dc._bars_cache.clear()
        alarms = dc.check("2026-09-01", self.base)["alarms"]
        self.assertEqual(len(alarms), 1)
        self.assertIn("2 of 10 stocks have no OHLCV bar", alarms[0])
        self.write("S02", "2026-09-01", {"status": "invalid symbol"})   # not counted as a snapshot
        self.assertEqual(dc.check("2026-09-01", self.base)["snapshots"], 9)

    def test_banner_and_exit_code(self):
        self.assertEqual(dc.html_banner({"alarms": []}), "")
        html = dc.html_banner({"day": "2026-09-01", "alarms": ["x went empty"]})
        self.assertIn("DATA COMPLETENESS: 1 problem(s)", html)
        self.assertIn("<li>x went empty</li>", html)
        self.assertIn("&lt;script&gt;", dc.html_banner({"day": "d", "alarms": ["signals '<script>'"]}))
        with mock.patch.object(dc, "check", return_value={"day": "d", "alarms": ["a"], "notes": []}), \
                mock.patch.object(dc.sys.stdout, "write"):
            self.assertEqual(dc.main(["--date", "d"]), 1)
        with mock.patch.object(dc, "check", return_value={"day": "d", "alarms": [], "notes": []}), \
                mock.patch.object(dc.sys.stdout, "write"):
            self.assertEqual(dc.main([]), 0)


class TestDailyTaskBanner(unittest.TestCase):
    def test_banner_goes_into_the_email_and_subject(self):
        with mock.patch.object(daily_task, "_data_completeness_banner", return_value="<div>B</div>"):
            html, subj = daily_task._add_data_banner("<html><body><h2>R</h2></body></html>", "Daily", "d")
        self.assertEqual((html, subj), ("<html><body><div>B</div><h2>R</h2></body></html>", "[DATA] Daily"))
        with mock.patch.object(daily_task, "_data_completeness_banner", return_value=""):
            self.assertEqual(daily_task._add_data_banner("<body>x", "Daily", "d"), ("<body>x", "Daily"))
        with mock.patch.object(daily_task, "get_symbols_from_xls", return_value=["AAA"]),                 mock.patch.object(dc, "check", return_value={"day": "d", "alarms": []}) as chk:
            daily_task._data_completeness_banner("d")
        self.assertEqual(chk.call_args.kwargs["expected"], ["AAA"])

    def test_banner_in_the_evening_email_never_raises(self):
        with mock.patch.object(dc, "check", return_value={"day": "2026-09-01", "alarms": ["x went empty"]}):
            self.assertIn("x went empty", daily_task._data_completeness_banner("2026-09-01"))
        with mock.patch.object(dc, "check", return_value={"day": "2026-09-01", "alarms": []}):
            self.assertEqual(daily_task._data_completeness_banner("2026-09-01"), "")
        with mock.patch.object(dc, "check", side_effect=OSError("disk")),                 self.assertLogs("aether.daily_task", level="WARNING"):
            self.assertEqual(daily_task._data_completeness_banner("2026-09-01"), "")


if __name__ == "__main__":
    unittest.main()
