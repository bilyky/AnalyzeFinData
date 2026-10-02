"""Hermetic tests for aether.scenario.schema.ResearchRow (BUILD phase B4).

ResearchRow is a positional lens over one row of the workbook **Research** sheet
(``ws.iter_rows(min_row=2, values_only=True)`` yields the row as a tuple). These
tests use a tiny fake worksheet exposing ``iter_rows`` so nothing touches real
openpyxl or disk — the same hand-fake style as the other scenario BUILD tests.

The load-bearing assertion is constraint 4 of the refactor design doc: the
Research sheet stores a previous close in **two different columns** depending on
the call-site (``row[8]`` in the SELL loop, ``row[10]`` everywhere else) — a
verified intentional inconsistency the reader must expose separately and NEVER
unify.
"""
import unittest

from aether.scenario import ResearchRow as ResearchRowReexport
from aether.scenario.schema import ResearchRow


class _FakeWorksheet:
    """Minimal stand-in for an openpyxl worksheet — just enough to row-scan."""

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=1, values_only=False):
        # Mirror openpyxl's 1-based min_row semantics (row 1 == header).
        for r in self._rows[min_row - 1:]:
            yield tuple(r) if values_only else r


def _row(overrides=None):
    """Build a Research-sheet row tuple with the magic indices populated.

    ``overrides`` is a ``{index: value}`` dict (integer keys) applied on top of
    the defaults. Indices that matter: 3=symbol, 4=industry, 6=pgr,
    8=prev_close_sell, 9=stop, 10=prev_close_buy, 11=target, 20=setup, 24=s10,
    25=l60.
    """
    row = [None] * 26
    row[3] = "AAPL"
    row[4] = "Technology"
    row[6] = "Bullish"
    row[8] = 231.5      # prev close as the SELL loop reads it
    row[9] = 224.0      # stop
    row[10] = 232.75    # prev close as BUY/report/after-hours read it
    row[11] = 260.0     # target
    row[20] = "OK"      # setup
    row[24] = 3.5       # s10
    row[25] = 6.0       # l60
    for i, v in (overrides or {}).items():
        row[i] = v
    return tuple(row)


class TestResearchRowAccessors(unittest.TestCase):
    def test_named_accessors_map_to_correct_indices(self):
        rr = ResearchRow.from_row(_row())
        self.assertEqual(rr.symbol, "AAPL")
        self.assertEqual(rr.industry, "Technology")
        self.assertEqual(rr.pgr, "Bullish")
        self.assertEqual(rr.stop, 224.0)
        self.assertEqual(rr.target, 260.0)
        self.assertEqual(rr.setup, "OK")
        self.assertEqual(rr.s10, 3.5)
        self.assertEqual(rr.l60, 6.0)

    def test_prev_close_split_is_not_unified(self):
        # The whole point of constraint 4: two columns, two accessors.
        rr = ResearchRow.from_row(_row({8: 100.0, 10: 200.0}))
        self.assertEqual(rr.prev_close_sell, 100.0)   # row[8]
        self.assertEqual(rr.prev_close_buy, 200.0)    # row[10]
        self.assertNotEqual(rr.prev_close_sell, rr.prev_close_buy)
        # There must be no unified `prev_close` accessor that silently picks one.
        self.assertFalse(hasattr(rr, "prev_close"))

    def test_accessors_return_raw_cells_no_coercion(self):
        # None cells stay None (callers apply their own `or 0` / `_to_float`).
        rr = ResearchRow.from_row(_row({6: None, 9: None, 24: None}))
        self.assertIsNone(rr.pgr)
        self.assertIsNone(rr.stop)
        self.assertIsNone(rr.s10)

    def test_short_row_yields_none_not_indexerror(self):
        # openpyxl yields ragged rows; the call-sites guard with len(row) > 10.
        rr = ResearchRow.from_row(("x", "y", "z", "TSLA"))  # len 4
        self.assertEqual(rr.symbol, "TSLA")     # index 3 present
        self.assertIsNone(rr.prev_close_buy)    # index 10 absent
        self.assertIsNone(rr.l60)               # index 25 absent


class TestResearchRowViewSemantics(unittest.TestCase):
    def test_from_row_wraps_no_copy_and_to_row_is_identity(self):
        raw = _row()
        rr = ResearchRow.from_row(raw)
        self.assertIs(rr.to_row(), raw)
        self.assertIs(rr.raw, raw)

    def test_reexported_from_package_root(self):
        self.assertIs(ResearchRowReexport, ResearchRow)

    def test_repr_is_informative(self):
        rr = ResearchRow.from_row(_row())
        text = repr(rr)
        self.assertIn("ResearchRow", text)
        self.assertIn("AAPL", text)


class TestResearchRowDerived(unittest.TestCase):
    def test_is_active_setup_matches_ok_convention(self):
        # Project convention: Setup field uses 'OK'/'' (also accepts legacy 1/'1').
        self.assertTrue(ResearchRow.from_row(_row({20: "OK"})).is_active_setup())
        self.assertTrue(ResearchRow.from_row(_row({20: "1"})).is_active_setup())
        self.assertTrue(ResearchRow.from_row(_row({20: 1})).is_active_setup())
        self.assertFalse(ResearchRow.from_row(_row({20: ""})).is_active_setup())
        self.assertFalse(ResearchRow.from_row(_row({20: None})).is_active_setup())

    def test_is_active_setup_requires_symbol(self):
        # Predicate mirrors line 302: `row[3] and str(row[20] ...) in (...)`.
        self.assertFalse(ResearchRow.from_row(_row({3: None, 20: "OK"})).is_active_setup())

    def test_total_score_sums_s10_l60_with_zero_default(self):
        self.assertAlmostEqual(ResearchRow.from_row(_row({24: 3.5, 25: 6.0})).total_score(), 9.5)
        # None legs coerce to 0.0 exactly like the BUY-screening expression.
        self.assertAlmostEqual(ResearchRow.from_row(_row({24: None, 25: None})).total_score(), 0.0)
        self.assertAlmostEqual(ResearchRow.from_row(_row({24: 2.0, 25: None})).total_score(), 2.0)


class TestResearchRowRowScan(unittest.TestCase):
    def test_scans_a_fake_worksheet_like_the_game_does(self):
        header = ["h"] * 26
        ws = _FakeWorksheet([
            header,
            _row({3: "AAPL", 24: 3.5, 25: 6.0}),
            _row({3: "MSFT", 20: "", 24: 1.0, 25: 2.0}),
        ])
        rows = [ResearchRow.from_row(r) for r in ws.iter_rows(min_row=2, values_only=True)]
        self.assertEqual([r.symbol for r in rows], ["AAPL", "MSFT"])
        active = [r.symbol for r in rows if r.is_active_setup()]
        self.assertEqual(active, ["AAPL"])  # MSFT has an empty setup


if __name__ == "__main__":
    unittest.main()
