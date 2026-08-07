"""
Unit tests for the multi-stock comparison engine (aether/stock_compare.py).

All tests mock ``data_api.read_research`` so they run with no workbook/network — the
engine's contract is: select the requested symbols from the Research rows, rank them
deterministically, flag stale caches, and never raise on an unknown ticker.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether import stock_compare


def _row(symbol, combined, *, setup=False, risk_ratio=1.0, stop_source="swing_low",
         target_source="swing_high", s10=0.0, l60=0.0):
    """Minimal Research-row fixture carrying the fields compare_data reads."""
    return {
        "symbol": symbol, "industry": "Test", "price": 100.0, "pgr": 60,
        "s10": s10, "l60": l60, "combined": combined, "setup": setup,
        "money_flow": "Strong", "lt_trend": "Up", "industry_strength": "Neutral",
        "obos": "Neutral", "stop": 92.0, "stop_source": stop_source,
        "target": 120.0, "target_source": target_source, "risk_ratio": risk_ratio,
        "instrument": "normal", "patterns": "", "status": "Hold",
        "buying_ratio": 0.0, "seasonality": 0.0, "win_pct": 55.0,
    }


def _research(rows, regime="Neutral"):
    return {"rows": rows, "summary": {"market_regime": regime}}


class TestCompareData(unittest.TestCase):

    def _patch(self, rows, regime="Neutral"):
        return mock.patch.object(
            stock_compare.data_api, "read_research",
            return_value=_research(rows, regime),
        )

    def test_ranking_is_by_combined_desc(self):
        rows = [_row("IBM", 3.8), _row("DAVE", -9.3), _row("TG", 11.9), _row("CC", 1.9)]
        with self._patch(rows):
            res = stock_compare.compare_data(["TG", "CC", "DAVE", "IBM"])
        order = [r["symbol"] for r in res["ranking"]]
        self.assertEqual(order, ["TG", "IBM", "CC", "DAVE"])
        self.assertEqual([r["rank"] for r in res["ranking"]], [1, 2, 3, 4])
        # IBM must rank above DAVE (the plan's named assertion).
        self.assertLess(order.index("IBM"), order.index("DAVE"))

    def test_tiebreak_setup_then_risk_ratio_then_alpha(self):
        # Equal combined: setup wins first; then higher risk_ratio; then symbol alpha.
        rows = [
            _row("AAA", 5.0, setup=False, risk_ratio=9.0),
            _row("BBB", 5.0, setup=True, risk_ratio=1.0),
            _row("CCC", 5.0, setup=True, risk_ratio=3.0),
        ]
        with self._patch(rows):
            res = stock_compare.compare_data(["AAA", "BBB", "CCC"])
        order = [r["symbol"] for r in res["ranking"]]
        # BBB & CCC (setup) outrank AAA (no setup) despite AAA's better R:R;
        # CCC before BBB on higher R:R.
        self.assertEqual(order, ["CCC", "BBB", "AAA"])

    def test_unknown_symbol_is_found_false_not_raised(self):
        with self._patch([_row("IBM", 3.8)]):
            res = stock_compare.compare_data(["IBM", "ZZZFAKE"])
        by_sym = {r["symbol"]: r for r in res["rows"]}
        self.assertTrue(by_sym["IBM"]["found"])
        self.assertFalse(by_sym["ZZZFAKE"]["found"])
        self.assertEqual(by_sym["ZZZFAKE"]["reason"], "not on Research sheet")
        self.assertEqual(res["meta"]["missing"], ["ZZZFAKE"])
        self.assertEqual(res["meta"]["found"], 1)
        # Unknown symbols never enter the ranking.
        self.assertNotIn("ZZZFAKE", [r["symbol"] for r in res["ranking"]])

    def test_stale_flag_and_warning(self):
        rows = [
            _row("IBM", 3.8, stop_source="stale", target_source="stale"),
            _row("TG", 11.9),   # fresh
        ]
        with self._patch(rows):
            res = stock_compare.compare_data(["IBM", "TG"])
        by_sym = {r["symbol"]: r for r in res["rows"]}
        self.assertTrue(by_sym["IBM"]["stale"])
        self.assertFalse(by_sym["TG"]["stale"])
        self.assertIsNotNone(res["meta"]["stale_warning"])

    def test_no_stale_warning_when_all_fresh(self):
        with self._patch([_row("IBM", 3.8), _row("TG", 11.9)]):
            res = stock_compare.compare_data(["IBM", "TG"])
        self.assertIsNone(res["meta"]["stale_warning"])

    def test_shape_and_symbol_normalization(self):
        with self._patch([_row("IBM", 3.8)], regime="Bullish"):
            res = stock_compare.compare_data([" ibm ", "ibm", ""])   # dupe + blank + case
        self.assertEqual(res["symbols"], ["IBM"])            # normalized, de-duped, blanks dropped
        self.assertEqual(res["meta"]["requested"], 1)
        self.assertEqual(res["meta"]["market_regime"], "Bullish")
        self.assertEqual(res["meta"]["generated_by"], "stock_compare")
        for key in ("as_of", "symbols", "rows", "ranking", "meta"):
            self.assertIn(key, res)
        row = res["rows"][0]
        for key in ("symbol", "combined", "s10", "l60", "setup", "risk_ratio",
                    "stop", "stop_source", "found", "stale"):
            self.assertIn(key, row)

    def test_order_preserved_in_rows(self):
        rows = [_row("IBM", 3.8), _row("TG", 11.9), _row("CC", 1.9)]
        with self._patch(rows):
            res = stock_compare.compare_data(["CC", "TG", "IBM"])
        # rows follow the requested order; ranking is the scored order.
        self.assertEqual([r["symbol"] for r in res["rows"]], ["CC", "TG", "IBM"])
        self.assertEqual([r["symbol"] for r in res["ranking"]], ["TG", "IBM", "CC"])


class TestSummarizeComparison(unittest.TestCase):
    """The optional AI summarizer degrades to None and never raises; importing the
    module triggers no LLM call (engine stays pure)."""

    def _data(self):
        rows = [_row("IBM", 3.8), _row("TG", 11.9)]
        with mock.patch.object(stock_compare.data_api, "read_research",
                               return_value=_research(rows)):
            return stock_compare.compare_data(["IBM", "TG"])

    def test_render_for_summary_includes_ranking_and_each_symbol(self):
        block = stock_compare.render_for_summary(self._data())
        self.assertIn("deterministic_ranking:", block)
        self.assertIn("[IBM]", block)
        self.assertIn("[TG]", block)
        self.assertIn("combined=", block)

    def test_render_for_summary_marks_missing(self):
        rows = [_row("IBM", 3.8)]
        with mock.patch.object(stock_compare.data_api, "read_research",
                               return_value=_research(rows)):
            data = stock_compare.compare_data(["IBM", "ZZZFAKE"])
        block = stock_compare.render_for_summary(data)
        self.assertIn("[ZZZFAKE] NOT FOUND", block)

    def test_summary_none_when_no_provider(self):
        # Guard must short-circuit BEFORE calling the provider (else removing the guard
        # would still pass via the except path — this asserts evaluate is never reached).
        with mock.patch.object(stock_compare.ai_client, "primary", return_value=None), \
             mock.patch.object(stock_compare.Path, "read_text", return_value="RUBRIC"), \
             mock.patch.object(stock_compare.ai_client, "evaluate") as m_eval:
            summary, reason = stock_compare.summarize_comparison(self._data())
        m_eval.assert_not_called()
        self.assertIsNone(summary)
        self.assertIn("provider", reason)   # message tells the user what happened

    def test_summary_none_when_rubric_missing(self):
        with mock.patch.object(stock_compare.ai_client, "primary", return_value="fake"), \
             mock.patch.object(stock_compare.Path, "read_text",
                               side_effect=FileNotFoundError):
            summary, reason = stock_compare.summarize_comparison(self._data())
        self.assertIsNone(summary)
        self.assertIn("rubric", reason)

    def test_summary_returns_none_on_evaluate_failure(self):
        with mock.patch.object(stock_compare.ai_client, "primary", return_value="fake"), \
             mock.patch.object(stock_compare.Path, "read_text", return_value="RUBRIC"), \
             mock.patch.object(stock_compare.ai_client, "evaluate",
                               side_effect=RuntimeError("boom")):
            summary, reason = stock_compare.summarize_comparison(self._data())
        self.assertIsNone(summary)
        # Names the provider and the failure kind, but never leaks the raw exception text.
        self.assertIn("fake", reason)
        self.assertIn("RuntimeError", reason)
        self.assertNotIn("boom", reason)

    def test_summary_returns_evaluate_string_when_mocked(self):
        with mock.patch.object(stock_compare.ai_client, "primary", return_value="fake"), \
             mock.patch.object(stock_compare.Path, "read_text", return_value="RUBRIC"), \
             mock.patch.object(stock_compare.ai_client, "evaluate",
                               return_value="RANKED WHY") as m:
            summary, reason = stock_compare.summarize_comparison(self._data())
        self.assertEqual(summary, "RANKED WHY")
        self.assertIsNone(reason)
        # rubric is passed as the system prompt, the rendered block as the user content.
        args, kwargs = m.call_args
        self.assertEqual(args[0], "RUBRIC")
        self.assertIn("[IBM]", args[1])


if __name__ == "__main__":
    unittest.main()
