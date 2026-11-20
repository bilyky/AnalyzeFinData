"""Position-watch's data has one home (aether/position_watch_store.py, PR #271 design review), and the
lots refresh reads the broker through the client facade, not a private helper. Placeholder data."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "monitoring"))
import oceanview_api as ov
import position_watch as pw
from aether import etrade as et_pkg
from aether import lots_refresh as lr
from aether import paths
from aether import position_watch_store as store
from aether.etrade.client import ETradeClient

LOTS = [{"account": "A", "acquired": "2024-05-01", "price": 30.0, "qty": 10.0},
        {"account": "B", "acquired": "2025-01-02", "price": 40.0, "qty": 5.0}]


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = os.path.join(self._tmp.name, store.FOLDER_NAME)
        os.makedirs(self.folder)

    def test_the_folder_is_where_position_watch_always_kept_it(self):
        self.assertEqual(store.watch_dir(), os.path.join(paths.data_dir(), "position_watch"))
        self.assertEqual(pw.watch_dir(), store.watch_dir())

    def test_config_missing_present_and_broken(self):
        self.assertIsNone(store.read_config(self.folder))
        with open(store.config_path(self.folder), "w", encoding="utf-8") as f:
            json.dump({"symbol": "XYZ"}, f)
        self.assertEqual(store.read_config(self.folder), {"symbol": "XYZ"})
        with open(store.config_path(self.folder), "w", encoding="utf-8") as f:
            f.write("{broken")
        with self.assertRaises(ValueError):
            store.read_config(self.folder)

    def test_lots_missing_is_none_and_broken_raises(self):
        self.assertIsNone(store.read_lots("XYZ", self.folder))
        with open(store.lots_path("XYZ", self.folder), "w", encoding="utf-8") as f:
            f.write("[1,")
        with self.assertRaises(ValueError):
            store.read_lots("XYZ", self.folder)

    def test_write_backs_up_then_replaces(self):
        self.assertIsNone(store.write_lots("XYZ", LOTS[:1], self.folder))        # no old file: no backup
        bdir = store.write_lots("XYZ", LOTS, self.folder, now=datetime.datetime(2026, 11, 19, 9, 0, 0))
        self.assertEqual(bdir, os.path.join(self._tmp.name, "Backup", "20261119_090000_pre_lots_refresh"))
        with open(os.path.join(bdir, "XYZ_lots.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), LOTS[:1])                              # the old file, intact
        self.assertEqual(store.read_lots("XYZ", self.folder), LOTS)
        self.assertFalse(os.path.exists(store.lots_path("XYZ", self.folder) + ".tmp"))

    def test_a_failed_write_leaves_no_tmp_and_the_old_file_intact(self):
        store.write_lots("XYZ", LOTS[:1], self.folder)
        with mock.patch.object(store.json, "dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.write_lots("XYZ", LOTS, self.folder)
        self.assertFalse(os.path.exists(store.lots_path("XYZ", self.folder) + ".tmp"))
        self.assertEqual(store.read_lots("XYZ", self.folder), LOTS[:1])

    def test_same_lots_ignores_order_and_number_type(self):
        self.assertTrue(store.same_lots(LOTS, [dict(LOTS[1], qty=5), LOTS[0]]))
        self.assertFalse(store.same_lots(LOTS, LOTS[:1]))
        self.assertEqual(store.lots_diff(LOTS[:1], LOTS), (1, 0))


class TestCallersUseTheStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(store, "watch_dir", return_value=self._tmp.name)
        p.start()
        self.addCleanup(p.stop)
        with open(store.config_path(), "w", encoding="utf-8") as f:
            json.dump({"symbol": "XYZ", "floor_pct": 0.25, "lot_sources": {"A": "9999"}}, f)

    def test_position_watch_reads_the_config_through_the_store(self):
        self.assertEqual(pw.load_config()["floor_pct"], 0.25)

    def test_margin_watch_reads_the_config_through_the_store(self):
        from aether import margin_watch
        with open(store.config_path(), "w", encoding="utf-8") as f:
            json.dump({"symbol": "xyz", "acquisition_dates": ["2026-11-01"]}, f)
        with mock.patch.object(store, "read_config", wraps=store.read_config) as rc:
            self.assertEqual(margin_watch.load_purchases(), {"XYZ": ["2026-11-01"]})
        rc.assert_called_once()
        with open(store.config_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(margin_watch.load_purchases(), {})               # broken: {} as before
        os.remove(store.config_path())
        self.assertEqual(margin_watch.load_purchases(), {})               # missing: {} as before

    def test_lots_refresh_reads_the_config_through_the_store(self):
        r = lr.refresh(mock.Mock(), cfg=None)                  # no positions: stops after the config read
        self.assertEqual(r["status"], "error")                 # the config was found (else "skipped")

    def test_oceanview_reads_the_lots_through_the_store(self):
        store.write_lots("XYZ", LOTS)
        with mock.patch.object(store, "read_lots", wraps=store.read_lots) as rl, \
                mock.patch.object(ov.pw, "run", return_value={"price": 50.0, "call": {"action": "HOLD"}}), \
                mock.patch.object(ov, "research", return_value=[]):
            s = ov.summary("XYZ")
        rl.assert_called_once_with("XYZ")
        self.assertEqual(len(s["lots"]), 2)

    def test_oceanview_shows_no_lots_on_a_broken_file_instead_of_failing(self):
        with open(store.lots_path("XYZ"), "w", encoding="utf-8") as f:
            f.write("[1,")
        with mock.patch.object(ov.pw, "run", return_value={"price": 50.0, "call": {"action": "HOLD"}}), \
                mock.patch.object(ov, "research", return_value=[]):
            self.assertEqual(ov.summary("XYZ")["lots"], [])


class TestFacade(unittest.TestCase):
    def api(self):
        api = mock.Mock()
        api.list_accounts.return_value = {"AccountListResponse": {"Accounts": {"Account": [
            {"accountId": "0009999", "accountIdKey": "k1"}, {"accountId": "0001111", "accountIdKey": "k2"}]}}}
        api.get_portfolio_position_lot.return_value = {"PositionLotsResponse": {"PositionLot": {"price": 1.0}}}
        return api

    def test_account_keys_and_position_lots_go_through_the_client(self):
        accts = ETradeClient("production", role="data", store=mock.Mock()).accounts
        api = self.api()
        with mock.patch.object(et_pkg, "get_accounts", return_value=api) as ga:
            self.assertEqual(accts.account_keys({"t": 1}), {"9999": "k1", "1111": "k2"})
            self.assertEqual(accts.position_lots("XYZ", "k1", {"t": 1}), [{"price": 1.0}])   # single dict -> list
        api.get_portfolio_position_lot.assert_called_once_with("XYZ", "k1", resp_format="json")
        self.assertEqual(ga.call_args.args, ({"t": 1}, "production"))

    def test_the_data_plane_never_renews(self):
        tokens = mock.Mock()
        tokens.tokens.load.return_value = {"t": "cached"}
        accts = ETradeClient("production", role="data", store=tokens).accounts
        with mock.patch.object(et_pkg, "get_accounts", return_value=self.api()) as ga, \
                mock.patch.object(et_pkg, "get_tokens") as gt, mock.patch.object(et_pkg, "renew_tokens") as rn:
            accts.account_keys()                                   # no token passed: read the cache only
        gt.assert_not_called()
        rn.assert_not_called()
        self.assertEqual(ga.call_args.args[0], {"t": "cached"})

    def test_lots_refresh_no_longer_reaches_into_the_package(self):
        src = open(lr.__file__, encoding="utf-8").read()
        self.assertNotIn("etrade._walk", src)
        self.assertNotIn("get_portfolio_position_lot", src)


if __name__ == "__main__":
    unittest.main()
