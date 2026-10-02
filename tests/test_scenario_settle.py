"""B6 tests for aether.scenario.steps.price_and_settle.

``price_and_settle`` is the third stateful stage lifted out of
``run_daily_ai_management`` — the block between the symbol-universe assembly
(:func:`assemble_symbol_universe`) and the queued-order / decision loops. It
establishes the day's single source of price truth and settles the book against
it. These tests pin the five behaviours it carries over from the root, unchanged:

1. **Live price fetch** — ``get_live_prices(all_syms)`` is called with the
   assembled universe, and its dict is returned to the caller.
2. **Price Source Gate** — an empty price dict raises the exact ``RuntimeError``
   (no stale-workbook trading), and the crash happens *before* the circuit
   breaker / options settlement run.
3. **Circuit breaker** — ``circuit_breaker.enforce_circuit_breaker`` is called
   once with ``(state, prices)``.
4. **Options settlement** — ``options.resolve_expiring_options`` is called once
   with ``(state, today, prices)``, and after the circuit breaker.
5. **Equity mark** — a held position with no live quote is surfaced via
   ``_log.console`` but does NOT abort the run (Rule of Loss Minimization);
   ``state["equity"]`` is set to the rounded ``_live_equity`` result.

Plus the ``_pkg()`` call-time seam and the package re-export. Every collaborator
(``get_live_prices``, ``circuit_breaker``, ``options``, ``_live_equity``,
``_log``) is patched on the live module — no network, disk, or openpyxl.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario import price_and_settle as settle_reexport  # noqa: E402
from aether.scenario.steps import price_and_settle  # noqa: E402


_TODAY = "2026-09-25"


def _patch(*, prices=None, equity=123456.0):
    """Patch the collaborators price_and_settle resolves via _pkg().

    ``prices`` is the ``get_live_prices`` return; ``equity`` is the
    ``_live_equity`` return. Returns the patch context managers plus the mocks so
    callers can assert on the calls.
    """
    m_prices = mock.MagicMock(return_value=({} if prices is None else prices))
    m_cb = mock.MagicMock()
    m_opt = mock.MagicMock()
    m_equity = mock.MagicMock(return_value=equity)
    m_log = mock.MagicMock()
    # circuit_breaker / options are submodules; patch the method on each.
    ctxs = [
        mock.patch.object(game, "get_live_prices", m_prices),
        mock.patch.object(game.circuit_breaker, "enforce_circuit_breaker", m_cb),
        mock.patch.object(game.options, "resolve_expiring_options", m_opt),
        mock.patch.object(game, "_live_equity", m_equity),
        mock.patch.object(game, "_log", m_log),
    ]
    return ctxs, m_prices, m_cb, m_opt, m_equity, m_log


def _run(state, all_syms, symbols_to_check, *, today=_TODAY, **kw):
    ctxs, m_prices, m_cb, m_opt, m_equity, m_log = _patch(**kw)
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        result = price_and_settle(state, all_syms, symbols_to_check, today)
    return result, m_prices, m_cb, m_opt, m_equity, m_log


def _state():
    return {"balance": 50000.0, "positions": {"AAA": {}}, "equity": 0}


class TestPriceFetchAndReturn(unittest.TestCase):
    def test_get_live_prices_called_with_universe(self):
        all_syms = ["AAA", "SPY"]
        _, m_prices, *_ = _run(_state(), all_syms, ["AAA"], prices={"AAA": 10.0, "SPY": 5.0})
        m_prices.assert_called_once_with(all_syms)

    def test_prices_dict_returned(self):
        prices = {"AAA": 10.0, "SPY": 5.0}
        result, *_ = _run(_state(), ["AAA", "SPY"], ["AAA"], prices=prices)
        self.assertEqual(result, prices)


class TestPriceSourceGate(unittest.TestCase):
    def test_empty_prices_raises_runtimeerror(self):
        with self.assertRaises(RuntimeError) as cm:
            _run(_state(), ["AAA"], ["AAA"], prices={})
        self.assertIn("Critical Data Failure", str(cm.exception))

    def test_gate_crashes_before_settlement(self):
        # The breaker / options must NOT run when there is no price truth.
        ctxs, m_prices, m_cb, m_opt, m_equity, m_log = _patch(prices={})
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
            with self.assertRaises(RuntimeError):
                price_and_settle(_state(), ["AAA"], ["AAA"], _TODAY)
        m_cb.assert_not_called()
        m_opt.assert_not_called()
        m_equity.assert_not_called()


class TestSettlement(unittest.TestCase):
    def test_circuit_breaker_called_with_state_and_prices(self):
        state = _state()
        prices = {"AAA": 10.0}
        _, _, m_cb, _, _, _ = _run(state, ["AAA"], ["AAA"], prices=prices)
        m_cb.assert_called_once_with(state, prices)

    def test_options_settlement_called_with_state_today_prices(self):
        state = _state()
        prices = {"AAA": 10.0}
        _, _, _, m_opt, _, _ = _run(state, ["AAA"], ["AAA"], prices=prices)
        m_opt.assert_called_once_with(state, _TODAY, prices)

    def test_breaker_runs_before_options(self):
        state = _state()
        order = []
        ctxs, m_prices, m_cb, m_opt, m_equity, m_log = _patch(prices={"AAA": 10.0})
        m_cb.side_effect = lambda *a, **k: order.append("cb")
        m_opt.side_effect = lambda *a, **k: order.append("opt")
        with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
            price_and_settle(state, ["AAA"], ["AAA"], _TODAY)
        self.assertEqual(order, ["cb", "opt"])


class TestEquityMark(unittest.TestCase):
    def test_equity_marked_from_live_equity_rounded(self):
        state = _state()
        result, _, _, _, m_equity, _ = _run(
            state, ["AAA"], ["AAA"], prices={"AAA": 10.0}, equity=123456.78)
        m_equity.assert_called_once_with(state["balance"], state["positions"], {"AAA": 10.0})
        self.assertEqual(state["equity"], 123457)  # round(123456.78) == 123457

    def test_missing_quote_warns_but_does_not_abort(self):
        # A held position with no live quote surfaces a console error but the run
        # continues (equity still marked, prices returned) — Rule of Loss Minimization.
        state = _state()
        result, _, _, _, _, m_log = _run(
            state, ["AAA", "SPY"], ["AAA"], prices={"SPY": 5.0})  # AAA missing
        self.assertEqual(result, {"SPY": 5.0})
        self.assertEqual(state["equity"], 123456)
        self.assertTrue(m_log.console.called)
        msg = m_log.console.call_args.args[0]
        self.assertIn("AAA", msg)

    def test_zero_or_negative_quote_counts_as_missing(self):
        state = _state()
        _, _, _, _, _, m_log = _run(
            state, ["AAA", "BBB"], ["AAA", "BBB"], prices={"AAA": 0, "BBB": -1})
        self.assertTrue(m_log.console.called)
        msg = m_log.console.call_args.args[0]
        self.assertIn("AAA", msg)
        self.assertIn("BBB", msg)

    def test_no_console_warn_when_all_quoted(self):
        state = _state()
        _, _, _, _, _, m_log = _run(
            state, ["AAA"], ["AAA"], prices={"AAA": 10.0})
        m_log.console.assert_not_called()


class TestSeamAndReexport(unittest.TestCase):
    def test_get_live_prices_resolved_at_call_time(self):
        # The whole point of _pkg(): the patch applied now is the one used.
        state = _state()
        sentinel = {"AAA": 42.0}
        result, m_prices, *_ = _run(state, ["AAA"], ["AAA"], prices=sentinel)
        self.assertEqual(result, sentinel)
        m_prices.assert_called_once()

    def test_reexported_from_package_root(self):
        self.assertIs(settle_reexport, price_and_settle)


if __name__ == "__main__":
    unittest.main()
