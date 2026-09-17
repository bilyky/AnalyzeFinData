"""B2 tests for aether.scenario.schema — the entity views.

These pin the ONE load-bearing invariant of the schema layer: the views are
lenses over the raw dicts, never copies. ``from_dict`` wraps, ``to_dict`` returns
the same object (identity), and a mutation on either side — through the view or
through legacy code holding the same dict — is seen by the other. That is what
lets ``circuit_breaker`` / ``options`` keep mutating ``state`` and positions by
reference after the REPLACE phase routes call-sites through these views.

Also covers the read accessors' defaults (matched to the game's ``.get`` call-
sites), the write-through setters, and the small pure helpers.

No game import, no network, no disk — pure dict lenses.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether.scenario.schema import Order, Portfolio, Position, Quote  # noqa: E402


class TestIdentityContract(unittest.TestCase):
    """from_dict wraps (no copy); to_dict returns the SAME object; mutations are
    shared both ways. This is the invariant the whole layer depends on."""

    def test_from_dict_does_not_copy(self):
        for cls, raw in [
            (Position, {"qty": 1, "cost": 2.0}),
            (Order, {"type": "BUY", "symbol": "AAA", "reason": "x"}),
            (Quote, {"AAA": 1.0}),
            (Portfolio, {"balance": 100.0, "positions": {}}),
        ]:
            with self.subTest(cls=cls.__name__):
                view = cls.from_dict(raw)
                self.assertIs(view.to_dict(), raw)
                self.assertIs(view.raw, raw)

    def test_position_mutation_visible_both_ways(self):
        raw = {"qty": 10, "cost": 50.0, "stop_loss": 40.0}
        pos = Position.from_dict(raw, "AAA")
        # legacy code mutates the raw dict (as options.py / the SELL loop do) ...
        raw["written_call"] = {"strike": 55, "qty": 10, "premium": 1.2}
        raw["highest_close_since_acq"] = 60.0
        self.assertTrue(pos.has_written_call())
        self.assertEqual(pos.written_call["strike"], 55)
        self.assertEqual(pos.highest_close_since_acq, 60.0)
        # ... and a mutation through the view is seen by the raw dict.
        pos.stop_loss = 44.0
        pos.banked_pct = 0.25
        self.assertEqual(raw["stop_loss"], 44.0)
        self.assertEqual(raw["banked_pct"], 0.25)

    def test_portfolio_mutation_visible_both_ways(self):
        raw = {"balance": 1000.0, "equity": 1000.0, "positions": {"AAA": {"qty": 1, "cost": 10.0}}}
        pf = Portfolio.from_dict(raw)
        # view -> raw
        pf.balance -= 250.0
        pf.profile = "AGGRESSIVE"
        self.assertEqual(raw["balance"], 750.0)
        self.assertEqual(raw["profile"], "AGGRESSIVE")
        # raw -> view (a circuit_breaker-style external pop)
        raw["positions"].pop("AAA")
        self.assertEqual(len(pf.positions), 0)
        self.assertIsNone(pf.position("AAA"))

    def test_live_containers_are_same_objects(self):
        positions = {"AAA": {"qty": 1, "cost": 10.0}}
        history = [{"type": "BUY"}]
        raw = {"positions": positions, "history": history}
        pf = Portfolio.from_dict(raw)
        self.assertIs(pf.positions, positions)
        self.assertIs(pf.history, history)


class TestPosition(unittest.TestCase):
    def test_read_defaults_match_game_callsites(self):
        pos = Position.from_dict({})
        self.assertEqual(pos.qty, 0)
        self.assertEqual(pos.cost, 0.0)
        self.assertIsNone(pos.stop_loss)
        self.assertFalse(pos.is_scarcity)
        self.assertEqual(pos.banked_pct, 0.0)
        self.assertEqual(pos.highest_close_since_acq, 0.0)
        self.assertEqual(pos.buy_dna, {})
        self.assertIsNone(pos.written_call)
        self.assertEqual(pos.verdicts, {})
        self.assertFalse(pos.has_written_call())

    def test_banked_pct_coerces_none_and_str(self):
        # The SELL loop does float(pos.get("banked_pct", 0.0) or 0.0).
        self.assertEqual(Position.from_dict({"banked_pct": None}).banked_pct, 0.0)
        self.assertEqual(Position.from_dict({"banked_pct": "0.5"}).banked_pct, 0.5)

    def test_market_value_uses_price(self):
        pos = Position.from_dict({"qty": 10, "cost": 50.0})
        self.assertEqual(pos.market_value(60.0), 600.0)

    def test_market_value_falls_back_to_cost_on_bad_price(self):
        # Mirrors _live_equity: missing / non-positive quote -> cost basis.
        pos = Position.from_dict({"qty": 10, "cost": 50.0})
        self.assertEqual(pos.market_value(0), 500.0)
        self.assertEqual(pos.market_value(None), 500.0)
        self.assertEqual(pos.market_value(-5), 500.0)

    def test_symbol_carried_alongside(self):
        pos = Position.from_dict({"qty": 1}, "NVDA")
        self.assertEqual(pos.symbol, "NVDA")
        self.assertIn("NVDA", repr(pos))


class TestOrder(unittest.TestCase):
    def test_buy_order(self):
        o = Order.from_dict({"type": "BUY", "symbol": "AAA", "reason": "top pick"})
        self.assertTrue(o.is_buy())
        self.assertFalse(o.is_sell())
        self.assertEqual(o.symbol, "AAA")
        self.assertEqual(o.reason, "top pick")

    def test_sell_order(self):
        o = Order.from_dict({"type": "SELL", "symbol": "BBB", "reason": "exit"})
        self.assertTrue(o.is_sell())
        self.assertFalse(o.is_buy())


class TestQuote(unittest.TestCase):
    def test_price_and_usability(self):
        q = Quote.from_dict({"AAA": 10.0, "BBB": 0, "CCC": None})
        self.assertEqual(q.price("AAA"), 10.0)
        self.assertIsNone(q.price("ZZZ"))
        self.assertEqual(q.price("ZZZ", 0.0), 0.0)
        self.assertTrue(q.is_usable("AAA"))
        self.assertFalse(q.is_usable("BBB"))   # zero
        self.assertFalse(q.is_usable("CCC"))   # None
        self.assertFalse(q.is_usable("ZZZ"))   # absent

    def test_contains_uses_usability(self):
        q = Quote.from_dict({"AAA": 10.0, "BBB": 0})
        self.assertIn("AAA", q)
        self.assertNotIn("BBB", q)

    def test_missing_matches_gap_predicate(self):
        # Reuses prices._missing — a symbol is missing when absent, falsy, or <= 0.
        q = Quote.from_dict({"AAA": 10.0, "BBB": 0, "CCC": -1})
        self.assertEqual(q.missing(["AAA", "BBB", "CCC", "DDD"]), ["BBB", "CCC", "DDD"])


class TestPortfolio(unittest.TestCase):
    def _state(self):
        return {
            "balance": 5000.0,
            "equity": 8000.0,
            "positions": {
                "AAA": {"qty": 10, "cost": 100.0},
                "BBB": {"qty": 5, "cost": 200.0},
            },
            "history": [{"type": "BUY", "symbol": "AAA"}],
            "start_date": "2026-01-01",
            "profile": "BALANCED",
            "queued_orders": [
                {"type": "SELL", "symbol": "AAA", "reason": "exit"},
                {"type": "BUY", "symbol": "CCC", "reason": "pick"},
            ],
        }

    def test_scalar_accessors(self):
        pf = Portfolio.from_dict(self._state())
        self.assertEqual(pf.balance, 5000.0)
        self.assertEqual(pf.equity, 8000.0)
        self.assertEqual(pf.start_date, "2026-01-01")
        self.assertEqual(pf.profile, "BALANCED")
        self.assertIsNone(pf.profile_mode)  # absent in default state

    def test_scalar_setters_write_through(self):
        raw = self._state()
        pf = Portfolio.from_dict(raw)
        pf.equity = 9000.0
        pf.profile_mode = "ADAPTIVE"
        self.assertEqual(raw["equity"], 9000.0)
        self.assertEqual(raw["profile_mode"], "ADAPTIVE")

    def test_position_view(self):
        pf = Portfolio.from_dict(self._state())
        aaa = pf.position("AAA")
        self.assertIsInstance(aaa, Position)
        self.assertEqual(aaa.symbol, "AAA")
        self.assertEqual(aaa.qty, 10)
        # the view is live: mutate through it, the state dict sees it
        aaa.stop_loss = 90.0
        self.assertEqual(pf.raw["positions"]["AAA"]["stop_loss"], 90.0)

    def test_position_views_iterates_all(self):
        pf = Portfolio.from_dict(self._state())
        got = {sym: p.qty for sym, p in pf.position_views()}
        self.assertEqual(got, {"AAA": 10, "BBB": 5})

    def test_orders_views(self):
        pf = Portfolio.from_dict(self._state())
        orders = pf.orders()
        self.assertEqual([o.type for o in orders], ["SELL", "BUY"])
        self.assertTrue(orders[0].is_sell())
        self.assertTrue(orders[1].is_buy())

    def test_queued_orders_setdefault_on_empty_state(self):
        # load_game's default state has NO queued_orders key; the game reaches it via
        # setdefault. The view must do the same and return a real, live list.
        raw = {"balance": 1.0, "positions": {}, "history": []}
        pf = Portfolio.from_dict(raw)
        pf.queued_orders.append({"type": "BUY", "symbol": "AAA", "reason": "x"})
        self.assertEqual(raw["queued_orders"], [{"type": "BUY", "symbol": "AAA", "reason": "x"}])


if __name__ == "__main__":
    unittest.main()
