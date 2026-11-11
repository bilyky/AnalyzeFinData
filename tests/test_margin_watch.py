"""Tests for aether/margin_watch.py (R&D #39) and the Oracle's margin section: debt from the
broker's figures, the requirement (owner's table, else assumed), excess and the call point, the
pay-down order, live-data-only, and the HTML. Placeholder numbers, not a real account."""
import json
import os
import tempfile
import unittest
from unittest import mock

import aether_oracle
import data_api
from aether import margin_watch as mw


def acct(holdings, equity, cash=0.0, id_="9999"):
    return {"id": id_, "equity": equity, "balance": cash,
            "holdings": [{"symbol": s, "qty": q, "current": p, "pnl": pnl} for s, q, p, pnl in holdings]}


A = acct([("AAA", 100, 10.0, -500.0), ("BBB", 10, 100.0, 200.0), ("CCC", 50, 20.0, -50.0)], equity=1000.0)
REQS = {"9999": {"AAA": 1.0, "BBB": 0.30, "CCC": 0.30}}


class TestAccountView(unittest.TestCase):
    def test_debt_requirement_excess_and_call(self):
        v = mw.account_view(A, REQS)
        self.assertEqual(v["holdings_value"], 3000.0)
        self.assertEqual(v["debt"], 2000.0)                         # 3000 holdings - 1000 equity
        self.assertAlmostEqual(v["requirement"], 1000 + 300 + 300)
        self.assertAlmostEqual(v["excess"], 1000 - 1600)
        self.assertLess(v["call_drop"], 0)                          # already under: 1 - 2000/(3000-1600)
        self.assertEqual(v["requirement_assumed"], 0.0)

    def test_call_drop_when_above(self):
        v = mw.account_view(acct([("BBB", 100, 100.0, 0.0)], equity=6000.0), {"9999": {"BBB": 0.30}})
        # debt 4000; requirement 3000; call when 10000(1-d) - 4000 = 3000(1-d) -> d = 1 - 4000/7000
        self.assertAlmostEqual(v["call_drop"], 1 - 4000 / 7000)
        self.assertAlmostEqual(v["excess"], 3000.0)

    def test_cash_field_is_not_in_the_debt(self):
        # the broker's cash field misread a margined account (2026-11-10); holdings - net value matched
        v = mw.account_view(acct([("BBB", 100, 100.0, 0.0)], equity=6000.0, cash=150.0), {})
        self.assertAlmostEqual(v["debt"], 10000 - 6000)
        self.assertIsNone(mw.account_view(acct([("BBB", 10, 100.0, 0.0)], equity=1500.0, cash=500.0), {}))

    def test_missing_requirement_is_assumed_and_reported(self):
        v = mw.account_view(A, {"9999": {"AAA": 1.0}})
        self.assertAlmostEqual(v["requirement_assumed"], 0.30 * 1000 + 0.30 * 1000)
        self.assertIn("assumed at 30%", mw.render_html([v]))

    def test_pay_down_order_highest_requirement_then_losses(self):
        order = [x["symbol"] for x in mw.account_view(A, REQS)["pay_down"]]
        self.assertEqual(order, ["AAA", "CCC", "BBB"])              # 100% first; then the loss before the gain
        self.assertEqual(mw.account_view(A, REQS)["pay_down"][0]["freed"], 1000.0)

    def test_requirement_outranks_a_bigger_loss(self):
        a = acct([("AAA", 100, 10.0, -100.0), ("DDD", 100, 10.0, -900.0)], equity=500.0)
        order = [x["symbol"] for x in mw.account_view(a, {"9999": {"AAA": 1.0, "DDD": 0.30}})["pay_down"]]
        self.assertEqual(order, ["AAA", "DDD"])                     # $1 freed per $1 beats a bigger loss at 30%

    def test_interest_only_with_a_rate(self):
        self.assertIsNone(mw.account_view(A, REQS)["interest_month"])
        v = mw.account_view(A, REQS, rate_pct=12.0)
        self.assertAlmostEqual(v["interest_month"], 20.0)
        self.assertAlmostEqual(v["pay_down"][0]["interest_saved_month"], 10.0)

    def test_no_debt_no_view(self):
        self.assertIsNone(mw.account_view(acct([("BBB", 10, 100.0, 0.0)], equity=1000.0), {}))


class TestBrokerFields(unittest.TestCase):
    def test_margin_fields_parse_with_none_for_missing(self):
        comp = {"marginBalance": -2000.5, "regtEquity": "1000.25", "regtEquityPercent": 33.3,
                "OpenCalls": {"houseCall": -120.4, "fedCall": 0, "minEquityCall": "", "cashCall": "x"}}
        self.assertEqual(data_api._margin_fields(comp),
                         {"margin_balance": -2000.5, "margin_equity": 1000.25, "margin_equity_pct": 33.3,
                          "house_call": -120.4, "fed_call": 0.0, "min_equity_call": None, "cash_call": None})
        self.assertEqual(set(data_api._margin_fields({}).values()), {None})

    def test_broker_figures_replace_the_computed_ones_and_show_the_call(self):
        # the broker's balance differs from holdings - equity (accrued interest): the broker's wins
        a = {**A, "margin": {"margin_balance": -2150.0, "margin_equity": 900.0, "house_call": -150.0,
                             "fed_call": 0.0, "min_equity_call": None, "cash_call": None}}
        v = mw.account_view(a, REQS)
        self.assertEqual((v["debt"], v["equity"], v["source"]), (2150.0, 900.0, "broker"))
        self.assertEqual(v["open_calls"], {"house_call": 150.0})          # only negative calls, as amounts due
        self.assertIn("OPEN CALL per the broker: house call $150", mw.render_html([v]))

    def test_broker_reports_no_loan_means_no_view(self):
        # live 2026-11-10: a cash account came back with no marginBalance, and holdings - net value
        # was a few dollars of price-override noise, shown as a "debt"
        a = {**A, "margin": {k: None for k in ("margin_balance", "margin_equity", "house_call")}}
        self.assertIsNone(mw.account_view(a, REQS))
        b = {**A, "margin": {"margin_balance": 0.0, "margin_equity": 1000.0}}
        self.assertIsNone(mw.account_view(b, REQS))                    # zero balance: no loan

    def test_without_broker_figures_it_says_computed(self):
        v = mw.account_view(A, REQS)
        self.assertEqual((v["source"], v["open_calls"]), ("computed", {}))

    def test_oracle_logs_the_broker_call(self):
        a = {**A, "margin": {"margin_balance": -2100.0, "margin_equity": 900.0, "house_call": -150.0}}
        with mock.patch.object(aether_oracle.data_api, "read_accounts",
                               return_value={"broker_status": "live", "accounts": [a]}), \
                mock.patch.object(aether_oracle.CFG, "accounts_real", ["9999"]), \
                mock.patch.object(aether_oracle.CFG, "oracle_margin_rate_pct", None, create=True), \
                mock.patch.object(aether_oracle.margin_watch, "load_requirements", return_value=REQS), \
                self.assertLogs(aether_oracle._oracle_log.name, level="WARNING") as cm:
            aether_oracle.margin_section()
        self.assertIn("open call per the broker: house_call $150.00", cm.output[0])


class TestViewsAndFile(unittest.TestCase):
    def test_live_only_and_real_accounts_only(self):
        data = {"broker_status": "live", "accounts": [A, {**A, "id": "1111"}]}
        self.assertEqual([v["id"] for v in mw.views(data, ["9999"], REQS)], ["9999"])
        self.assertEqual(mw.views({**data, "broker_status": "offline"}, ["9999"], REQS), [])
        self.assertEqual(mw.views(None, ["9999"], REQS), [])

    def test_requirements_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "margin_requirements.json")
            self.assertEqual(mw.load_requirements(p), {})
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"9999": {"aaa": 1, "BBB": "0.5"}, "bad": 3}, f)
            self.assertEqual(mw.load_requirements(p), {"9999": {"AAA": 1.0, "BBB": 0.5}})
            with open(p, "w", encoding="utf-8") as f:
                f.write("{broken")
            self.assertEqual(mw.load_requirements(p), {})

    def test_render_states(self):
        self.assertEqual(mw.render_html([]), "")
        self.assertIn("AT/UNDER", mw.render_html([mw.account_view(A, REQS)]))
        thin = mw.account_view(acct([("BBB", 100, 100.0, 0.0)], equity=3500.0), {"9999": {"BBB": 0.30}})
        self.assertIn("thin", mw.render_html([thin]))
        ok = mw.account_view(acct([("BBB", 100, 100.0, 0.0)], equity=6000.0), {"9999": {"BBB": 0.30}})
        self.assertIn("a 42.9% fall reaches the call", mw.render_html([ok]))


class TestOracleSection(unittest.TestCase):
    def test_section_covers_real_accounts_and_never_raises(self):
        data = {"broker_status": "live", "accounts": [A]}
        with mock.patch.object(aether_oracle.data_api, "read_accounts", return_value=data), \
                mock.patch.object(aether_oracle.CFG, "accounts_real", ["9999"]), \
                mock.patch.object(aether_oracle.CFG, "oracle_margin_rate_pct", None, create=True), \
                mock.patch.object(aether_oracle.margin_watch, "load_requirements", return_value=REQS), \
                self.assertLogs(aether_oracle._oracle_log.name, level="WARNING"):
            html = aether_oracle.margin_section()
        self.assertIn("Margin watch", html)
        self.assertIn("...9999", html)
        with mock.patch.object(aether_oracle.data_api, "read_accounts", side_effect=RuntimeError("down")):
            self.assertEqual(aether_oracle.margin_section(), "")

    def test_advisory_appends_the_section_even_when_the_oracle_account_is_unavailable(self):
        with mock.patch.object(aether_oracle, "get_oracle_account", return_value=None), \
                mock.patch.object(aether_oracle, "margin_section", return_value="<MW>"):
            self.assertTrue(aether_oracle.run_oracle_advisory().endswith("<MW>"))


if __name__ == "__main__":
    unittest.main()
