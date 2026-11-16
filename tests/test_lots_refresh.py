"""Tests for aether/lots_refresh.py: the read-only lot fetch (payload shapes, ET dates), the merge
(mapped labels replaced, others kept), the gates (quantity must match, nothing written on a broker
error or a mismatch), the backup, and powergauge's wrapper that never raises. Placeholder data."""
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

from aether import lots_refresh as lr

KEY = "key-1"


def accounts_api(lots, qty=None, last4="9999", single=False):
    """A fake pyetrade ETradeAccounts: one account holding XYZ with `lots` [(ms, price, qty)]."""
    api = mock.Mock()
    acct = {"accountId": "000" + last4, "accountIdKey": KEY}
    api.list_accounts.return_value = {"AccountListResponse": {"Accounts": {"Account": acct if single else [acct]}}}
    q = sum(x[2] for x in lots) if qty is None else qty
    api.positions = [{"symbol": "XYZ", "qty": q, "account_last4": last4},        # etrade.fetch_positions() rows
                     {"symbol": "ABC", "qty": 5, "account_last4": last4}]
    pl = [{"acquiredDate": ms, "price": p, "remainingQty": n} for ms, p, n in lots]
    api.get_portfolio_position_lot.return_value = {"PositionLotsResponse": {"PositionLot": pl[0] if single and pl else pl}}
    return api


def run_refresh(api, **kw):
    return lr.refresh(api, positions=api.positions, **kw)


def fetch(api, symbol, last4s):
    return lr.fetch_position_lots(api, symbol, last4s, api.positions)


def ms(y, m, d, hour_utc=12):
    return int(datetime.datetime(y, m, d, hour_utc, tzinfo=datetime.timezone.utc).timestamp() * 1000)


class TestFetch(unittest.TestCase):
    def test_lots_and_position(self):
        got = fetch(accounts_api([(ms(2024, 5, 1), 30.0, 10), (ms(2025, 1, 2), 40.0, 5)]), "XYZ", {"9999"})
        self.assertEqual(got["9999"]["position_qty"], 15)
        self.assertEqual(got["9999"]["lots"], [{"acquired": "2024-05-01", "price": 30.0, "qty": 10.0},
                                               {"acquired": "2025-01-02", "price": 40.0, "qty": 5.0}])

    def test_single_dict_payloads_and_unmapped_accounts(self):
        got = fetch(accounts_api([(ms(2024, 5, 1), 30.0, 10)], single=True), "XYZ", {"9999"})
        self.assertEqual(len(got["9999"]["lots"]), 1)
        self.assertEqual(fetch(accounts_api([(ms(2024, 5, 1), 30.0, 10)]), "XYZ", {"1111"}), {})

    def test_dates_are_eastern(self):
        # 2024-05-02 02:00 UTC is still 2024-05-01 in New York: the ET date, not the UTC one
        self.assertEqual(lr.et_date(ms(2024, 5, 2, hour_utc=2)), "2024-05-01")
        self.assertIsNone(lr.et_date(0))
        self.assertIsNone(lr.et_date("bad"))


class TestMerge(unittest.TestCase):
    def test_mapped_label_replaced_others_kept(self):
        existing = [{"account": "brokerage", "acquired": "2020-01-01", "price": 1.0, "qty": 1.0},
                    {"account": "stock plan", "acquired": "2021-01-01", "price": 2.0, "qty": 2.0}]
        new = lr.merge(existing, {"brokerage": [{"acquired": "2024-05-01", "price": 30.0, "qty": 10.0}]})
        self.assertEqual(new[0], existing[1])                                   # untouched, first
        self.assertEqual(new[1], {"account": "brokerage", "acquired": "2024-05-01", "price": 30.0, "qty": 10.0})
        self.assertEqual(len(new), 2)


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.watch = os.path.join(self.tmp, "position_watch")
        os.makedirs(self.watch)
        self.path = os.path.join(self.watch, "XYZ_lots.json")
        self.cfg = {"symbol": "XYZ", "lot_sources": {"brokerage": "9999"}}
        self.old = [{"account": "brokerage", "acquired": "2020-01-01", "price": 1.0, "qty": 1.0},
                    {"account": "stock plan", "acquired": "2021-01-01", "price": 2.0, "qty": 2.0}]
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.old, f)

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def _backups(self):
        b = os.path.join(self.tmp, "Backup")
        return sorted(os.listdir(b)) if os.path.isdir(b) else []

    def test_updates_with_a_backup(self):
        api = accounts_api([(ms(2024, 5, 1), 30.0, 10)])
        r = run_refresh(api, cfg=self.cfg, data_dir=self.tmp, now=datetime.datetime(2026, 11, 14, 9, 0, 0))
        self.assertEqual(r["status"], "updated")
        self.assertIn({"account": "stock plan", "acquired": "2021-01-01", "price": 2.0, "qty": 2.0}, self._read())
        self.assertIn({"account": "brokerage", "acquired": "2024-05-01", "price": 30.0, "qty": 10.0}, self._read())
        self.assertEqual(self._backups(), ["20261114_090000_pre_lots_refresh"])
        with open(os.path.join(self.tmp, "Backup", self._backups()[0], "XYZ_lots.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.old)                           # the old file, intact
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_quantity_mismatch_blocks_the_write(self):
        r = run_refresh(accounts_api([(ms(2024, 5, 1), 30.0, 10)], qty=12), cfg=self.cfg, data_dir=self.tmp)
        self.assertEqual(r["status"], "blocked")
        self.assertIn("lots add up to 10, position is 12", r["reason"])
        self.assertEqual(self._read(), self.old)
        self.assertEqual(self._backups(), [])

    def test_broker_error_and_missing_account_keep_the_file(self):
        api = accounts_api([])
        api.list_accounts.side_effect = RuntimeError("401")
        self.assertEqual(run_refresh(api, cfg=self.cfg, data_dir=self.tmp)["status"], "error")
        r = run_refresh(accounts_api([(ms(2024, 5, 1), 30.0, 10)], last4="1111"), cfg=self.cfg, data_dir=self.tmp)
        self.assertEqual(r["status"], "blocked")
        self.assertIn("not in the broker's list", r["reason"])
        self.assertEqual(self._read(), self.old)

    def test_unchanged_and_not_configured(self):
        lots = [(ms(2020, 1, 1), 1.0, 1)]
        self.assertEqual(run_refresh(accounts_api(lots), cfg=self.cfg, data_dir=self.tmp)["status"], "unchanged")
        self.assertEqual(self._backups(), [])
        self.assertEqual(run_refresh(accounts_api(lots), cfg={"symbol": "XYZ"}, data_dir=self.tmp)["status"], "skipped")

    def test_quantities_come_from_the_positions_not_a_second_portfolio_read(self):
        api = accounts_api([(ms(2024, 5, 1), 30.0, 10)])
        self.assertEqual(run_refresh(api, cfg=self.cfg, data_dir=self.tmp)["status"], "updated")
        api.get_account_portfolio.assert_not_called()
        api2 = accounts_api([(ms(2024, 5, 1), 30.0, 10)])
        r = lr.refresh(api2, cfg=self.cfg, data_dir=self.tmp)              # no positions given
        self.assertEqual(r["status"], "error")
        api2.list_accounts.assert_not_called()

    def test_same_symbol_in_another_account_does_not_count(self):
        api = accounts_api([(ms(2024, 5, 1), 30.0, 10)])
        api.positions.append({"symbol": "XYZ", "qty": 7, "account_last4": "1111"})   # an unmapped account
        self.assertEqual(run_refresh(api, cfg=self.cfg, data_dir=self.tmp)["status"], "updated")

    def test_dry_run_reports_and_writes_nothing(self):
        r = run_refresh(accounts_api([(ms(2024, 5, 1), 30.0, 10)]), cfg=self.cfg, data_dir=self.tmp, dry_run=True)
        self.assertEqual((r["status"], r["added"], r["removed"]), ("would_update", 1, 1))
        self.assertEqual(self._read(), self.old)
        self.assertEqual(self._backups(), [])

    def test_cli_without_a_token_does_nothing(self):
        with mock.patch.object(lr.etrade, "_load_tokens", return_value=None), \
                mock.patch.object(lr, "refresh") as ref, mock.patch.object(lr.sys, "stdout"):
            self.assertEqual(lr.main([]), 1)
        ref.assert_not_called()

    def test_cli_is_a_dry_run_unless_write(self):
        with mock.patch.object(lr.etrade, "_load_tokens", return_value={"oauth_token": "t"}), \
                mock.patch.object(lr.etrade, "get_accounts", return_value="API"), \
                mock.patch.object(lr.etrade, "fetch_positions", return_value=["ROWS"]), \
                mock.patch.object(lr, "refresh", return_value={"status": "would_update"}) as ref, \
                mock.patch.object(lr.sys, "stdout"):
            lr.main([])
            self.assertTrue(ref.call_args.kwargs["dry_run"])
            self.assertEqual(ref.call_args.kwargs["positions"], ["ROWS"])   # quantities from fetch_positions
            lr.main(["--write"])
            self.assertFalse(ref.call_args.kwargs["dry_run"])

    def test_a_sold_out_position_empties_that_label(self):
        r = run_refresh(accounts_api([], qty=0), cfg=self.cfg, data_dir=self.tmp)
        self.assertEqual(r["status"], "updated")
        self.assertEqual(self._read(), [self.old[1]])                          # brokerage gone, plan kept


class TestPowergaugeWrapper(unittest.TestCase):
    def test_never_raises_and_logs_the_outcome(self):
        import powergauge
        with mock.patch.object(powergauge.lots_refresh, "refresh", side_effect=RuntimeError("boom")), \
                self.assertLogs(powergauge._pg_log.name, level="WARNING") as cm:
            r = powergauge._refresh_watch_lots(mock.Mock())
        self.assertEqual(r["status"], "error")
        self.assertIn("[Lots] error: boom", cm.output[0])


if __name__ == "__main__":
    unittest.main()
