"""Tests for scripts/utils/pgr_factor_repair.py: the factor-coverage audit, the gap marker,
the latest-session check and the today-only re-fetch (with a fake fetch, no network)."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "utils"))
import pgr_factor_repair as rep  # noqa: E402


def _bundle(factors=True, status="ok", etf=False):
    groups = [{g: ([{"Value": 3}] if factors else [])} for g in rep.GROUPS]
    return {"status": status, "pgr": [{"PGR Value": 3}, *groups, {"Corrected PGR Value": 3},
                                      {"is_etf_symbol": etf}],
            "metaInfo": [{"is_etf_symbol": etf}]}


def _write(base, sym, day, bundle):
    os.makedirs(os.path.join(base, sym), exist_ok=True)
    with open(os.path.join(base, sym, f"{sym}_{day}.json"), "w", encoding="utf-8") as f:
        json.dump(bundle, f)


class TestHasFactors(unittest.TestCase):
    def test_cases(self):
        self.assertTrue(rep.has_factors(_bundle()))
        self.assertTrue(rep.has_factors(_bundle(status="success")))      # legacy snapshot
        self.assertFalse(rep.has_factors(_bundle(factors=False)))
        self.assertIsNone(rep.has_factors(_bundle(factors=False, etf=True)))
        self.assertIsNone(rep.has_factors({"status": "invalid symbol"}))
        self.assertIsNone(rep.has_factors(None))
        one_group = _bundle(factors=False)
        one_group["pgr"][1] = {"Financials": [{"Value": 3}]}            # any group counts
        self.assertTrue(rep.has_factors(one_group))


class TestAudit(unittest.TestCase):
    def test_coverage_gap_days_and_check(self):
        with tempfile.TemporaryDirectory() as base:
            for s in ("AAA", "BBB", "CCC"):
                _write(base, s, "2026-08-31", _bundle())                    # Monday: all good
                _write(base, s, "2026-09-01", _bundle(factors=False))       # the bug
            _write(base, "ETF", "2026-09-01", _bundle(factors=False, etf=True))
            _write(base, "BBB", "2026-09-02", _bundle(factors=False))
            _write(base, "AAA", "2026-09-02", _bundle())
            rows = rep.audit(base, datetime.date(2026, 8, 29), datetime.date(2026, 9, 2))
            self.assertEqual([(r["date"], r["stocks"], r["with"]) for r in rows],
                             [("2026-08-31", 3, 3), ("2026-09-01", 3, 0), ("2026-09-02", 2, 1)])
            self.assertEqual(rep.gap_days(rows, 0.9), ["2026-09-01", "2026-09-02"])
            self.assertEqual(rep.gap_days(rows, 0.5), ["2026-09-01"])

    def test_check_exit_code_and_marker(self):
        today = datetime.date.today().isoformat()
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as data:
            _write(base, "AAA", today, _bundle(factors=False))
            with mock.patch.object(rep.paths, "symbol_dir", return_value=base), \
                    mock.patch.object(rep.paths, "data_dir", return_value=data), \
                    mock.patch.object(rep.market_calendar, "is_session", return_value=True), \
                    mock.patch.object(rep, "_out"):
                self.assertEqual(rep.main(["--check"]), 1)
                _write(base, "AAA", today, _bundle())
                self.assertEqual(rep.main(["--check"]), 0)
                _write(base, "AAA", today, _bundle(factors=False))
                rep.main(["--since", today, "--mark"])
            with open(os.path.join(data, "pgr_factor_gap.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["gap_sessions"], [today])


class TestCheckWindow(unittest.TestCase):
    def _main(self, base, data, argv):
        with mock.patch.object(rep.paths, "symbol_dir", return_value=base),                 mock.patch.object(rep.paths, "data_dir", return_value=data),                 mock.patch.object(rep.market_calendar, "is_session", return_value=True),                 mock.patch.object(rep, "_out"):
            return rep.main(argv)

    def test_check_looks_at_the_latest_session(self):
        today = datetime.date.today()
        older = (today - datetime.timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as data:
            _write(base, "AAA", older, _bundle())                          # older: full
            _write(base, "AAA", today.isoformat(), _bundle(factors=False))  # latest: empty
            self.assertEqual(self._main(base, data, ["--check"]), 1)

    def test_check_window_reaches_back_a_week(self):
        three_ago = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()   # e.g. over a weekend
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as data:
            _write(base, "AAA", three_ago, _bundle())
            self.assertEqual(self._main(base, data, ["--check"]), 0)


class TestRefetchToday(unittest.TestCase):
    def test_dry_run_then_apply_with_backup(self):
        today = datetime.date.today().isoformat()
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as data:
            _write(base, "AAA", today, _bundle(factors=False))
            _write(base, "BBB", today, _bundle(factors=False))
            _write(base, "CCC", today, _bundle())
            calls = []

            def fake_fetch(sym, day):
                calls.append(sym)
                if sym == "AAA":                       # BBB: the API still sends no factors
                    _write(base, sym, today, _bundle())

            dry = rep.refetch_today(base, ["AAA", "BBB", "CCC"], apply=False, fetch=fake_fetch)
            self.assertEqual((dry["todo"], calls), (["AAA", "BBB"], []))
            with mock.patch.object(rep.paths, "data_dir", return_value=data):
                r = rep.refetch_today(base, ["AAA", "BBB", "CCC"], apply=True, fetch=fake_fetch)
            self.assertEqual((r["fixed"], r["still_missing"], calls), (["AAA"], ["BBB"], ["AAA", "BBB"]))
            self.assertEqual(sorted(os.listdir(r["backup"])), [f"AAA_{today}.json", f"BBB_{today}.json"])
            with open(os.path.join(r["backup"], f"AAA_{today}.json"), encoding="utf-8") as f:
                self.assertFalse(rep.has_factors(json.load(f)))   # the backup holds the old file


if __name__ == "__main__":
    unittest.main()
