"""B6 tests for aether.scenario.steps.deploy_capital.

``deploy_capital`` is the last trading block of ``run_daily_ai_management``,
lifted out after :func:`rotate_positions`. These tests pin the behaviours it
carries over from the root, unchanged:

1. **Entry gate** — nothing happens without a free slot and cash strictly above
   the buffer.
2. **After hours** — the top ``available_slots`` candidates are queued as BUY
   orders, deduped by symbol; nothing is bought.
3. **Market hours** — ``_execute_buys`` gets the candidates, then pyramiding
   (idle-cash trigger, winner/peak test, Research-sheet s10/l60, $500 room
   floor, cash capped by the buffer, blended cost, ``BUY_SCALE_IN`` tx), then
   the covered-call pass if the market is still open.

Every collaborator is patched on the live module and ``ws`` is a hermetic fake —
no network or disk. Re-export and root parity are pinned in
``test_scenario_steps`` and ``test_scenario_parity``.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario.steps import deploy_capital  # noqa: E402


_TODAY = "2026-10-12"
_NOW = "10:30:00"
_RULES = {"max_allocation_pct": 0.2, "cash_buffer_pct": 0.2}


class _FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=2, values_only=True):
        return list(self._rows)


def _row(sym, s10=4.0, l60=5.0):
    r = [None] * 26
    r[3], r[24], r[25] = sym, s10, l60
    return tuple(r)


def _state(balance=6000.0, equity=10000.0, **positions):
    return {"balance": balance, "equity": equity, "history": [], "queued_orders": [],
            "positions": dict(positions or {"WIN": {"qty": 10, "cost": 100.0, "stop_loss": 100.0,
                                                   "highest_close_since_acq": 110.0}})}


class _Harness:
    """Patch every collaborator deploy_capital resolves via _pkg()."""

    def __init__(self, market=(True, True), ratio=0.5, pyramid=True, qty=8):
        self.log = mock.MagicMock()
        self.market = mock.MagicMock(side_effect=list(market))
        self.execute = mock.MagicMock()
        self.pyramid = mock.MagicMock(return_value=pyramid)
        self.qty = mock.MagicMock(return_value=qty)
        self.calls = mock.MagicMock()
        self.ratio = ratio

    def __enter__(self):
        self._stack = ExitStack()
        for ctx in (
            mock.patch.object(game, "_log", self.log),
            mock.patch.object(game, "is_market_hours", self.market),
            mock.patch.object(game, "_execute_buys", self.execute),
            mock.patch.object(game, "should_pyramid_into_winner", self.pyramid),
            mock.patch.object(game, "calculate_share_qty", self.qty),
            mock.patch.object(game.options, "execute_weekly_covered_call_pass", self.calls),
            mock.patch.object(game.CFG, "system_pyramiding_cash_ratio", self.ratio),
        ):
            self._stack.enter_context(ctx)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    def logged(self):
        return " | ".join(str(c.args[0]) for c in self.log.info.call_args_list)


_BUYS = [{"sym": "AAA"}, {"sym": "BBB"}, {"sym": "CCC"}]


def _run(state, prices=None, rows=(_row("WIN"),), top_buys=_BUYS, slots=2, min_cash=2000.0, txs=None):
    txs = [] if txs is None else txs
    deploy_capital(state, {"WIN": 110.0} if prices is None else prices, _RULES, _FakeWS(rows),
                   top_buys, slots, min_cash, _TODAY, _NOW, txs)
    return txs


class TestEntryGate(unittest.TestCase):
    def test_no_slot_or_no_spare_cash_does_nothing(self):
        for slots, balance in ((0, 6000.0), (2, 2000.0)):
            with self.subTest(slots=slots, balance=balance), _Harness() as h:
                state = _state(balance=balance)
                self.assertEqual(_run(state, slots=slots), [])
                h.market.assert_not_called()
                self.assertEqual(state["queued_orders"], [])


class TestAfterHours(unittest.TestCase):
    def test_queues_top_candidates_up_to_the_slots_deduped(self):
        state = _state()
        state["queued_orders"] = [{"type": "BUY", "symbol": "AAA"}, {"type": "SELL", "symbol": "BBB"}]
        with _Harness(market=(False,)) as h:
            txs = _run(state, slots=2)
        self.assertEqual(state["queued_orders"][2:], [
            {"type": "BUY", "symbol": "BBB", "reason": "Top-ranked quantitative pick (After-Hours)"}])
        self.assertEqual(txs, [])
        h.execute.assert_not_called()
        h.calls.assert_not_called()
        self.assertIn("After-hours BUY queued for BBB", h.logged())

    def test_creates_the_queue_when_missing(self):
        state = _state()
        del state["queued_orders"]
        with _Harness(market=(False,)):
            _run(state, slots=1)
        self.assertEqual([q["symbol"] for q in state["queued_orders"]], ["AAA"])


class TestMarketHoursBuys(unittest.TestCase):
    def test_execute_buys_gets_the_candidates(self):
        state, prices, txs = _state(), {"WIN": 110.0}, []
        with _Harness(ratio=1.0) as h:
            _run(state, prices=prices, slots=2, min_cash=2000.0, txs=txs)
        h.execute.assert_called_once_with(state, _BUYS, 2, 2000.0, _RULES, _TODAY, _NOW, txs, prices)


class TestPyramiding(unittest.TestCase):
    def test_scale_in_buys_blends_cost_and_records_tx(self):
        state = _state()
        pos = state["positions"]["WIN"]
        with _Harness() as h:
            txs = _run(state)
        h.pyramid.assert_called_once_with(True, True, 4.0, 5.0)
        # room = 0.2*10000 - 10*110 = 900; spare cash = 6000 - 0.2*10000 = 4000 -> deploy 900
        h.qty.assert_called_once_with("WIN", 900.0, 110.0)
        self.assertEqual(state["balance"], 6000.0 - 8 * 110.0)
        self.assertEqual((pos["qty"], pos["cost"]), (18, 104.44))
        self.assertEqual(txs, [{"date": _TODAY, "time": _NOW, "type": "BUY_SCALE_IN", "symbol": "WIN",
                                "price": 110.0, "qty": 8, "details": "Pyramiding Scale-In (Breakout Peak, s10=4.0)"}])
        self.assertIs(state["history"][0], txs[0])
        self.assertIn("Added 8 shares to WIN @ $110.00 (Blended Cost: $104.44)", h.logged())

    def test_no_scale_in_after_a_same_day_sell(self):
        # #181 (PROD GNE 2026-10-02): never buy back what was sold earlier today.
        cases = [
            ({"symbol": "WIN", "type": "SELL", "date": _TODAY}, False),
            ({"symbol": "WIN", "type": "SELL", "date": "2026-10-11"}, True),
            ({"symbol": "OTHER", "type": "SELL", "date": _TODAY}, True),
            ({"symbol": "WIN", "type": "BUY", "date": _TODAY}, True),
        ]
        for prior, scaled in cases:
            with self.subTest(prior=prior), _Harness() as h:
                state = _state()
                state["history"].append(prior)
                txs = _run(state)
                self.assertEqual(bool(txs), scaled)
                self.assertEqual(h.pyramid.called, scaled)
                self.assertEqual("WIN: sold earlier today; no scale-in." in h.logged(), not scaled)

    def test_spare_cash_caps_the_deploy_amount(self):
        state = _state(balance=2300.0, equity=10000.0)  # ratio 0.23 > 0.2; spare = 300
        with _Harness(ratio=0.2) as h:
            _run(state)
        h.qty.assert_called_once_with("WIN", 300.0, 110.0)

    def test_idle_cash_trigger_is_strict(self):
        for balance, equity, ratio in ((5000.0, 10000.0, 0.5), (5000.0, 1.0, -1.0)):
            with self.subTest(balance=balance, equity=equity), _Harness(ratio=ratio) as h:
                state = _state(balance=balance, equity=equity)
                _run(state, min_cash=0.0)
                if equity > 1.0:
                    h.pyramid.assert_not_called()
                else:  # degenerate equity -> cash_ratio 0.0, still above a negative trigger
                    h.pyramid.assert_called_once()

    def test_winner_and_peak_inputs(self):
        cases = [
            # (price, peak, row s10/l60, expected call)
            (100.0, 110.0, (4.0, 5.0), (False, True, 4.0, 5.0)),   # not above cost
            (110.0, 107.8, (4.0, 5.0), (True, True, 4.0, 5.0)),    # exactly 98% of price
            (110.0, 107.0, (4.0, 5.0), (True, False, 4.0, 5.0)),   # below 98%
            (110.0, 110.0, (None, None), (True, True, 0.0, 0.0)),  # empty score cells
        ]
        for px, peak, (s10, l60), expected in cases:
            with self.subTest(px=px, peak=peak, s10=s10), _Harness(pyramid=False) as h:
                state = _state(WIN={"qty": 10, "cost": 100.0, "stop_loss": 100.0, "highest_close_since_acq": peak})
                _run(state, prices={"WIN": px}, rows=(_row("OTHER", 9.0, 9.0), _row("WIN", s10, l60)))
                h.pyramid.assert_called_once_with(*expected)

    def test_unlisted_symbol_and_unquoted_price_use_defaults(self):
        state = _state(WIN={"qty": 10, "cost": 100.0, "stop_loss": 100.0})
        with _Harness(pyramid=False) as h:
            _run(state, prices={}, rows=())
        h.pyramid.assert_called_once_with(False, False, 0.0, 0.0)

    def test_room_must_exceed_500(self):
        # room = 0.2*10000 - qty*100: qty 14 -> 600, qty 15 -> exactly 500 (not enough)
        for qty, sized in ((14, True), (15, False)):
            with self.subTest(qty=qty), _Harness() as h:
                state = _state(WIN={"qty": qty, "cost": 90.0, "stop_loss": 90.0, "highest_close_since_acq": 100.0})
                _run(state, prices={"WIN": 100.0})
                self.assertEqual(h.qty.called, sized)

    def test_no_scale_in_while_the_stop_is_below_cost(self):
        state = _state(WIN={"qty": 10, "cost": 100.0, "stop_loss": 99.0, "highest_close_since_acq": 110.0})
        with _Harness() as h:
            txs = _run(state)
        self.assertEqual(txs, [])
        h.qty.assert_not_called()
        self.assertIn("WIN: stop $99.00 is below cost $100.00; no scale-in.", h.logged())

    def test_scale_in_cash_is_capped_by_the_per_trade_loss_limit(self):
        # stop 100, price 110: $10 at risk per share; 1% of $10,000 = $100 -> at most $1,100 of stock.
        state = _state(balance=9000.0, WIN={"qty": 1, "cost": 100.0, "stop_loss": 100.0,
                                            "highest_close_since_acq": 110.0})
        with _Harness() as h, mock.patch.object(game.CFG, "system_max_trade_risk_pct", 0.01):
            _run(state)
        sym, cash, px = h.qty.call_args.args
        self.assertEqual((sym, px), ("WIN", 110.0))
        self.assertAlmostEqual(cash, 1100.0)

    def test_zero_shares_changes_nothing(self):
        state = _state()
        with _Harness(qty=0):
            txs = _run(state)
        self.assertEqual((txs, state["balance"], state["positions"]["WIN"]["qty"]), ([], 6000.0, 10))


class TestCoveredCalls(unittest.TestCase):
    def test_written_only_if_the_market_is_still_open(self):
        for second_read, written in ((True, True), (False, False)):
            with self.subTest(second_read=second_read), _Harness(market=(True, second_read), ratio=1.0) as h:
                state, prices, ws = _state(), {"WIN": 110.0}, _FakeWS(())
                deploy_capital(state, prices, _RULES, ws, _BUYS, 2, 2000.0, _TODAY, _NOW, [])
                if written:
                    h.calls.assert_called_once_with(state, _TODAY, prices, ws)
                else:
                    h.calls.assert_not_called()
                self.assertEqual(h.market.call_count, 2)


if __name__ == "__main__":
    unittest.main()
