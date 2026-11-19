"""PortfolioQueries and the read views (R&D #78 S4, plan P9).

- The views: built from the model, read-only, ``None`` for an absent or null value, and a
  transaction's ``record()`` is exactly what was stored.
- The query contract, run on the JSON and in-memory adapters: never writes, ``None`` when
  never saved, ``StorageError`` when unreadable, portfolios kept apart.
- A guard: no code outside the portfolio service names the state file, so every reader goes
  through the queries.
"""
import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aether.bootstrap as boot
from aether.core import DEFAULT_PORTFOLIO, PortfolioId, StorageError
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.adapters.json_queries import JsonPortfolioQueries
from aether.services.portfolio.adapters.json_repository import PortfolioFile
from aether.services.portfolio.adapters.memory import InMemoryPortfolioQueries, InMemoryPortfolioRepository
from aether.services.portfolio.api import PortfolioView, PositionView, TransactionView, UnknownPortfolio

_REPO = Path(__file__).resolve().parent.parent
_P = DEFAULT_PORTFOLIO
_STATE = {
    "balance": 812.5, "equity": 10450.0, "profile": "AGGRESSIVE", "start_date": "2026-01-02",
    "positions": {
        "AAA": {"qty": 3, "cost": 10.0, "stop_loss": 9.0, "is_scarcity": False, "price": 11.0,
                "buy_dna": {"buy_date": "2026-11-02", "pgr": "Bullish"}, "highest_close_since_acq": 12.0,
                "written_call": {"qty": 1, "strike": 12.0, "premium": 0.3, "expiration_date": "2026-11-27",
                                 "sigma": 0.4}},
        "BBB": {"qty": 10, "cost": 5, "stop_loss": None},
    },
    "history": [
        {"date": "2026-11-02", "type": "BUY", "symbol": "AAA", "price": 10.0, "qty": 3},
        {"date": "2026-11-03", "time": "10:00", "type": "SELL", "symbol": "CCC", "price": 5.5, "qty": 2,
         "pnl": -1.0, "details": "stop", "contract": "kept"},
    ],
}


class TestViews(unittest.TestCase):
    def setUp(self):
        repo = InMemoryPortfolioRepository([_P])
        repo.save(json_mapper.from_dict(_P, _STATE))
        self.view = InMemoryPortfolioQueries(repo).view(_P)

    def test_portfolio_fields(self):
        v = self.view
        self.assertIsInstance(v, PortfolioView)
        self.assertEqual((v.portfolio_id, v.balance, v.equity, v.profile, v.start_date),
                         ("ai_game", 812.5, 10450.0, "AGGRESSIVE", "2026-01-02"))
        self.assertEqual(v.held_symbols, ("AAA", "BBB"))  # stored order
        self.assertEqual(v.written_call_symbols(), ["AAA"])

    def test_positions(self):
        aaa, bbb = self.view.positions
        self.assertIsInstance(aaa, PositionView)
        self.assertEqual((aaa.symbol, aaa.qty, aaa.cost, aaa.stop_loss, aaa.highest_close_since_acq, aaa.buy_date),
                         ("AAA", 3, 10.0, 9.0, 12.0, "2026-11-02"))
        self.assertEqual((aaa.written_call.strike, aaa.written_call.expiration_date), (12.0, "2026-11-27"))
        self.assertEqual(aaa.extra["price"], 11.0)  # a key the model doesn't know yet
        self.assertIsNone(bbb.stop_loss)            # stored null
        self.assertIsNone(bbb.highest_close_since_acq)  # not stored
        self.assertIsNone(bbb.written_call)
        self.assertIs(type(bbb.cost), int)          # values are not converted

    def test_transactions_and_exact_records(self):
        buy, sell = self.view.history
        self.assertIsInstance(buy, TransactionView)
        self.assertIsNone(buy.pnl)
        self.assertEqual(list(sell.record().items()), list(_STATE["history"][1].items()))
        sell.record()["pnl"] = 99  # a record is a copy
        self.assertEqual(sell.record()["pnl"], -1.0)

    def test_views_are_read_only(self):
        with self.assertRaises(AttributeError):
            self.view.balance = 1
        with self.assertRaises(TypeError):
            self.view.positions[0].extra["price"] = 1

    def test_empty_and_missing_parts(self):
        repo = InMemoryPortfolioRepository([_P])
        repo.save(json_mapper.from_dict(_P, {"profile": "BALANCED"}))  # no cash, positions or history stored
        v = InMemoryPortfolioQueries(repo).view(_P)
        self.assertEqual((v.balance, v.positions, v.history), (None, (), ()))


class QueriesContract:
    """Every PortfolioQueries adapter passes these. Subclasses set ``make(ids, states)``."""

    def make(self, ids, states):
        raise NotImplementedError

    def test_never_saved_is_none(self):
        self.assertIsNone(self.make([_P], {}).view(_P))

    def test_view_of_a_saved_portfolio(self):
        v = self.make([_P], {_P: _STATE}).view(_P)
        self.assertEqual((v.balance, v.held_symbols), (812.5, ("AAA", "BBB")))

    def test_portfolios_are_kept_apart(self):
        other = PortfolioId("other")
        q = self.make([_P, other], {other: {"balance": 1.0}})
        self.assertIsNone(q.view(_P))
        self.assertEqual(q.view(other).balance, 1.0)

    def test_unknown_portfolio_is_refused(self):
        with self.assertRaises(UnknownPortfolio):
            self.make([_P], {}).view(PortfolioId("nope"))


class TestJsonQueriesContract(QueriesContract, unittest.TestCase):
    def make(self, ids, states):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        files = {i: PortfolioFile(self.root / f"{i}.json", self.root / "Backup" / str(i)) for i in ids}
        for i, state in states.items():
            files[i].path.write_text(json.dumps(state), encoding="utf-8")
        return JsonPortfolioQueries(files)

    def _files(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))

    def test_unreadable_file_raises_and_nothing_is_written(self):
        for text in ("", "{broken"):
            with self.subTest(text=repr(text)):
                q = self.make([_P], {})
                (self.root / "ai_game.json").write_text(text, encoding="utf-8")
                backup = self.root / "Backup" / "ai_game" / "ai_game_20261101_000000.json"
                backup.parent.mkdir(parents=True)
                backup.write_text(json.dumps(_STATE), encoding="utf-8")
                before = self._files()
                with self.assertRaises(StorageError):
                    q.view(_P)
                self.assertEqual(self._files(), before)  # no restore, no .corrupt copy
                self.assertEqual((self.root / "ai_game.json").read_text(encoding="utf-8"), text)

    def test_a_wrong_shape_raises(self):
        with self.assertRaises(StorageError):
            self.make([_P], {_P: {"positions": []}}).view(_P)


class TestMemoryQueriesContract(QueriesContract, unittest.TestCase):
    def make(self, ids, states):
        repo = InMemoryPortfolioRepository(ids)
        for i, state in states.items():
            repo.save(json_mapper.from_dict(i, state))
        return InMemoryPortfolioQueries(repo)


class TestBootstrapWiresQueries(unittest.TestCase):
    def test_json_queries_read_the_game_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(boot, "DEFAULT_DATA_DIR", Path(d)):
            app = boot.bootstrap()
            self.assertIsInstance(app.portfolio_queries, JsonPortfolioQueries)
            self.assertIsNone(app.portfolio_queries.view(_P))
            app.portfolio_state.save_state(_STATE)
            self.assertEqual(app.portfolio_queries.view(_P).held_symbols, ("AAA", "BBB"))

    def test_memory_repository_gets_memory_queries(self):
        app = boot.bootstrap(portfolio_repository=InMemoryPortfolioRepository())
        self.assertIsInstance(app.portfolio_queries, InMemoryPortfolioQueries)


class TestReadersUseTheQueries(unittest.TestCase):
    """Readers moved onto PortfolioQueries in this PR that had no test of their own."""

    def test_bootstrap_dna_pairs_trades_from_the_read_view(self):
        import bootstrap_dna
        from aether import ledgers
        state = {"history": [
            {"date": "2026-11-02", "type": "BUY", "symbol": "AAA", "price": 10.0, "qty": 3},
            {"date": "2026-11-05", "type": "SELL", "symbol": "AAA", "price": 12.0, "qty": 3, "pnl": 6.0}]}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(boot, "DEFAULT_DATA_DIR", Path(d)),                 mock.patch.object(ledgers, "TRADE_DNA_FILE", Path(d) / "dna.json"),                 mock.patch.object(bootstrap_dna, "find_backup_for_date", return_value=None),                 mock.patch.object(bootstrap_dna, "extract_dna_from_backup", return_value={}),                 mock.patch.object(bootstrap_dna, "_log") as log:
            bootstrap_dna.bootstrap()  # never saved: logs and stops
            self.assertIn("not found", log.error.call_args[0][0])
            Path(d, "ai_portfolio_game.json").write_text(json.dumps(state), encoding="utf-8")
            bootstrap_dna.bootstrap()
            (trade,) = json.loads((Path(d) / "dna.json").read_text(encoding="utf-8"))
        self.assertEqual((trade["symbol"], trade["holding_days"], trade["pnl_pct"]), ("AAA", 3, 20.0))


# Code that may name the state file: its owner, and tools that read a copy from a folder given
# on the command line (a PROD drop, for example). Everything else uses PortfolioQueries.
STATE_FILE_ALLOWED = {
    "aether/bootstrap.py",                       # the one place that names the game's file
    "scripts/backtesting/anti_churn_study.py",   # --data-dir: studies a copy
    "docs/prod-notes/purge_test_fixtures.py",    # a one-off PROD maintenance note (2026-10-06)
}


def state_file_constants(root: Path):
    """(path, line) of every "ai_portfolio_game.json" string constant outside tests."""
    found = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(("tests/", "_wt", ".claude/", "venv")) or "/." in rel or "site-packages" in rel:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        found += [(rel, n.lineno) for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and n.value == "ai_portfolio_game.json"]
    return found


class TestNoDirectReaders(unittest.TestCase):
    def test_only_the_owner_names_the_state_file(self):
        bad = [f"{rel}:{line}" for rel, line in state_file_constants(_REPO) if rel not in STATE_FILE_ALLOWED
               and not rel.startswith("aether/services/portfolio/")]
        self.assertEqual(bad, [], "read the game state through bootstrap().portfolio_queries")

    def test_the_guard_finds_a_new_reader(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "tool.py").write_text('open(os.path.join(d, "ai_portfolio_game.json"))\n', encoding="utf-8")
            Path(d, "ok.py").write_text('"""Reads ai_portfolio_game.json via the service."""\n', encoding="utf-8")
            self.assertEqual(state_file_constants(Path(d)), [("tool.py", 1)])


if __name__ == "__main__":
    unittest.main()
