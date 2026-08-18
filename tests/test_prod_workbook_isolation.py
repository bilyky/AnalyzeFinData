"""Guards the tests/__init__.py prod-workbook redirect.

No test run may leave any module's ``XLSX_FILE`` pointing at the real, gitignored
``Data/state_of_the_day.xlsx`` — a save-path test (powergauge.check_from_xls,
the pipeline savers) would otherwise overwrite live trading state, which is what
mutated the prod file on 2026-08-17. If the redirect in tests/__init__.py is
removed, ``XLSX_FILE`` falls back to the prod path and this test goes red.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_portfolio_game
import autonomous_pipeline
import powergauge
import workbook_read

# The real workbook location, resolved the same way the app derives it (repo
# root / Data). Independent of the redirect so the assertion has something to
# compare against.
_PROD_XLSX = (Path(__file__).resolve().parent.parent / "Data" / "state_of_the_day.xlsx").resolve()


class TestProdWorkbookIsolation(unittest.TestCase):
    def test_no_module_targets_the_prod_workbook(self):
        for mod in (ai_portfolio_game, autonomous_pipeline, powergauge, workbook_read):
            with self.subTest(module=mod.__name__):
                active = Path(str(mod.XLSX_FILE)).resolve()
                self.assertNotEqual(
                    active, _PROD_XLSX,
                    f"{mod.__name__}.XLSX_FILE points at the production workbook",
                )


if __name__ == "__main__":
    unittest.main()
