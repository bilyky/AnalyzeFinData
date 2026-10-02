"""Tests for aether/oceanview_context.py — the OceanView Context Pack assembler.

Pins the fail-safe contract: live -> ok + cache refresh; dead token -> cache -> degraded
(fresh) / failed (stale or none); the broker read never goes near the browser-capable
etrade.get_tokens; placeholder-heavy OHLCV on a held symbol degrades health.
"""
import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from aether import oceanview_context as ovc

NOW = datetime.datetime(2026, 10, 2, 15, 0, tzinfo=datetime.timezone.utc)


def _bar(px, vol=1000, provisional=False):
    b = {"1. open": str(px), "2. high": str(px + 1), "3. low": str(px - 1),
         "4. close": str(px), "5. volume": str(vol)}
    if provisional:
        b.update({"2. high": str(px), "3. low": str(px), "5. volume": "0", "provisional": True})
    return b


class _FakeAccounts:
    def list_accounts(self, resp_format="json"):
        return {"AccountListResponse": {"Accounts": {"Account": [
            {"accountId": "000123766", "accountIdKey": "K1", "accountDesc": "Brokerage"},
            {"accountId": "000111315", "accountIdKey": "K2", "accountDesc": "Margin"}]}}}

    def get_account_balance(self, key, resp_format="json"):
        val = {"K1": 274708.0, "K2": 41394.0}[key]
        return {"BalanceResponse": {"Computed": {"RealTimeValues": {"totalAccountValue": val},
                                                 "netCash": 1000.0}}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "Symbol_full"))
        with open(os.path.join(self.dir, "ai_portfolio_game.json"), "w", encoding="utf-8") as f:
            json.dump({"balance": 100.0, "equity": 200.0, "profile": "BALANCED",
                       "start_date": "2026-06-17", "positions": {"AAPL": {"qty": 1}},
                       "history": [{"type": "SELL", "date": "2026-09-01", "pnl": 5.0},
                                   {"type": "BUY", "date": "2026-09-02"}]}, f)
        self._series("AAPL", provisional_every=0)
        # Any call into the browser-capable token path is a ban-safety violation.
        self._no_mint = mock.patch.object(ovc.etrade, "get_tokens",
                                          side_effect=AssertionError("get_tokens must never be called"))
        self._no_mint.start()

    def tearDown(self):
        self._no_mint.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _series(self, sym, provisional_every):
        ts = {}
        day = datetime.date(2026, 8, 1)
        for i in range(40):
            ts[(day + datetime.timedelta(days=i)).isoformat()] = _bar(
                100, provisional=bool(provisional_every) and i % provisional_every == 0)
        with open(os.path.join(self.dir, "Symbol_full", f"{sym}_daily.json"), "w", encoding="utf-8") as f:
            json.dump({"Time Series (Daily)": ts}, f)

    def _live(self):
        return mock.patch.multiple(
            ovc.etrade, keep_alive=mock.DEFAULT, get_accounts=mock.DEFAULT, fetch_positions=mock.DEFAULT)

    def build(self, **kw):
        return ovc.build_oceanview_context(data_dir=self.dir, now=kw.pop("now", NOW), **kw)


class TestFailSafe(_Base):
    def test_live_success_is_ok_and_writes_cache(self):
        with self._live() as m:
            m["keep_alive"].return_value = {"oauth_token": "t"}
            m["get_accounts"].return_value = _FakeAccounts()
            m["fetch_positions"].return_value = [
                {"symbol": "AAPL", "qty": 1, "date_acquired": datetime.date(2026, 1, 2)}]
            pack = self.build()
        self.assertEqual((pack["meta"]["source"], pack["meta"]["health"]), ("live", "ok"))
        self.assertEqual(pack["meta"]["warnings"], [])
        sleeves = {a["account_last4"]: (a["sleeve"], a["net_value"]) for a in pack["state"]["accounts"]}
        self.assertEqual(sleeves, {"3766": ("overlay-anchor", 274708.0), "1315": ("active-margin", 41394.0)})
        self.assertEqual(pack["state"]["positions"][0]["date_acquired"], "2026-01-02")
        self.assertTrue(os.path.exists(os.path.join(self.dir, ovc.CACHE_NAME)))

    def test_dead_token_no_cache_is_failed(self):
        with mock.patch.object(ovc.etrade, "keep_alive", return_value=None):
            pack = self.build()
        self.assertEqual((pack["meta"]["source"], pack["meta"]["health"]), ("cache", "failed"))
        self.assertIsNone(pack["state"]["accounts"])
        self.assertTrue(any("keep_alive" in w for w in pack["meta"]["warnings"]))
        # Local sections still present so non-numeric explanation keeps working.
        self.assertEqual(pack["state"]["portfolio"]["closed_sells"], 1)
        self.assertTrue(pack["guardrails"])

    def _seed_cache(self, hours_old):
        as_of = (NOW - datetime.timedelta(hours=hours_old)).isoformat(timespec="seconds")
        with open(os.path.join(self.dir, ovc.CACHE_NAME), "w", encoding="utf-8") as f:
            json.dump({"broker_as_of": as_of, "broker": {"accounts": [{"account_last4": "3766"}],
                                                          "positions": []}}, f)

    def test_dead_token_fresh_cache_is_degraded(self):
        self._seed_cache(hours_old=5)
        with mock.patch.object(ovc.etrade, "keep_alive", return_value=None):
            pack = self.build()
        self.assertEqual((pack["meta"]["source"], pack["meta"]["health"]), ("cache", "degraded"))
        self.assertEqual(pack["meta"]["staleness_hours"], 5.0)
        self.assertEqual(pack["state"]["accounts"], [{"account_last4": "3766"}])

    def test_dead_token_stale_cache_is_failed(self):
        self._seed_cache(hours_old=30)
        with mock.patch.object(ovc.etrade, "keep_alive", return_value=None):
            pack = self.build(max_stale_hours=24)
        self.assertEqual(pack["meta"]["health"], "failed")

    def test_broker_api_error_falls_back_not_raises(self):
        self._seed_cache(hours_old=1)
        with self._live() as m:
            m["keep_alive"].return_value = {"oauth_token": "t"}
            m["get_accounts"].side_effect = ConnectionError("proxy down")
            pack = self.build()
        self.assertEqual((pack["meta"]["source"], pack["meta"]["health"]), ("cache", "degraded"))
        self.assertTrue(any("ConnectionError" in w for w in pack["meta"]["warnings"]))

    def test_live_false_reads_cache_without_touching_broker(self):
        self._seed_cache(hours_old=2)
        with mock.patch.object(ovc.etrade, "keep_alive",
                               side_effect=AssertionError("live=False must not call the broker")):
            pack = self.build(live=False)
        self.assertEqual((pack["meta"]["source"], pack["meta"]["health"]), ("cache", "degraded"))


class TestDataHealth(_Base):
    def test_placeholder_heavy_held_symbol_degrades(self):
        self._series("AAPL", provisional_every=2)          # half the recent bars are placeholders
        with self._live() as m:
            m["keep_alive"].return_value = {"oauth_token": "t"}
            m["get_accounts"].return_value = _FakeAccounts()
            m["fetch_positions"].return_value = []
            pack = self.build()
        self.assertEqual(pack["meta"]["health"], "degraded")
        self.assertIn("AAPL", pack["state"]["data_health"]["placeholder_heavy"])
        self.assertTrue(any("ATR stops unreliable" in w for w in pack["meta"]["warnings"]))

    def test_placeholder_share(self):
        self._series("MSFT", provisional_every=3)
        self.assertAlmostEqual(ovc.placeholder_share(self.dir, "MSFT"), 10 / 30)
        self.assertIsNone(ovc.placeholder_share(self.dir, "NOPE"))


class TestStudyGates(_Base):
    def test_reports_only_what_the_file_states(self):
        for name, body in {"a": {"as_of": "2026-09-01", "verdict": "PASS"},
                           "b": {"gates": {"x": {"pass": False}}},
                           "c": {"weights": {}}}.items():
            with open(os.path.join(self.dir, f"{name}_study.json"), "w", encoding="utf-8") as f:
                json.dump(body, f)
        gates = ovc._study_gates(self.dir)
        self.assertEqual(gates["a"], {"as_of": "2026-09-01", "verdict": "PASS"})
        self.assertEqual(gates["b"], {"as_of": None, "gates": {"x": False}})
        self.assertEqual(gates["c"], {"as_of": None, "verdict": None})


class TestKnowledge(unittest.TestCase):
    def test_main_checkout_resolved_from_worktree(self):
        main = tempfile.mkdtemp()
        wt = os.path.join(main, "_wt_x")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as f:
            f.write(f"gitdir: {os.path.join(main, '.git', 'worktrees', '_wt_x')}\n")
        try:
            self.assertEqual(ovc._main_checkout(wt), os.path.normpath(main))
            self.assertEqual(ovc._main_checkout(main), main)        # no .git file -> itself
        finally:
            shutil.rmtree(main, ignore_errors=True)


class TestCapabilities(unittest.TestCase):
    def test_all_recommend_only_and_paths_exist(self):
        caps = ovc._capabilities()
        for role, items in caps.items():
            for item in items:
                self.assertTrue(item["recommend_only"], item)
                self.assertTrue(item["exists"], f"{role}: {item['path']}")


if __name__ == "__main__":
    unittest.main()
