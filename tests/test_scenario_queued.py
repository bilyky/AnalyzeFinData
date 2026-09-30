"""B6 tests for aether.scenario.steps.execute_queued_orders.

``execute_queued_orders`` is the fourth stateful stage lifted out of
``run_daily_ai_management`` — the ``# 0. Execute QUEUED ORDERS`` block that runs
after the price gate / book settlement (:func:`price_and_settle`) and before the
SELL decision loop. A queued order is a strategic override placed on a prior run
that fills at today's live price. These tests pin the behaviours it carries over
from the root, unchanged:

1. **Queue lifecycle** — an empty queue returns early and leaves
   ``state["queued_orders"]`` untouched (keying off the *passed* ``queued``, not
   ``state``); a non-empty queue is reset to ``[]`` only after it runs.
2. **Queued SELL** — unwinds option liability, pops the position, credits
   proceeds, records the transaction (history + ``new_transactions``) with the
   correct PnL, and logs the closed-trade DNA. A SELL for a symbol not held is a
   no-op. A price ``<= 0`` skips the order.
3. **Queued BUY** — the execution-time Zero-Trust freshness gate (heal once, then
   skip if still stale); profile slot / allocation sizing; ATR stop with the 8%
   fallback; buy-DNA resolution off the Research sheet (``r_row[3]``/``[6]``/
   ``[4]``/``[24]``/``[25]``); a BUY for a symbol already held, an empty slot
   book, a too-low balance, or ``qty == 0`` all no-op.
4. **Call-time seam** (``_pkg()``) and the package re-export.

Every collaborator (``_log``, ``options.unwind_option_liability_if_held``,
``log_closed_trade_dna``, ``_cache_stale`` / ``_heal_symbol_cache`` /
``_MAX_STALE_DAYS``, ``calculate_share_qty``, ``risk_utils.calculate_atr``) is
patched on the live module, and ``ws`` is a hermetic fake — no network or disk.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import execute_queued_orders as execute_reexport  # noqa: E402
from aether.scenario.steps import execute_queued_orders  # noqa: E402


_TODAY = "2026-09-25"
_NOW = "10:00 AM"
_RULES = {"max_positions": 5, "max_allocation_pct": 0.2, "atr_multiplier": 2.0}
_BUY_BALANCE = 10000.0


class _FakeWS:
    """Minimal openpyxl worksheet stand-in: ``iter_rows`` yields row tuples."""

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=2, values_only=True):
        return list(self._rows)


def _row(sym, pgr="Bullish", industry="Tech", s10=1.0, l60=2.0):
    """A Research-sheet row: sym at [3], industry [4], pgr [6], s10 [24], l60 [25]."""
    r = [None] * 26
    r[3] = sym
    r[4] = industry
    r[6] = pgr
    r[24] = s10
    r[25] = l60
    return tuple(r)


def _sell_state():
    return {
        "balance": 1000.0,
        "equity": 5000.0,
        "positions": {"AAA": {"qty": 10, "cost": 50.0}},
        "history": [],
        "queued_orders": [],
    }


def _buy_state():
    return {
        "balance": _BUY_BALANCE,
        "equity": _BUY_BALANCE,
        "positions": {},
        "history": [],
        "queued_orders": [],
    }


def _patch(*, stale=False, heal_makes_fresh=False, atr=None, qty=10):
    """Patch the collaborators execute_queued_orders resolves via _pkg().

    ``stale`` toggles the execution-time freshness gate; ``heal_makes_fresh``
    decides whether the post-heal re-check clears; ``atr`` is the
    ``calculate_atr`` return (``None`` ⇒ 8% fallback); ``qty`` the
    ``calculate_share_qty`` return.
    """
    m_log = mock.MagicMock()
    m_unwind = mock.MagicMock()
    m_dna = mock.MagicMock()
    m_heal = mock.MagicMock()
    m_share = mock.MagicMock(return_value=qty)
    m_atr = mock.MagicMock(return_value=atr)

    # _cache_stale: fresh unless `stale`; when stale the pre-heal check is True and
    # the post-heal re-check clears only if `heal_makes_fresh`.
    calls = {"n": 0}

    def _stale(sym, max_stale_days=None):
        if not stale:
            return False
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        return not heal_makes_fresh

    ctxs = [
        mock.patch.object(game, "_log", m_log),
        mock.patch.object(game.options, "unwind_option_liability_if_held", m_unwind),
        mock.patch.object(game, "log_closed_trade_dna", m_dna),
        mock.patch.object(game, "_cache_stale", side_effect=_stale),
        mock.patch.object(game, "_MAX_STALE_DAYS", 3),
        mock.patch.object(game, "_heal_symbol_cache", m_heal),
        mock.patch.object(game, "calculate_share_qty", m_share),
        mock.patch.object(game.risk_utils, "calculate_atr", m_atr),
    ]
    return ctxs, m_log, m_unwind, m_dna, m_heal, m_share, m_atr


def _run(state, queued, *, prices=None, rules=None, ws=None, new_transactions=None, **kw):
    prices = {} if prices is None else prices
    rules = rules or _RULES
    ws = _FakeWS([]) if ws is None else ws
    new_transactions = [] if new_transactions is None else new_transactions
    ctxs, m_log, m_unwind, m_dna, m_heal, m_share, m_atr = _patch(**kw)
    with ExitStack() as stack:
        for c in ctxs:
            stack.enter_context(c)
        result = execute_queued_orders(
            state, queued, prices, rules, ws, _TODAY, _NOW, new_transactions)
    return result, new_transactions, m_log, m_unwind, m_dna, m_heal, m_share, m_atr


class TestQueueLifecycle(unittest.TestCase):
    def test_empty_queue_returns_early_and_leaves_key(self):
        state = {"positions": {}, "queued_orders": ["SENTINEL"]}
        result, new_tx, m_log, *_ = _run(state, [])
        self.assertIsNone(result)
        self.assertEqual(state["queued_orders"], ["SENTINEL"])  # keyed off `queued`, untouched
        m_log.info.assert_not_called()
        self.assertEqual(new_tx, [])

    def test_queue_reset_after_execution(self):
        order = {"symbol": "AAA", "type": "SELL", "reason": "strategic"}
        state = _sell_state()
        state["queued_orders"] = [order]
        _run(state, [order], prices={"AAA": 60.0})
        self.assertEqual(state["queued_orders"], [])


class TestQueuedSell(unittest.TestCase):
    def test_queued_sell_executes(self):
        order = {"symbol": "AAA", "type": "SELL", "reason": "take profit"}
        state = _sell_state()
        _, new_tx, _, m_unwind, m_dna, *_ = _run(state, [order], prices={"AAA": 60.0})
        self.assertNotIn("AAA", state["positions"])
        self.assertEqual(state["balance"], 1000.0 + 10 * 60.0)  # proceeds credited
        self.assertEqual(len(new_tx), 1)
        tx = new_tx[0]
        self.assertEqual(tx["type"], "SELL")
        self.assertEqual(tx["symbol"], "AAA")
        self.assertEqual(tx["qty"], 10)
        self.assertEqual(tx["pnl"], round((60.0 - 50.0) * 10, 2))
        self.assertIn("take profit", tx["details"])
        self.assertEqual(state["history"], new_tx)  # appended to both
        m_unwind.assert_called_once_with("AAA", mock.ANY, state, 60.0, _TODAY)
        m_dna.assert_called_once()

    def test_queued_sell_records_stop_loss(self):
        # Parity with the root after #136: the SELL tx carries the position's stop
        # (None when the position never had one).
        order = {"symbol": "AAA", "type": "SELL", "reason": "x"}
        for pos_stop, expected in ((45.0, 45.0), (None, None)):
            with self.subTest(stop=pos_stop):
                state = _sell_state()
                if pos_stop is not None:
                    state["positions"]["AAA"]["stop_loss"] = pos_stop
                _, new_tx, *_ = _run(state, [order], prices={"AAA": 60.0})
                self.assertEqual(new_tx[0]["stop_loss"], expected)

    def test_queued_sell_symbol_not_held_is_noop(self):
        order = {"symbol": "ZZZ", "type": "SELL", "reason": "x"}
        state = _sell_state()
        _, new_tx, _, m_unwind, m_dna, *_ = _run(state, [order], prices={"ZZZ": 10.0})
        self.assertEqual(new_tx, [])
        m_unwind.assert_not_called()
        m_dna.assert_not_called()
        self.assertIn("AAA", state["positions"])  # untouched

    def test_nonpositive_price_skips_order(self):
        order = {"symbol": "AAA", "type": "SELL", "reason": "x"}
        state = _sell_state()
        _, new_tx, _, m_unwind, *_ = _run(state, [order], prices={"AAA": 0})
        self.assertEqual(new_tx, [])
        m_unwind.assert_not_called()
        self.assertIn("AAA", state["positions"])

    def test_missing_price_skips_order(self):
        order = {"symbol": "AAA", "type": "SELL", "reason": "x"}
        state = _sell_state()
        _, new_tx, _, m_unwind, *_ = _run(state, [order], prices={})  # AAA absent → 0
        self.assertEqual(new_tx, [])
        m_unwind.assert_not_called()
        self.assertIn("AAA", state["positions"])


class TestQueuedBuy(unittest.TestCase):
    def test_queued_buy_executes_with_atr_stop(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "breakout"}
        state = _buy_state()
        ws = _FakeWS([_row("BBB", pgr="Very Bullish", industry="Semis", s10=3.0, l60=4.0)])
        _, new_tx, _, _, _, _, m_share, m_atr = _run(
            state, [order], prices={"BBB": 20.0}, ws=ws, atr=1.5, qty=10)
        self.assertIn("BBB", state["positions"])
        pos = state["positions"]["BBB"]
        self.assertEqual(pos["qty"], 10)
        self.assertEqual(pos["cost"], 20.0)
        self.assertEqual(pos["stop_loss"], 17.0)  # 20 - 2.0*1.5
        dna = pos["buy_dna"]
        self.assertEqual(dna["pgr"], "Very Bullish")
        self.assertEqual(dna["industry"], "Semis")
        self.assertEqual(dna["s10"], 3.0)
        self.assertEqual(dna["l60"], 4.0)
        self.assertEqual(dna["score"], 7.0)
        self.assertEqual(dna["buy_date"], _TODAY)
        self.assertEqual(dna["z_score"], 0.0)
        self.assertEqual(state["balance"], _BUY_BALANCE - 10 * 20.0)
        self.assertEqual(len(new_tx), 1)
        self.assertEqual(new_tx[0]["type"], "BUY")
        self.assertIn("ATR-based Stop", new_tx[0]["details"])

    def test_queued_buy_uses_8pct_fallback_when_no_atr(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        ws = _FakeWS([_row("BBB")])
        _run(state, [order], prices={"BBB": 100.0}, ws=ws, atr=None, qty=1)
        pos = state["positions"]["BBB"]
        self.assertEqual(pos["stop_loss"], round(100.0 * 0.92, 2))  # 92.0
        # DNA defaults survive a bare row match, too.
        self.assertEqual(pos["buy_dna"]["pgr"], "Bullish")

    def test_queued_buy_dna_defaults_when_symbol_absent_from_sheet(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        ws = _FakeWS([_row("OTHER")])  # BBB not on the sheet
        _run(state, [order], prices={"BBB": 20.0}, ws=ws, atr=1.0, qty=1)
        dna = state["positions"]["BBB"]["buy_dna"]
        self.assertEqual(dna["pgr"], "Neutral")
        self.assertEqual(dna["industry"], "Unknown")
        self.assertEqual(dna["s10"], 0.0)
        self.assertEqual(dna["l60"], 0.0)

    def test_queued_buy_skipped_when_stale_after_heal(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        _, new_tx, m_log, _, _, m_heal, m_share, _ = _run(
            state, [order], prices={"BBB": 20.0}, stale=True, heal_makes_fresh=False)
        self.assertNotIn("BBB", state["positions"])
        self.assertEqual(new_tx, [])
        m_heal.assert_called_once_with("BBB")
        m_share.assert_not_called()  # never sized on stale data
        self.assertTrue(m_log.warning.called)

    def test_queued_buy_proceeds_when_heal_makes_fresh(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        ws = _FakeWS([_row("BBB")])
        _, new_tx, _, _, _, m_heal, _, _ = _run(
            state, [order], prices={"BBB": 20.0}, ws=ws,
            stale=True, heal_makes_fresh=True, atr=1.0, qty=5)
        m_heal.assert_called_once_with("BBB")
        self.assertIn("BBB", state["positions"])
        self.assertEqual(len(new_tx), 1)

    def test_queued_buy_symbol_already_held_is_noop(self):
        order = {"symbol": "AAA", "type": "BUY", "reason": "x"}
        state = _sell_state()  # already holds AAA
        _, new_tx, _, _, _, _, m_share, _ = _run(state, [order], prices={"AAA": 60.0})
        m_share.assert_not_called()
        self.assertEqual(new_tx, [])
        self.assertEqual(state["positions"]["AAA"]["qty"], 10)  # untouched

    def test_queued_buy_no_fill_when_balance_too_low(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        state["balance"] = 500.0  # not strictly > 500
        _, new_tx, _, _, _, _, m_share, _ = _run(state, [order], prices={"BBB": 20.0})
        m_share.assert_not_called()
        self.assertNotIn("BBB", state["positions"])

    def test_queued_buy_no_fill_when_no_slots(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        state["positions"] = {f"H{i}": {"qty": 1, "cost": 1} for i in range(5)}  # == max
        _, new_tx, _, _, _, _, m_share, _ = _run(state, [order], prices={"BBB": 20.0})
        m_share.assert_not_called()
        self.assertNotIn("BBB", state["positions"])

    def test_queued_buy_no_fill_when_qty_zero(self):
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        ws = _FakeWS([_row("BBB")])
        _, new_tx, _, _, _, _, m_share, _ = _run(
            state, [order], prices={"BBB": 20.0}, ws=ws, atr=1.0, qty=0)
        m_share.assert_called_once()  # sizing attempted
        self.assertNotIn("BBB", state["positions"])  # but no fill
        self.assertEqual(new_tx, [])


class TestMixedQueueAndSeam(unittest.TestCase):
    def test_mixed_queue_sell_then_buy_in_order(self):
        orders = [
            {"symbol": "AAA", "type": "SELL", "reason": "exit"},
            {"symbol": "BBB", "type": "BUY", "reason": "enter"},
        ]
        state = _sell_state()
        state["balance"] = _BUY_BALANCE
        state["equity"] = _BUY_BALANCE
        ws = _FakeWS([_row("BBB")])
        _, new_tx, *_ = _run(
            state, orders, prices={"AAA": 60.0, "BBB": 20.0}, ws=ws, atr=1.0, qty=3)
        self.assertNotIn("AAA", state["positions"])
        self.assertIn("BBB", state["positions"])
        self.assertEqual([t["type"] for t in new_tx], ["SELL", "BUY"])
        self.assertEqual(state["queued_orders"], [])

    def test_share_qty_resolved_at_call_time(self):
        # The whole point of _pkg(): the patch applied now is the one used.
        order = {"symbol": "BBB", "type": "BUY", "reason": "x"}
        state = _buy_state()
        ws = _FakeWS([_row("BBB")])
        _, _, _, _, _, _, m_share, _ = _run(
            state, [order], prices={"BBB": 20.0}, ws=ws, atr=1.0, qty=7)
        m_share.assert_called_once()
        self.assertEqual(state["positions"]["BBB"]["qty"], 7)

    def test_reexported_from_package_root(self):
        self.assertIs(execute_reexport, execute_queued_orders)


if __name__ == "__main__":
    unittest.main()
