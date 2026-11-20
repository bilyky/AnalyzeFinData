"""Dependency rules for the aether package (R&D #78, ADR 0001/0002).

Static checks over the import statements of every module under aether/ (AST, nothing is
imported or run):

1. **No aether module imports root code**: any ``*.py`` at the repo root that is not a
   ``sys.modules`` shim for an aether module (ai_portfolio_game, data_api, bar_provenance,
   ...). The set is derived from the tree, so a new root script is covered automatically.
   Dependencies point from the root into the package, never back. The imports that break
   this today are listed in ``KNOWN_ROOT_IMPORTS`` and must shrink: the test fails both on
   a new violation and on a listed one that has been fixed.
2. **aether/core imports only the standard library and aether.core** (the kernel depends
   on nothing).
3. **A service imports another service only through its ``api``**
   (``aether.services.<other>.api``), never its domain, adapters or app.
4. **A service's domain does no I/O**: it imports no adapter, no other aether I/O module
   and no I/O library.
5. **Only the composition root and a service's own adapters import its adapters**
   (``aether/bootstrap.py`` and ``aether/services/<name>/adapters/``). The domain, the app,
   the api and every other module depend on ports, never on an adapter (``adapters -> ports
   <- app``).

Relative imports (``from ..x import y``) are resolved against the file's package and
checked like absolute ones. Each rule is also run on a small synthetic tree, so a broken
checker cannot pass silently.
"""
import ast
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Debt to pay down (each entry: module path, imported root module). Remove an entry when
# its import is gone; the test fails if a listed import no longer exists.
KNOWN_ROOT_IMPORTS = frozenset({
    ("aether/scenario/steps.py", "ai_portfolio_game"),   # _pkg(); goes with the scenario package (S9)
    ("aether/stock_compare.py", "data_api"),
    ("aether/workbook_write.py", "data_api"),
    # bar_provenance and performance_tracker: small root libraries that belong under aether/.
    ("aether/oceanview_context.py", "bar_provenance"),
    ("aether/patterns.py", "bar_provenance"),
    ("aether/pick_eval.py", "bar_provenance"),
    ("aether/pick_eval.py", "performance_tracker"),
    ("aether/risk_utils.py", "bar_provenance"),
})

# I/O that a service domain must not import (third-party, stdlib and aether I/O modules).
DOMAIN_FORBIDDEN = frozenset({
    "requests", "urllib", "http", "socket", "subprocess", "sqlite3", "sqlalchemy", "openpyxl",
    "pyetrade", "playwright", "smtplib", "shutil", "pathlib", "os",
    "aether.etrade", "aether.notify", "aether.paths", "aether.logger", "aether.ledgers",
})


def _imports(path: Path, rel: str):
    """(lineno, absolute module name) for every import in the file, including nested ones.

    ``rel`` is the file's repo-relative path; relative imports are resolved against it.
    """
    package = rel[:-3].split("/")[:-1]  # the module's package (for __init__.py: the package itself)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - (node.level - 1)] if node.level - 1 <= len(package) else []
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module
            if not module:
                continue
            if node.module:
                yield node.lineno, module
            for alias in node.names:  # `from aether.services.x import api` imports aether.services.x.api
                yield node.lineno, f"{module}.{alias.name}"


def _modules(root: Path):
    for path in sorted((root / "aether").rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path.relative_to(root).as_posix(), path


def root_modules(root: Path):
    """Root-level modules aether/ must not import: every root *.py that isn't a shim.

    A shim (``sys.modules[__name__] = aether.x``) only re-points an old name at an aether
    module, so importing it is importing aether code.
    """
    return {p.stem for p in root.glob("*.py")
            if "sys.modules[__name__]" not in p.read_text(encoding="utf-8", errors="replace")}


def root_script_imports(root: Path):
    roots = root_modules(root)
    found = set()
    for rel, path in _modules(root):
        for _, mod in _imports(path, rel):
            top = mod.split(".")[0]
            if top in roots:
                found.add((rel, top))
    return found


def core_violations(root: Path):
    stdlib = set(sys.stdlib_module_names)
    bad = []
    for rel, path in _modules(root):
        if not rel.startswith("aether/core/"):
            continue
        for line, mod in _imports(path, rel):
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
        for line, mod in _imports(path, rel):
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
        for line, mod in _imports(path, rel):
            hit = next((f for f in DOMAIN_FORBIDDEN if mod == f or mod.startswith(f + ".")), None)
            adapter = mod.startswith(f"aether.services.{own}.adapters")
            if (hit or adapter) and not any(b.startswith(f"{rel}:{line} ") for b in bad):  # one per line
                bad.append(f"{rel}:{line} imports {mod} (domain code does no I/O)")
    return bad


def adapter_import_violations(root: Path):
    bad = []
    for rel, path in _modules(root):
        for line, mod in _imports(path, rel):
            parts = mod.split(".")
            if parts[:2] != ["aether", "services"] or len(parts) < 4 or parts[3] != "adapters":
                continue
            if rel == "aether/bootstrap.py" or rel.startswith(f"aether/services/{parts[2]}/adapters/"):
                continue
            if not any(b.startswith(f"{rel}:{line} ") for b in bad):  # one report per import line
                bad.append(f"{rel}:{line} imports {mod} (only aether/bootstrap.py wires adapters)")
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

    def test_only_bootstrap_wires_adapters(self):
        self.assertEqual(adapter_import_violations(_REPO), [])


def _tree(files):
    d = tempfile.TemporaryDirectory()
    root = Path(d.name)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d, root


class TestCheckersCatchViolations(unittest.TestCase):
    def test_root_import_is_found_even_inside_a_function(self):
        d, root = _tree({"ai_portfolio_game.py": "x = 1\n", "data_api.py": "x = 1\n", "new_tool.py": "x = 1\n",
                         "risk_utils.py": "import sys\nimport aether.risk_utils as m\nsys.modules[__name__] = m\n",
                         "aether/x.py": "def f():\n    import ai_portfolio_game as g\n",
                         "aether/y.py": "import data_api.sub\nimport risk_utils\n",
                         "aether/z.py": "from new_tool import thing\n", "aether/ok.py": "import json\n"})
        with d:
            found = root_script_imports(root)
        # A shim (risk_utils) is not root code; a brand-new root script (new_tool) is.
        self.assertEqual(found, {("aether/x.py", "ai_portfolio_game"), ("aether/y.py", "data_api"),
                                 ("aether/z.py", "new_tool")})

    def test_relative_imports_are_checked_like_absolute_ones(self):
        d, root = _tree({
            "aether/core/x.py": "from .. import paths\n",
            "aether/services/a/app.py": "from ..b.domain import model\n",
            "aether/services/a/domain/m.py": "from ....notify import send\n",  # aether.notify
            "aether/services/a/domain/ok.py": "from . import other\nfrom .other import y\n",
        })
        with d:
            core, boundary, domain = core_violations(root), service_boundary_violations(root), domain_io_violations(root)
        self.assertEqual(core, ["aether/core/x.py:1 imports aether.paths"])
        self.assertEqual(boundary, ["aether/services/a/app.py:1 imports aether.services.b.domain (use aether.services.b.api)"])
        self.assertEqual(domain, ["aether/services/a/domain/m.py:1 imports aether.notify (domain code does no I/O)"])

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

    def test_adapters_are_imported_only_by_bootstrap_and_their_own_service(self):
        d, root = _tree({
            "aether/bootstrap.py": "from aether.services.portfolio.adapters.json_repository import Repo\n",
            "aether/services/portfolio/adapters/gateway.py": "from . import json_mapper\n"
                                                             "from aether.services.portfolio.adapters import memory\n",
            "aether/services/portfolio/app.py": "from aether.services.portfolio.adapters import json_mapper\n",
            "aether/services/portfolio/api.py": "from .adapters.memory import Fake\n",
            "aether/services/trading/adapters/x.py": "import aether.services.portfolio.adapters.memory\n",
            "aether/server_like.py": "from aether.services.portfolio.adapters import json_repository\n",
        })
        with d:
            bad = adapter_import_violations(root)
        self.assertEqual(sorted(b.split(":")[0] for b in bad),
                         ["aether/server_like.py", "aether/services/portfolio/api.py",
                          "aether/services/portfolio/app.py", "aether/services/trading/adapters/x.py"])


if __name__ == "__main__":
    unittest.main()
