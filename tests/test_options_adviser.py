"""Offline unit tests for the options + tax adviser engine (aether/options_adviser.py).

Red-green intent: lock the chain-normalization, strike selection, per-strategy P&L
geometry, and the tax-consideration flags (holding period, §1259, §1092) against a
saved sample ``OptionChainResponse`` fixture — no network, no broker (the auto-loaded
``tests/__init__.py`` harness blocks sockets and pins temp dirs anyway).
"""
import datetime
import json
import unittest
from pathlib import Path

from aether import options_adviser as oa

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "intc_option_chain.json"
with open(FIXTURE, encoding="utf-8") as _f:
    RAW = json.load(_f)

TODAY = datetime.date(2026, 8, 25)          # deterministic "now" for holding-period math
EXPIRY = datetime.date(2026, 10, 16)        # matches the fixture SelectedED


def _pos(qty=100, cost=80.0, price=100.0, acquired=datetime.date(2024, 1, 1)):
    return oa.Position("INTC", qty, cost, price, acquired)


class TestNormalizeChain(unittest.TestCase):
    def setUp(self):
        self.quotes = oa.normalize_chain(RAW)

    def test_counts_and_split(self):
        self.assertEqual(len(self.quotes), 14)
        self.assertEqual(len([q for q in self.quotes if q.option_type == "CALL"]), 7)
        self.assertEqual(len([q for q in self.quotes if q.option_type == "PUT"]), 7)

    def test_expiry_and_fields(self):
        put90 = next(q for q in self.quotes if q.option_type == "PUT" and q.strike == 90)
        self.assertEqual(put90.expiry, EXPIRY)
        self.assertAlmostEqual(put90.bid, 1.55)
        self.assertAlmostEqual(put90.ask, 1.80)
        self.assertAlmostEqual(put90.delta, -0.24)
        self.assertIs(put90.in_the_money, False)

    def test_defensive_single_pair_and_empty(self):
        single = {"OptionChainResponse": {"OptionPair": {"Call": {
            "optionType": "CALL", "strikePrice": 50, "bid": 1, "ask": 2}}}}
        q = oa.normalize_chain(single)
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0].strike, 50)
        self.assertEqual(oa.normalize_chain({}), [])
        self.assertEqual(oa.normalize_chain(None), [])


class TestSelectors(unittest.TestCase):
    def setUp(self):
        self.quotes = oa.normalize_chain(RAW)

    def test_nearest_strike(self):
        self.assertEqual(oa.nearest_strike(self.quotes, 97, "CALL").strike, 95)
        self.assertEqual(oa.nearest_strike(self.quotes, 91, "PUT").strike, 90)

    def test_by_delta(self):
        self.assertEqual(oa.by_delta(self.quotes, 0.35, "CALL").strike, 105)
        self.assertEqual(oa.by_delta(self.quotes, -0.25, "PUT").strike, 90)


class TestStrategyEconomics(unittest.TestCase):
    def setUp(self):
        self.quotes = oa.normalize_chain(RAW)
        self.pos = _pos()

    def test_protective_put(self):
        s = oa.build_protective_put(self.pos, oa.Levels(stop=90, target=110), self.quotes)
        self.assertEqual(s.legs[0].strike, 90)
        self.assertAlmostEqual(s.net_cost, 180.0)          # debit 1.80 * 100
        self.assertAlmostEqual(s.max_loss, 1180.0)         # (100-90 + 1.80) * 100
        self.assertAlmostEqual(s.downside_floor, 88.20)
        self.assertIsNone(s.max_gain)
        self.assertAlmostEqual(s.breakevens[0], 101.80)

    def test_covered_call(self):
        s = oa.build_covered_call(self.pos, oa.Levels(stop=90, target=110), self.quotes)
        self.assertEqual(s.legs[0].strike, 110)
        self.assertAlmostEqual(s.net_cost, -155.0)         # credit 1.55 * 100
        self.assertAlmostEqual(s.max_gain, 1155.0)         # (110-100 + 1.55) * 100
        self.assertAlmostEqual(s.upside_cap, 111.55)
        self.assertIsNone(s.max_loss)

    def test_covered_call_itm_strike_max_gain(self):
        # Strike 95 < spot 100 (ITM): max gain from spot is (95-100 + bid 7.60)*100 = 260,
        # NOT the raw premium 760 — the intrinsic is already spent capping below spot.
        s = oa.build_covered_call(self.pos, oa.Levels(target=95), self.quotes)
        self.assertEqual(s.legs[0].strike, 95)
        self.assertAlmostEqual(s.max_gain, 260.0)          # (95-100 + 7.60) * 100

    def test_collar(self):
        s = oa.build_collar(self.pos, oa.Levels(stop=90, target=110), self.quotes)
        strikes = {l.option_type: l.strike for l in s.legs}
        self.assertEqual(strikes, {"PUT": 90, "CALL": 110})
        self.assertAlmostEqual(s.net_cost, 25.0)           # (1.80 - 1.55) * 100
        self.assertAlmostEqual(s.max_loss, 1025.0)
        self.assertAlmostEqual(s.max_gain, 975.0)
        self.assertAlmostEqual(s.downside_floor, 89.75)
        self.assertAlmostEqual(s.upside_cap, 109.75)

    def test_cash_secured_put(self):
        s = oa.build_cash_secured_put(self.pos, oa.Levels(stop=90), self.quotes, contracts=1)
        self.assertEqual(s.legs[0].strike, 90)
        self.assertAlmostEqual(s.net_cost, -155.0)         # credit 1.55 * 100
        self.assertAlmostEqual(s.max_gain, 155.0)
        self.assertAlmostEqual(s.breakevens[0], 88.45)     # 90 - 1.55

    def test_sub_100_shares_skips_covered_structures(self):
        small = _pos(qty=50)
        levels = oa.Levels(stop=90, target=110)
        self.assertIsNone(oa.build_collar(small, levels, self.quotes))
        self.assertIsNone(oa.build_protective_put(small, levels, self.quotes))
        self.assertIsNone(oa.build_covered_call(small, levels, self.quotes))
        # A cash-secured put stands alone and does not need the existing lot.
        self.assertIsNotNone(oa.build_cash_secured_put(small, levels, self.quotes))


class TestTaxConsiderations(unittest.TestCase):
    def setUp(self):
        self.quotes = oa.normalize_chain(RAW)

    def _flags(self, strategy, pos, spot=100.0, today=TODAY):
        return oa.tax_considerations(strategy, pos, spot, today)

    def test_long_vs_short_term(self):
        s = oa.build_protective_put(_pos(acquired=datetime.date(2024, 1, 1)),
                                    oa.Levels(stop=90), self.quotes)
        long_flags = self._flags(s, _pos(acquired=datetime.date(2024, 1, 1)))
        self.assertTrue(any("long-term" in f for f in long_flags))

        short_flags = self._flags(s, _pos(acquired=datetime.date(2026, 6, 1)))
        self.assertTrue(any("short-term" in f for f in short_flags))

    def test_near_long_term_warning(self):
        pos = _pos(acquired=datetime.date(2025, 9, 1))   # 358 days held on TODAY
        s = oa.build_protective_put(pos, oa.Levels(stop=90), self.quotes)
        flags = self._flags(s, pos)
        self.assertTrue(any("from long-term" in f for f in flags))

    def test_unknown_acquisition_date(self):
        pos = _pos(acquired=None)
        s = oa.build_protective_put(pos, oa.Levels(stop=90), self.quotes)
        flags = self._flags(s, pos)
        self.assertTrue(any("Acquisition date unknown" in f for f in flags))

    def test_1259_tight_collar_flagged(self):
        pos = _pos(cost=80.0)                             # appreciated to spot 100
        s = oa.build_collar(pos, oa.Levels(stop=100, target=100), self.quotes)
        flags = self._flags(s, pos)
        self.assertTrue(any("§1259 constructive-sale risk" in f for f in flags))

    def test_1259_wide_collar_not_flagged(self):
        pos = _pos(cost=80.0)
        s = oa.build_collar(pos, oa.Levels(stop=90, target=110), self.quotes)
        flags = self._flags(s, pos)
        self.assertFalse(any("constructive-sale risk" in f for f in flags))
        self.assertTrue(any("wide enough" in f for f in flags))

    def test_1092_itm_covered_call_flagged(self):
        pos = _pos()
        s = oa.build_covered_call(pos, oa.Levels(target=95), self.quotes)   # strike 95 < spot 100
        flags = self._flags(s, pos)
        self.assertTrue(any("§1092" in f and "in-the-money" in f for f in flags))

    def test_1092_near_dated_covered_call_flagged(self):
        pos = _pos()
        s = oa.build_covered_call(pos, oa.Levels(target=110), self.quotes)  # OTM strike 110
        flags = self._flags(s, pos, today=datetime.date(2026, 10, 1))       # 15 DTE
        self.assertTrue(any("§1092" in f for f in flags))

    def test_1092_qualified_covered_call_not_flagged(self):
        pos = _pos()
        s = oa.build_covered_call(pos, oa.Levels(target=110), self.quotes)  # OTM, 52 DTE on TODAY
        flags = self._flags(s, pos)
        self.assertFalse(any("§1092" in f for f in flags))


class TestReportAndRenderers(unittest.TestCase):
    def setUp(self):
        self.quotes = oa.normalize_chain(RAW)
        self.report = oa.build_report(_pos(), oa.Levels(stop=90, target=110),
                                      self.quotes, spot=100.0, today=TODAY)

    def test_report_has_full_menu_with_tax_flags(self):
        kinds = {s.kind for s in self.report.strategies}
        self.assertEqual(kinds, {"collar", "protective_put", "covered_call", "cash_secured_put"})
        self.assertTrue(all(s.tax_flags for s in self.report.strategies))
        self.assertEqual(self.report.expiry, EXPIRY)

    def test_terminal_render(self):
        txt = oa.render_terminal(self.report)
        self.assertIn("Options adviser — INTC", txt)
        self.assertIn("Collar", txt)
        self.assertIn(oa.TAX_DISCLAIMER, txt)

    def test_html_render(self):
        html = oa.render_html(self.report)
        self.assertIn("<div", html)
        self.assertIn("INTC", html)
        self.assertIn("Tax considerations", html)
        self.assertIn(oa.TAX_DISCLAIMER, html)


class TestExpirySelection(unittest.TestCase):
    """DTE-aware expiry auto-selection (avoids the degenerate 1-DTE front month)."""

    def _payload(self, dates):
        return {"OptionExpireDateResponse": {"ExpirationDate": [
            {"year": d.year, "month": d.month, "day": d.day, "expiryType": "MONTHLY"}
            for d in dates]}}

    def test_normalize_expiries_sorted_dedup_and_defensive(self):
        d1, d2 = datetime.date(2026, 9, 18), datetime.date(2026, 10, 16)
        self.assertEqual(oa.normalize_expiries(self._payload([d2, d1, d1])), [d1, d2])
        # ExpirationDate may arrive as a single dict, not a list.
        single = {"OptionExpireDateResponse": {"ExpirationDate":
                  {"year": 2026, "month": 9, "day": 18}}}
        self.assertEqual(oa.normalize_expiries(single), [d1])
        self.assertEqual(oa.normalize_expiries({}), [])
        self.assertEqual(oa.normalize_expiries(None), [])

    def test_select_nearest_target(self):
        exps = [TODAY + datetime.timedelta(days=n) for n in (3, 10, 24, 38, 66)]
        # target 35 / min 30 -> only 38 & 66 eligible; 38 is closest to 35.
        self.assertEqual(oa.select_expiry(exps, TODAY), TODAY + datetime.timedelta(days=38))

    def test_select_skips_front_when_below_min(self):
        exps = [TODAY + datetime.timedelta(days=n) for n in (1, 2, 45)]
        self.assertEqual(oa.select_expiry(exps, TODAY), TODAY + datetime.timedelta(days=45))

    def test_select_ties_break_to_nearer_date(self):
        # 30 DTE and 40 DTE are both 5 days from target 35 -> prefer the nearer (30).
        exps = [TODAY + datetime.timedelta(days=n) for n in (30, 40)]
        self.assertEqual(oa.select_expiry(exps, TODAY), TODAY + datetime.timedelta(days=30))

    def test_select_fallback_when_all_below_min(self):
        exps = [TODAY + datetime.timedelta(days=n) for n in (1, 5, 12)]
        # none reach 30 DTE -> furthest available rather than the 1-DTE front.
        self.assertEqual(oa.select_expiry(exps, TODAY), TODAY + datetime.timedelta(days=12))

    def test_select_custom_target(self):
        exps = [TODAY + datetime.timedelta(days=n) for n in (7, 14, 21, 60)]
        # short-dated caller: target 10, min 7 -> 7 is closest to 10 among eligible.
        self.assertEqual(oa.select_expiry(exps, TODAY, min_dte=7, target_dte=10),
                         TODAY + datetime.timedelta(days=7))

    def test_select_empty(self):
        self.assertIsNone(oa.select_expiry([], TODAY))

    def test_fetch_chain_auto_selects_and_skips_front(self):
        near = TODAY + datetime.timedelta(days=3)
        good = TODAY + datetime.timedelta(days=38)

        class FakeMarket:
            chain_expiry = "unset"

            def option_expiry_dates(self, symbol, resp_format="json"):
                return {"OptionExpireDateResponse": {"ExpirationDate": [
                    {"year": near.year, "month": near.month, "day": near.day},
                    {"year": good.year, "month": good.month, "day": good.day}]}}

            def options_chain(self, symbol, expiry=None, **kw):
                self.chain_expiry = expiry
                return RAW

            def quotes(self, syms):
                return {}

        client = type("C", (), {"market": FakeMarket()})()
        quotes, spot, expiry = oa.fetch_chain("INTC", client, today=TODAY)
        self.assertEqual(client.market.chain_expiry, good)   # 3-DTE front skipped
        self.assertEqual(expiry, good)
        self.assertTrue(quotes)

    def test_fetch_chain_respects_pinned_expiry(self):
        pinned = datetime.date(2026, 12, 18)

        class FakeMarket:
            looked_up = False

            def option_expiry_dates(self, *a, **k):
                FakeMarket.looked_up = True
                return {}

            def options_chain(self, symbol, expiry=None, **kw):
                self.seen = expiry
                return RAW

            def quotes(self, syms):
                return {}

        client = type("C", (), {"market": FakeMarket()})()
        quotes, spot, expiry = oa.fetch_chain("INTC", client, expiry=pinned, today=TODAY)
        self.assertFalse(FakeMarket.looked_up)               # pinned -> no expiry lookup
        self.assertEqual(client.market.seen, pinned)
        self.assertEqual(expiry, pinned)

    def test_fetch_chain_warns_on_empty_expiry_list(self):
        # An empty / unrecognized expiry payload must not degrade *silently* to the front.
        class FakeMarket:
            def option_expiry_dates(self, symbol, resp_format="json"):
                return {}                                    # -> normalize [] -> select None

            def options_chain(self, symbol, expiry=None, **kw):
                self.seen = expiry
                return RAW

            def quotes(self, syms):
                return {}

        client = type("C", (), {"market": FakeMarket()})()
        with self.assertLogs("aether.options_adviser", level="WARNING") as cm:
            quotes, spot, expiry = oa.fetch_chain("INTC", client, today=TODAY)
        self.assertIsNone(client.market.seen)                # fell back to front (None)
        self.assertIn("front month", "\n".join(cm.output))
        self.assertTrue(quotes)

    def test_fetch_chain_warns_when_expiry_lookup_raises(self):
        class FakeMarket:
            def option_expiry_dates(self, *a, **k):
                raise RuntimeError("boom")

            def options_chain(self, symbol, expiry=None, **kw):
                self.seen = expiry
                return RAW

            def quotes(self, syms):
                return {}

        client = type("C", (), {"market": FakeMarket()})()
        with self.assertLogs("aether.options_adviser", level="WARNING") as cm:
            oa.fetch_chain("INTC", client, today=TODAY)
        self.assertIsNone(client.market.seen)                # fell back to front (None)
        logged = "\n".join(cm.output)
        self.assertIn("auto-select failed", logged)
        self.assertIn("boom", logged)


if __name__ == "__main__":
    unittest.main()
