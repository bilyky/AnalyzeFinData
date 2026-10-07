"""
Tests for the RapidAPI per-run fetch budget (rapidapi.repair_missing).

The recovery pass used to walk the symbol list in a fixed order; once the plan's quota ran
out the same tail of ~23 symbols got 429 every night and was never repaired. Pins:
  - at most `max_fetches` API calls per run (default CFG.rapidapi_max_fetches = 400);
  - most-starved first: needing symbols are ordered by their newest real bar, oldest first;
  - rotation: two consecutive runs with a budget below the need cover everyone;
  - QUOTA_STOP_AFTER consecutive 429s stop the run; other errors do not.
All HTTP is mocked; sleeps are zeroed; files live in a temp dir.
"""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import daily_task
import rapidapi
from aether.config import CFG

TODAY = "2026-10-05"


def _bar(px, vol=1000, provisional=False):
    b = {"1. open": str(px), "2. high": str(px + 1), "3. low": str(px - 1),
         "4. close": str(px), "5. volume": str(vol)}
    if provisional:
        b.update({"2. high": str(px), "3. low": str(px), "5. volume": "0", "provisional": True})
    return b


def _http_429():
    resp = requests.Response()
    resp.status_code = 429
    return requests.HTTPError("429 Client Error: Too Many Requests", response=resp)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "Symbol_full")   # lock lands in the tempdir
        os.makedirs(self.dir)
        self._orig = rapidapi.OHLCV_DIR
        rapidapi.OHLCV_DIR = self.dir
        self.addCleanup(lambda: setattr(rapidapi, "OHLCV_DIR", self._orig))
        self.fetched = []

    def seed(self, sym, last_real):
        """A series whose newest real bar is `last_real`, then today's placeholder."""
        ts = {last_real: _bar(10), TODAY: _bar(10, provisional=True)}
        with open(os.path.join(self.dir, f"{sym}_daily.json"), "w", encoding="utf-8") as f:
            json.dump({"Meta Data": {}, "Time Series (Daily)": ts}, f)

    def _settle(self, sym, path, outputsize="compact"):
        """Stand-in for _fetch_and_merge: records the call and settles today's bar."""
        self.fetched.append(sym)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["Time Series (Daily)"][TODAY] = _bar(11)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def run_pass(self, syms, side_effect=None, **kw):
        with mock.patch.object(rapidapi, "SLEEP_SEC", 0), \
             mock.patch.object(rapidapi, "_fetch_and_merge", side_effect=side_effect or self._settle):
            return rapidapi.repair_missing(syms, TODAY, **kw)


class TestBudget(_Base):
    def setUp(self):
        super().setUp()
        start = datetime.date(2026, 9, 1)
        # Listed order A..E, but staleness reversed: E is the most starved.
        self.syms = ["A", "B", "C", "D", "E"]
        for k, sym in enumerate(self.syms):
            self.seed(sym, (start + datetime.timedelta(days=10 - 2 * k)).isoformat())

    def test_default_budget_is_400(self):
        self.assertEqual(CFG.rapidapi_max_fetches, 400)

    def test_caps_fetches_and_counts_deferred(self):
        res = self.run_pass(self.syms, max_fetches=2)
        self.assertEqual(len(self.fetched), 2)
        self.assertEqual((res["updated"], res["deferred"]), (2, 3))

    def test_most_starved_first_not_list_order(self):
        self.run_pass(self.syms, max_fetches=2)
        self.assertEqual(self.fetched, ["E", "D"])

    def test_two_runs_cover_everyone(self):
        self.run_pass(self.syms, max_fetches=3)
        first = list(self.fetched)
        res = self.run_pass(self.syms, max_fetches=3)
        self.assertEqual(sorted(self.fetched), self.syms)       # nobody starved
        self.assertEqual(len(self.fetched) - len(first), 2)      # 2nd run did the remainder
        self.assertEqual(res["deferred"], 0)

    def test_dormant_symbol_goes_last(self):
        self.seed("DEAD", "2023-06-30")                          # delisted years ago
        self.syms.insert(0, "DEAD")
        self.run_pass(self.syms, max_fetches=5)
        self.assertNotIn("DEAD", self.fetched)                   # budget went to live names
        self.run_pass(["DEAD"], max_fetches=5)
        self.assertEqual(self.fetched[-1], "DEAD")               # still retried when budget allows

    def test_api_error_file_goes_last(self):
        with open(os.path.join(self.dir, "GONE_daily.json"), "w", encoding="utf-8") as f:
            json.dump({"Error Message": "Invalid API call."}, f)
        self.syms.insert(0, "GONE")
        self.run_pass(self.syms, max_fetches=5)
        self.assertNotIn("GONE", self.fetched)

    def test_missing_file_is_most_starved(self):
        self.syms.append("NEW")                                  # no file -> full fetch first
        self.run_pass(self.syms, max_fetches=1)
        self.assertEqual(self.fetched, ["NEW"])


class TestQuotaStop(_Base):
    def setUp(self):
        super().setUp()
        self.syms = [f"S{k}" for k in range(8)]
        for sym in self.syms:
            self.seed(sym, "2026-09-20")

    def test_consecutive_429s_stop_the_run(self):
        def quota(sym, path, outputsize="compact"):
            self.fetched.append(sym)
            raise _http_429()
        res = self.run_pass(self.syms, side_effect=quota, max_fetches=8)
        self.assertEqual(len(self.fetched), rapidapi.QUOTA_STOP_AFTER)
        self.assertTrue(res["quota_stopped"])
        self.assertEqual(res["deferred"], 8 - rapidapi.QUOTA_STOP_AFTER)

    def test_other_errors_do_not_stop(self):
        def broken(sym, path, outputsize="compact"):
            self.fetched.append(sym)
            raise RuntimeError("Alpha Vantage error: Invalid API call")
        res = self.run_pass(self.syms, side_effect=broken, max_fetches=8)
        self.assertEqual(len(self.fetched), 8)
        self.assertFalse(res["quota_stopped"])

    def test_success_resets_the_429_streak(self):
        calls = {"n": 0}

        def flaky(sym, path, outputsize="compact"):
            calls["n"] += 1
            self.fetched.append(sym)
            if calls["n"] % 3 == 0:                               # every 3rd call succeeds
                return
            raise _http_429()
        res = self.run_pass(self.syms, side_effect=flaky, max_fetches=8)
        self.assertEqual(len(self.fetched), 8)
        self.assertFalse(res["quota_stopped"])



class TestPassTimeout(_Base):
    """The caller's kill timeout must cover the whole budget: PROD's daily_task killed every
    pass at 600 s (~42 of ~500 symbols), which is what starved the rest of the universe."""

    def test_timeout_covers_every_budgeted_fetch_worst_case(self):
        t = rapidapi.pass_timeout_seconds(400)
        self.assertGreaterEqual(t, 400 * (rapidapi.SLEEP_SEC + rapidapi.REQUEST_TIMEOUT))
        self.assertGreater(t, 600)
        self.assertEqual(rapidapi.pass_timeout_seconds(), rapidapi.pass_timeout_seconds(CFG.rapidapi_max_fetches))

    def test_daily_task_passes_the_budgeted_timeout(self):
        # The recovery call sits deep inside daily_task's run flow; pin the call site so a
        # revert to the 600 s default cannot slip back in unnoticed.
        with open(daily_task.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('run_command([sys.executable, "rapidapi.py"], timeout=rapidapi.pass_timeout_seconds())', src)

    def test_lock_younger_than_a_pass_is_respected(self):
        lock = os.path.join(os.path.dirname(self.dir), "rapidapi.lock")
        with open(lock, "w"):
            pass
        old = time.time() - 9001                                  # past the OLD 2.5 h TTL
        os.utime(lock, (old, old))
        self.seed("A", "2026-09-20")
        with self.assertLogs("aether.rapidapi", level="WARNING") as logs:
            res = self.run_pass(["A"], max_fetches=400)
        self.assertTrue(res.get("locked"))                        # a 400-fetch pass can still be live
        self.assertEqual(self.fetched, [])
        # A skip is explained (a killed pass leaves its lock), never silent.
        self.assertTrue(any("killed pass" in m and "min old" in m for m in logs.output))

    def test_lock_older_than_a_pass_is_reclaimed(self):
        lock = os.path.join(os.path.dirname(self.dir), "rapidapi.lock")
        with open(lock, "w"):
            pass
        old = time.time() - rapidapi.pass_timeout_seconds(400) - 60
        os.utime(lock, (old, old))
        self.seed("A", "2026-09-20")
        with mock.patch.object(rapidapi.trash, "soft_delete", side_effect=lambda p, **k: os.remove(p)):
            res = self.run_pass(["A"], max_fetches=400)
        self.assertFalse(res.get("locked"))
        self.assertEqual(self.fetched, ["A"])


if __name__ == "__main__":
    unittest.main()
