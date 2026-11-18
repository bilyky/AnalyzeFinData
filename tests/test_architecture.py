"""Dependency rules for the aether package (R&D #78, ADR 0001/0002).

Static checks over the import statements of every module under aether/ (AST, nothing is
imported or run):

1. **No aether module imports a root application script** (ai_portfolio_game, data_api,
   server, ...). Dependencies point from the scripts into the package, never back. Three
   modules break this today; they are listed in ``KNOWN_ROOT_IMPORTS`` and must shrink:
   the test fails both on a new violation and on a listed one that has been fixed.
2. **aether/core imports only the standard library and aether.core** (the kernel depends
   on nothing).
3. **A service imports another service only through its ``api``**
   (``aether.services.<other>.api``), never its domain, adapters or app.
4. **A service's domain does no I/O**: it imports no adapter, no other aether I/O module
   and no I/O library.

Each rule is also run on a small synthetic tree, so a broken checker cannot pass silently.
"""
import ast
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Root application scripts: entry points and orchestrators that aether/ must never import.
ROOT_APP_SCRIPTS = frozenset({
    "ai_portfolio_game", "aether_oracle", "autonomous_pipeline", "daily_task", "data_api", "main",
    "oceanview_api", "powergauge", "real_copilot", "run_history", "server", "watchdog", "workbook_read",
})

# Debt to pay down (each entry: module path, imported root script). Remove an entry when
# its import is gone; the test fails if a listed import no longer exists.
KNOWN_ROOT_IMPORTS = frozenset({
    ("aether/scenario/steps.py", "ai_portfolio_game"),   # _pkg(); goes with the scenario package (S9)
    ("aether/stock_compare.py", "data_api"),
    ("aether/workbook_write.py", "data_api"),
})

# I/O that a service domain must not import (third-party, stdlib and aether I/O modules).
DOMAIN_FORBIDDEN = frozenset({
    "requests", "urllib", "http", "socket", "subprocess", "sqlite3", "sqlalchemy", "openpyxl",
    "pyetrade", "playwright", "smtplib", "shutil", "pathlib", "os",
    "aether.etrade", "aether.notify", "aether.paths", "aether.logger", "aether.ledgers",
})


def _imports(path: Path):
    """(lineno, absolute module name) for every import in the file, including nested ones."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, node.module
            for alias in node.names:  # `from aether.services.x import api` imports aether.services.x.api
                yield node.lineno, f"{node.module}.{alias.name}"


def _modules(root: Path):
    for path in sorted((root / "aether").rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path.relative_to(root).as_posix(), path


def root_script_imports(root: Path):
    found = set()
    for rel, path in _modules(root):
        for _, mod in _imports(path):
            top = mod.split(".")[0]
            if top in ROOT_APP_SCRIPTS:
                found.add((rel, top))
    return found


def core_violations(root: Path):
    stdlib = set(sys.stdlib_module_names)
    bad = []
    for rel, path in _modules(root):
        if not rel.startswith("aether/core/"):
            continue
        for line, mod in _imports(path):
            top = mod.split(".")[0]
            if mod == "aether.core" or mod.startswith("aether.core.") or top in stdlib or top == "__future__":
                continue
            if not any(b.startswith(f"{rel}:{line} ") for b in bad):  # one report per import line
                bad.append(f"{rel}:{line} imports {mod}")
    return bad


def _service_of(rel: str):
    parts = rel.split("/")
    return parts[2] if len(parts) > 3 and parts[:2] == ["aether", "services"] else None


def service_boundary_violations(root: Path):
    bad = []
    for rel, path in _modules(root):
        own = _service_of(rel)
        if own is None:
            continue
        for line, mod in _imports(path):
            parts = mod.split(".")
            if parts[:2] != ["aether", "services"] or len(parts) < 3 or parts[2] == own:
                continue
            if len(parts) == 3 or parts[3] == "api":
                continue  # `from aether.services import x` / `aether.services.x.api` are the public way in
            if not any(b.startswith(f"{rel}:{line} ") for b in bad):  # one report per import line
                bad.append(f"{rel}:{line} imports {mod} (use aether.services.{parts[2]}.api)")
    return bad


def domain_io_violations(root: Path):
    bad = []
    for rel, path in _modules(root):
        own = _service_of(rel)
        if own is None or "/domain/" not in rel:
            continue
        for line, mod in _imports(path):
            hit = next((f for f in DOMAIN_FORBIDDEN if mod == f or mod.startswith(f + ".")), None)
            adapter = mod.startswith(f"aether.services.{own}.adapters")
            if (hit or adapter) and not any(b.startswith(f"{rel}:{line} ") for b in bad):  # one per line
                bad.append(f"{rel}:{line} imports {mod} (domain code does no I/O)")
    return bad


class TestRealTree(unittest.TestCase):
    def test_no_new_root_script_imports(self):
        new = root_script_imports(_REPO) - KNOWN_ROOT_IMPORTS
        self.assertEqual(new, set(), "aether/ must not import root application scripts")

    def test_known_root_imports_list_is_current(self):
        fixed = KNOWN_ROOT_IMPORTS - root_script_imports(_REPO)
        self.assertEqual(fixed, set(), "these imports are gone: remove them from KNOWN_ROOT_IMPORTS")

    def test_core_depends_only_on_stdlib(self):
        self.assertEqual(core_violations(_REPO), [])

    def test_services_talk_through_api_only(self):
        self.assertEqual(service_boundary_violations(_REPO), [])

    def test_service_domains_do_no_io(self):
        self.assertEqual(domain_io_violations(_REPO), [])


def _tree(files):
    d = tempfile.TemporaryDirectory()
    root = Path(d.name)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d, root


class TestCheckersCatchViolations(unittest.TestCase):
    def test_root_script_import_is_found_even_inside_a_function(self):
        d, root = _tree({"aether/x.py": "def f():\n    import ai_portfolio_game as g\n",
                         "aether/y.py": "import data_api.sub\n", "aether/ok.py": "import json\n"})
        with d:
            self.assertEqual(root_script_imports(root), {("aether/x.py", "ai_portfolio_game"), ("aether/y.py", "data_api")})

    def test_core_may_not_import_aether_or_third_party(self):
        d, root = _tree({"aether/core/a.py": "import datetime\nfrom aether.core.ids import PortfolioId\n",
                         "aether/core/b.py": "from aether import paths\nimport pytz\n"})
        with d:
            bad = core_violations(root)
        self.assertEqual(len(bad), 2, bad)  # `from aether import paths`, `import pytz`
        self.assertTrue(all(b.startswith("aether/core/b.py") for b in bad), bad)

    def test_cross_service_import_must_go_through_api(self):
        d, root = _tree({
            "aether/services/trading/app.py": "from aether.services.portfolio.api import load\n"
                                              "from aether.services import market_data\n"
                                              "from aether.services.trading.domain import model\n",
            "aether/services/reporting/app.py": "from aether.services.portfolio.domain.model import Portfolio\n"
                                                "import aether.services.ledger.adapters.json_file\n",
        })
        with d:
            bad = service_boundary_violations(root)
        self.assertEqual(len(bad), 2, bad)
        self.assertTrue(all(b.startswith("aether/services/reporting/app.py") for b in bad), bad)

    def test_domain_may_not_do_io(self):
        d, root = _tree({
            "aether/services/portfolio/domain/model.py": "import dataclasses\nfrom aether.core import PortfolioId\n",
            "aether/services/portfolio/domain/bad.py": "import openpyxl\nfrom aether import notify\n"
                                                       "from aether.services.portfolio.adapters import json_repo\n",
            "aether/services/portfolio/app.py": "import openpyxl\n",  # app may do I/O through adapters
        })
        with d:
            bad = domain_io_violations(root)
        self.assertEqual(len(bad), 3, bad)  # openpyxl, aether.notify, the adapters import
        self.assertTrue(all(b.startswith("aether/services/portfolio/domain/bad.py") for b in bad), bad)


if __name__ == "__main__":
    unittest.main()
