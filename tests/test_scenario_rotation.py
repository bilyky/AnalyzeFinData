"""B6 tests for aether.scenario.steps.rotate_positions.

``rotate_positions`` is the R&D #27 momentum-rotation block lifted out of
``run_daily_ai_management`` — it runs after :func:`screen_buys` and before BUY
execution. These tests pin the behaviours it carries over from the root,
unchanged:

1. **Decision** — ``evaluate_momentum_rotation`` gets the profile, the
   market-hours flag, the slots, the live positions dict, prices, the ranked
   candidates and the held scores; its new slot count is returned.
2. **Each rotation sell** — a written call is unwound at the market price
   (cost when unquoted) while still held, the position is popped, one SELL tx
   lands in ``history`` and ``new_transactions``, and the DNA is logged.
3. **Cash** — the loop credits nothing; ``balance_addition`` is added once.

Every collaborator is patched on the live module — no network or disk.
Re-export and root parity are pinned in ``test_scenario_steps`` and
``test_scenario_parity``.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario.steps import rotate_positions  # noqa: E402


_TODAY = "2026-10-08"
_NOW = "11:00:00"


class _Harness:
    """Patch every collaborator rotate_positions resolves via _pkg()."""

    def __init__(self, sells=(), slots=1, addition=0.0, market=True, unwind=None):
        self.log = mock.MagicMock()
        self.rotation = mock.MagicMock(return_value=(list(sells), slots, addition))
        self.market = mock.MagicMock(return_value=market)
        self.unwind = mock.MagicMock(side_effect=unwind)
        self.dna = mock.MagicMock()

    def __enter__(self):
        self._stack = ExitStack()
        for ctx in (
            mock.patch.object(game, "_log", self.log),
            mock.patch.object(game, "evaluate_momentum_rotation", self.rotation),
            mock.patch.object(game, "is_market_hours", self.market),
            mock.patch.object(game.options, "unwind_option_liability_if_held", self.unwind),
            mock.patch.object(game, "log_closed_trade_dna", self.dna),
        ):
            self._stack.enter_context(ctx)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _state():
    return {"balance": 100.0, "history": [], "positions": {
        "OLD": {"qty": 10, "cost": 50.0}, "KEEP": {"qty": 1, "cost": 5.0}}}


def _run(state, prices=None, scores=None, top_buys=None, slots=0):
    txs = []
    out = rotate_positions(state, {"OLD": 60.0} if prices is None else prices, "AGGRESSIVE", slots, 5,
                           [] if top_buys is None else top_buys, {"OLD": 3.25} if scores is None else scores,
                           _TODAY, _NOW, txs)
    return out, txs


class TestDecision(unittest.TestCase):
    def test_rotation_inputs_and_returned_slots(self):
        state, buys, prices, scores = _state(), [{"sym": "NEW", "total": 9.0}], {"OLD": 60.0}, {"OLD": 3.25}
        with _Harness(slots=2, market=False) as h:
            out, txs = _run(state, prices=prices, scores=scores, top_buys=buys, slots=0)
        self.assertEqual(out, 2)
        args = h.rotation.call_args.args
        self.assertEqual(args[:4], ("AGGRESSIVE", False, 0, 5))
        self.assertIs(args[4], state["positions"])
        self.assertIs(args[5], prices)
        self.assertIs(args[6], buys)
        self.assertIs(args[7], scores)

    def test_no_rotation_sells_nothing_but_still_adds_the_cash(self):
        state = _state()
        with _Harness(addition=25.0) as h:
            _, txs = _run(state)
        self.assertEqual((txs, state["balance"], len(state["positions"])), ([], 125.0, 2))
        h.dna.assert_not_called()


class TestRotationSell(unittest.TestCase):
    def test_sell_records_one_tx_and_logs_dna(self):
        state = _state()
        pos = state["positions"]["OLD"]
        with _Harness(sells=["OLD"], addition=600.0) as h:
            _, txs = _run(state)
        self.assertEqual(list(state["positions"]), ["KEEP"])
        self.assertEqual(txs, [{
            "date": _TODAY, "time": _NOW, "type": "SELL", "symbol": "OLD", "price": 60.0, "qty": 10,
            "pnl": 100.0,
            "details": "🔄 [MOMENTUM ROTATION] Sold mature position OLD (Score: 3.2) to free slot."}])
        self.assertIs(state["history"][0], txs[0])
        self.assertEqual(state["balance"], 700.0)  # balance_addition only, not qty*price again
        h.dna.assert_called_once_with("OLD", pos, 60.0, _TODAY)
        self.assertIn("(Score: 3.2) @ $60.0 to open slot.", h.log.info.call_args.args[0])

    def test_unquoted_uses_cost_and_missing_score_is_zero(self):
        state = _state()
        with _Harness(sells=["OLD"]):
            _, txs = _run(state, prices={}, scores={})
        self.assertEqual((txs[0]["price"], txs[0]["pnl"]), (50.0, 0.0))
        self.assertIn("(Score: 0.0)", txs[0]["details"])

    def test_call_unwound_at_market_price_while_still_held(self):
        state, seen = _state(), {}

        def unwind(sym, pos, st, price, today):
            seen.update(sym=sym, held=sym in st["positions"], price=price, today=today)

        with _Harness(sells=["OLD"], unwind=unwind):
            _run(state)
        self.assertEqual(seen, {"sym": "OLD", "held": True, "price": 60.0, "today": _TODAY})

    def test_sells_in_the_order_rotation_returns(self):
        state = _state()
        with _Harness(sells=["KEEP", "OLD"]):
            _, txs = _run(state, prices={"OLD": 60.0, "KEEP": 6.0})
        self.assertEqual([t["symbol"] for t in txs], ["KEEP", "OLD"])
        self.assertEqual(state["positions"], {})


if __name__ == "__main__":
    unittest.main()
