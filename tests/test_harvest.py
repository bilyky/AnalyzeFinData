"""Harvest list (plan O2a): term on the sale date, losses by term, tax value, wash flags, ranking, live adapter.
Placeholder data only."""
import datetime
import unittest
from unittest import mock

from aether import harvest as hv

D = datetime.date
RATES = {"st_rate": 0.40, "lt_rate": 0.20}


def _row(symbol, acquired, cost, value, qty=10, account="1111"):
    return {"symbol": symbol, "account": account, "acquired": acquired, "qty": qty, "cost": cost, "value": value,
            "gain": value - cost}


class TestTerm(unittest.TestCase):
    def test_long_term_only_after_the_anniversary(self):
        self.assertFalse(hv.is_long_term(D(2025, 11, 20), D(2026, 11, 20)))     # the anniversary itself: short
        self.assertTrue(hv.is_long_term(D(2025, 11, 20), D(2026, 11, 21)))
        self.assertEqual(hv.anniversary(D(2024, 2, 29)), D(2025, 3, 1))


class TestLotRow(unittest.TestCase):
    def test_from_a_broker_lot(self):
        ms = int(datetime.datetime(2023, 10, 17, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        r = hv.lot_row({"acquiredDate": ms, "remainingQty": 10, "totalCost": 393.0, "marketValue": 72.0}, "XYZ", "1111")
        self.assertEqual((r["acquired"], r["qty"], r["cost"], r["gain"]), ("2023-10-17", 10.0, 393.0, -321.0))

    def test_cost_falls_back_to_price_times_shares_and_empty_lots_are_dropped(self):
        ms = int(datetime.datetime(2023, 10, 17, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        self.assertEqual(hv.lot_row({"acquiredDate": ms, "remainingQty": 2, "price": 5.0, "marketValue": 8}, "X", "1")["cost"], 10.0)
        self.assertIsNone(hv.lot_row({"acquiredDate": ms, "remainingQty": 0, "price": 5.0}, "X", "1"))
        self.assertIsNone(hv.lot_row({"acquiredDate": 0, "remainingQty": 3, "price": 5.0}, "X", "1"))


class TestSummary(unittest.TestCase):
    sale = D(2026, 11, 23)

    def test_losses_by_term_and_tax_value(self):
        rows = [_row("ABC", "2023-04-21", 1000, 100),          # long-term loss -900
                _row("ABC", "2026-06-01", 500, 300)]            # short-term loss -200
        s = hv.summarize(rows, self.sale, RATES)
        self.assertEqual((s["lt_loss"], s["st_loss"]), (-900, -200))
        self.assertAlmostEqual(s["tax_value"], 900 * 0.20 + 200 * 0.40)
        self.assertEqual(s["buy_back_after"], "2026-12-24")      # sale + 31 days

    def test_a_recent_buy_is_a_wash_risk(self):
        rows = [_row("ABC", "2024-01-02", 1000, 500), _row("ABC", "2026-11-05", 100, 90)]
        self.assertEqual(hv.summarize(rows, self.sale, RATES)["wash_risk_buys"], ["2026-11-05"])
        old = [_row("ABC", "2024-01-02", 1000, 500), _row("ABC", "2026-10-23", 100, 90)]   # 31 days: clean
        self.assertEqual(hv.summarize(old, self.sale, RATES)["wash_risk_buys"], [])

    def test_a_short_term_loss_lot_about_to_turn_long_term_is_flagged(self):
        rows = [_row("ABC", "2025-12-01", 1000, 500)]
        self.assertEqual(hv.summarize(rows, self.sale, RATES)["st_lots_turning_lt"], ["2026-12-01"])

    def test_gain_lots_are_counted_separately(self):
        rows = [_row("ABC", "2023-01-02", 100, 300), _row("ABC", "2024-01-02", 1000, 500)]
        s = hv.summarize(rows, self.sale, RATES)
        self.assertEqual((s["gain_lots"], s["lt_loss"]), (200, -500))


class TestList(unittest.TestCase):
    def test_only_net_losers_ranked_by_tax_value(self):
        rows = [_row("BIG", "2023-01-02", 10000, 1000),          # LT -9000 -> 1800
                _row("SMALL", "2026-06-01", 1000, 500),          # ST -500 -> 200
                _row("WIN", "2023-01-02", 100, 900)]              # a gain: left out
        lst = hv.harvest_list(rows, D(2026, 11, 23), RATES)
        self.assertEqual([s["symbol"] for s in lst], ["BIG", "SMALL"])

    def test_both_accounts_of_a_symbol_are_one_entry(self):
        rows = [_row("XYZ", "2026-08-07", 1000, 700, account="1111"), _row("XYZ", "2026-05-14", 400, 300, account="2222")]
        (s,) = hv.harvest_list(rows, D(2026, 11, 23), RATES)
        self.assertEqual((s["accounts"], s["st_loss"]), (["1111", "2222"], -400))


class TestLiveAdapter(unittest.TestCase):
    def test_reads_lots_only_for_losing_positions_through_the_facade(self):
        ms = int(datetime.datetime(2024, 1, 2, 12, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        client = mock.Mock()
        client.accounts.account_keys.return_value = {"1111": "k1"}
        client.accounts.positions.return_value = [
            {"symbol": "LOSE", "account_last4": "1111", "qty": 10, "cost": 50.0, "mval": 100.0},   # 500 cost, 100 value
            {"symbol": "WIN", "account_last4": "1111", "qty": 10, "cost": 5.0, "mval": 100.0}]
        client.accounts.position_lots.return_value = [{"acquiredDate": ms, "remainingQty": 10, "totalCost": 500.0,
                                                       "marketValue": 100.0}]
        rows = hv.live_rows(client)
        client.accounts.position_lots.assert_called_once_with("LOSE", "k1")
        self.assertEqual([(r["symbol"], r["gain"]) for r in rows], [("LOSE", -400.0)])


if __name__ == "__main__":
    unittest.main()
