"""B6 tests for aether.scenario.steps.execute_exits.

``execute_exits`` is the live SELL execution loop lifted out of
``run_daily_ai_management`` — it runs right after :func:`decide_exits` and
liquidates the ``{symbol: exit_reason}`` dict that stage returns. These tests pin
the behaviours it carries over from the root, unchanged:

1. **Fill + bookkeeping** — the position is popped, ``qty * price`` is credited,
   and one SELL tx (``pnl``, ``Exit: <reason>``, ``stop_loss``) lands in both
   ``state["history"]`` and ``new_transactions``; the price falls back to cost
   when unquoted.
2. **STP LMT @ market** (R&D #8) — at or below a positive stop the order is a
   stop-limit whose limit is the market price, so the fill is the market price
   (never the stop: a gap below the stop is a real loss) and the details get
   ``[STP LMT @ market, stop <stop>]``; no stop (0 or missing) means a plain
   market fill.
3. **Order of side effects** — a written call is unwound at the market price
   while the position is still held; the closed-trade DNA gets the final fill.

Every collaborator is patched on the live module — no network or disk. The
package re-export is pinned once, in ``test_scenario_steps.TestPackageReexports``,
and root parity in ``test_scenario_parity``.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario.steps import execute_exits  # noqa: E402


_TODAY = "2026-10-07"
_NOW = "10:00:00"


def _state(**positions):
    return {"balance": 1000.0, "positions": positions, "history": []}


class _Harness:
    """Patch every collaborator execute_exits resolves via _pkg()."""

    def __init__(self, unwind=None):
        self.log = mock.MagicMock()
        self.unwind = mock.MagicMock(side_effect=unwind)
        self.dna = mock.MagicMock()

    def __enter__(self):
        self._stack = ExitStack()
        for ctx in (
            mock.patch.object(game, "_log", self.log),
            mock.patch.object(game.options, "unwind_option_liability_if_held", self.unwind),
            mock.patch.object(game, "log_closed_trade_dna", self.dna),
        ):
            self._stack.enter_context(ctx)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _run(state, to_sell, prices):
    txs = []
    execute_exits(state, to_sell, prices, _TODAY, _NOW, txs)
    return txs


class TestFill(unittest.TestCase):
    def test_market_fill_pops_credits_and_records_one_tx(self):
        pos = {"qty": 10, "cost": 100.0, "stop_loss": 90.0}
        state = _state(AAA=pos, BBB={"qty": 1, "cost": 5.0})
        with _Harness() as h:
            txs = _run(state, {"AAA": "Momentum fade"}, {"AAA": 120.0})
        self.assertNotIn("AAA", state["positions"])
        self.assertIn("BBB", state["positions"])  # only listed symbols are sold
        self.assertEqual(state["balance"], 1000.0 + 1200.0)
        self.assertEqual(txs, [{
            "date": _TODAY, "time": _NOW, "type": "SELL", "symbol": "AAA", "price": 120.0,
            "qty": 10, "pnl": 200.0, "details": "Exit: Momentum fade", "stop_loss": 90.0}])
        self.assertIs(state["history"][0], txs[0])
        h.dna.assert_called_once_with("AAA", pos, 120.0, _TODAY)
        self.assertIn("AI LIVE SELL: AAA at $120.0", h.log.info.call_args.args[0])

    def test_unquoted_symbol_fills_at_cost(self):
        state = _state(AAA={"qty": 2, "cost": 50.0, "stop_loss": 40.0})
        with _Harness():
            txs = _run(state, {"AAA": "r"}, {})
        self.assertEqual((txs[0]["price"], txs[0]["pnl"]), (50.0, 0.0))
        self.assertEqual(state["balance"], 1100.0)

    def test_sells_in_dict_order_and_empty_is_a_no_op(self):
        state = _state(AAA={"qty": 1, "cost": 1.0}, BBB={"qty": 1, "cost": 1.0})
        with _Harness():
            self.assertEqual(_run(state, {}, {}), [])
            txs = _run(state, {"BBB": "b", "AAA": "a"}, {"AAA": 2.0, "BBB": 3.0})
        self.assertEqual([t["symbol"] for t in txs], ["BBB", "AAA"])
        self.assertEqual(state["positions"], {})


class TestStopLimitFill(unittest.TestCase):
    def test_gap_below_stop_fills_at_market_price(self):
        state = _state(AAA={"qty": 10, "cost": 100.0, "stop_loss": 90.0})
        with _Harness() as h:
            txs = _run(state, {"AAA": "Hard stop"}, {"AAA": 80.0})
        self.assertEqual(txs[0]["price"], 80.0)
        self.assertEqual(txs[0]["pnl"], -200.0)
        self.assertEqual(txs[0]["details"], "Exit: Hard stop [STP LMT @ market, stop 90.00]")
        self.assertEqual(state["balance"], 1800.0)
        self.assertIn("[STP LMT]", h.log.info.call_args_list[0].args[0])

    def test_price_exactly_at_stop_is_a_stop_fill(self):
        state = _state(AAA={"qty": 1, "cost": 100.0, "stop_loss": 90.0})
        with _Harness():
            txs = _run(state, {"AAA": "r"}, {"AAA": 90.0})
        self.assertEqual(txs[0]["price"], 90.0)
        self.assertTrue(txs[0]["details"].endswith("[STP LMT @ market, stop 90.00]"))

    def test_no_stop_means_plain_market_fill(self):
        # A zero quote with no stop is still a market fill, not a stop fill at 0.
        cases = [(stop, px) for stop in (0.0, None) for px in (10.0, 0.0)]
        for stop, px in cases:
            with self.subTest(stop=stop, price=px):
                pos = {"qty": 1, "cost": 100.0}
                if stop is not None:
                    pos["stop_loss"] = stop
                state = _state(AAA=pos)
                with _Harness():
                    txs = _run(state, {"AAA": "r"}, {"AAA": px})
                self.assertEqual(txs[0]["price"], px)
                self.assertEqual(txs[0]["details"], "Exit: r")
                self.assertEqual(txs[0]["stop_loss"], pos.get("stop_loss"))


class TestSideEffectOrder(unittest.TestCase):
    def test_call_unwound_at_market_price_while_still_held(self):
        state = _state(AAA={"qty": 10, "cost": 100.0, "stop_loss": 90.0})
        seen = {}

        def unwind(sym, pos, st, price, today):
            seen.update(held=sym in st["positions"], price=price, today=today)

        with _Harness(unwind=unwind) as h:
            _run(state, {"AAA": "r"}, {"AAA": 80.0})
        self.assertEqual(seen, {"held": True, "price": 80.0, "today": _TODAY})
        self.assertEqual(h.dna.call_args.args[2], 80.0)  # DNA gets the real fill, not the stop


if __name__ == "__main__":
    unittest.main()
