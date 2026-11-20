"""Portfolio service: domain model, JSON mapper, repository contract, unit of work, store switch.

R&D #78 S4. Pure domain tests use real numbers and no mocks. The mapper must give back every
state the game writes, key for key and in order, so ``json.dump`` gives the same bytes.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aether.bootstrap as boot
from aether.core import DEFAULT_PORTFOLIO, DomainError, EventBus, PortfolioId, StorageError
from aether.services.portfolio.adapters import json_mapper
from aether.services.portfolio.adapters.json_repository import JsonPortfolioRepository, PortfolioFile
from aether.services.portfolio.adapters.memory import InMemoryPortfolioRepository
from aether.services.portfolio.api import (ABSENT, BuyDNA, Portfolio, PortfolioService, Position, StopChanged,
                                           Transaction, UnknownPortfolio)

_GOLDEN = Path(__file__).parent / "fixtures" / "scenario_runner_golden.json"
_P = DEFAULT_PORTFOLIO


def _rich_state():
    """Every shape seen in a real state file (keys and types only; the values are made up)."""
    return {
        "balance": 812.5, "equity": 10450.0,
        "positions": {
            "AAA": {"qty": 3, "cost": 10.0, "stop_loss": 9.0, "is_scarcity": False,
                    "buy_dna": {"buy_date": "2026-11-02", "pgr": "Bullish", "s10": 1.5, "l60": 2.0, "score": 7.0,
                                "z_score": 0.0, "industry": "Tech", "future_key": [1]},
                    "highest_close_since_acq": 12.0},
            "BBB": {"cost": 5, "qty": 10, "stop_loss": 4.5, "is_scarcity": True, "buy_dna": {"buy_date": "2026-10-01"},
                    "banked_pct": 0.25, "highest_close_since_acq": 6.0, "written_call": {"strike": 7}},
        },
        "queued_orders": [{"type": "SELL", "symbol": "AAA", "reason": "Exit triggered: x"}],
        "profile": "BALANCED", "start_date": "2026-01-02",
        "history": [
            {"date": "2026-11-02", "type": "BUY", "symbol": "AAA", "price": 10, "qty": 3},
            {"date": "2026-11-03", "time": "10:00", "type": "SELL", "symbol": "CCC", "price": 5.5, "qty": 2.0,
             "pnl": -1.0, "details": "stop", "stop_loss": 5.4},
            {"date": "2026-11-03", "type": "COST_DEDUCTION", "symbol": "FEE", "price": 1.0, "details": "data"},
            {"date": "2026-11-04", "time": "10:00", "type": "OPTION_WRITE", "symbol": "AAA", "price": 0.3, "qty": 1,
             "pnl": 0.3, "details": "covered call", "contract": "AAA C 12"},
        ],
        "profile_mode": "AUTO", "days_active": 12, "iteration": 40, "unknown_top": {"x": None},
    }


def _golden_states():
    cases = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    return {name: case["saved"] for name, case in cases.items() if case.get("saved") is not None}


def _dumps(d):
    return json.dumps(d, indent=4)


class TestMapperRoundTrip(unittest.TestCase):
    def test_golden_states_round_trip_exactly(self):
        states = _golden_states()
        self.assertGreaterEqual(len(states), 5)
        for name, state in states.items():
            with self.subTest(case=name):
                self.assertEqual(_dumps(json_mapper.to_dict(json_mapper.from_dict(_P, state))), _dumps(state))

    def test_every_real_shape_round_trips_exactly(self):
        state = _rich_state()
        back = json_mapper.to_dict(json_mapper.from_dict(_P, state))
        self.assertEqual(_dumps(back), _dumps(state))
        self.assertIs(type(back["positions"]["BBB"]["cost"]), int)  # values are never converted

    def test_objects_are_typed(self):
        p = json_mapper.from_dict(_P, _rich_state())
        self.assertIsInstance(p.positions["AAA"], Position)
        self.assertIsInstance(p.positions["AAA"].buy_dna, BuyDNA)
        self.assertEqual(p.positions["AAA"].buy_dna.extra, {"future_key": [1]})
        self.assertIsInstance(p.history[0], Transaction)
        self.assertIs(p.history[0].pnl, ABSENT)
        self.assertEqual(p.extra, {"unknown_top": {"x": None}})

    def test_minimal_and_null_values(self):
        for state in ({}, {"balance": 1.0}, {"positions": {"A": {"qty": None}}}, {"history": []}):
            with self.subTest(state=state):
                self.assertEqual(_dumps(json_mapper.to_dict(json_mapper.from_dict(_P, state))), _dumps(state))

    def test_no_shared_objects_with_the_input(self):
        state = _rich_state()
        p = json_mapper.from_dict(_P, state)
        state["positions"]["AAA"]["buy_dna"]["future_key"].append(2)
        state["unknown_top"]["x"] = 1
        self.assertEqual(json_mapper.to_dict(p), _rich_state())

    def test_a_changed_field_keeps_its_place_and_a_new_one_goes_last(self):
        p = json_mapper.from_dict(_P, {"positions": {"A": {"qty": 1, "stop_loss": 2.0, "cost": 3.0}}})
        pos = p.positions["A"]
        pos.raise_stop(2.5)
        pos.track_peak(4.0)
        self.assertEqual(list(json_mapper.to_dict(p)["positions"]["A"].items()),
                         [("qty", 1), ("stop_loss", 2.5), ("cost", 3.0), ("highest_close_since_acq", 4.0)])

    def test_wrong_shapes_are_refused(self):
        for state in ([], {"positions": []}, {"positions": {"A": 5}}, {"history": {}}, {"history": [1]},
                      {"queued_orders": "x"}, {"positions": {"A": {"buy_dna": "x"}}}):
            with self.subTest(state=state):
                with self.assertRaises(StorageError):
                    json_mapper.from_dict(_P, state)


class TestPositionRules(unittest.TestCase):
    def test_raise_stop_never_lowers(self):
        pos = Position(qty=1, cost=10.0, stop_loss=9.0)
        self.assertFalse(pos.raise_stop(8.5))
        self.assertFalse(pos.raise_stop(9.0))
        self.assertEqual(pos.stop_loss, 9.0)
        self.assertTrue(pos.raise_stop(9.4))
        self.assertEqual(pos.stop_loss, 9.4)

    def test_raise_stop_sets_a_missing_stop(self):
        self.assertTrue(Position(qty=1).raise_stop(5.0))

    def test_restore_stop_can_lower(self):  # today's breaker restore; F8 studies it
        pos = Position(stop_loss=9.4)
        pos.restore_stop(8.0)
        self.assertEqual(pos.stop_loss, 8.0)

    def test_track_peak_keeps_the_high(self):
        pos = Position()
        for close in (10.0, 12.0, 11.0):
            pos.track_peak(close)
        self.assertEqual(pos.highest_close_since_acq, 12.0)


class TestPortfolioAggregate(unittest.TestCase):
    def _portfolio(self):
        return Portfolio(id=_P, positions={"AAA": Position(qty=1, stop_loss=9.0)})

    def test_new_is_todays_first_run_state(self):
        fresh = Portfolio.new(_P, 10000.0, "2026-11-18")
        self.assertEqual(list(json_mapper.to_dict(fresh).items()),
                         [("balance", 10000.0), ("equity", 10000.0), ("positions", {}), ("history", []),
                          ("start_date", "2026-11-18"), ("profile", "BALANCED")])

    def test_stop_changes_record_events_in_order(self):
        p = self._portfolio()
        self.assertTrue(p.raise_stop("AAA", 9.5, "trail"))
        self.assertFalse(p.raise_stop("AAA", 9.0, "trail"))  # refused: no event
        p.restore_stop("AAA", 8.0, "breaker restore")
        self.assertEqual(p.pull_events(), [StopChanged("ai_game", "AAA", 9.0, 9.5, "trail"),
                                           StopChanged("ai_game", "AAA", 9.5, 8.0, "breaker restore")])
        self.assertEqual(p.pull_events(), [])

    def test_unknown_position_is_a_domain_error(self):
        with self.assertRaises(DomainError):
            self._portfolio().raise_stop("ZZZ", 1.0, "x")


class RepositoryContract:
    """Every PortfolioRepository adapter passes these (ADR 0003). Subclasses set ``make``."""

    def make(self, ids):
        raise NotImplementedError

    def test_first_load_is_a_fresh_portfolio(self):
        p = self.make([_P]).load(_P)
        self.assertEqual((p.id, p.balance, p.positions, p.history), (_P, 10000.0, {}, []))

    def test_save_then_load_gives_the_same_state(self):
        repo = self.make([_P])
        repo.save(json_mapper.from_dict(_P, _rich_state()))
        self.assertEqual(_dumps(json_mapper.to_dict(repo.load(_P))), _dumps(_rich_state()))

    def test_loads_are_independent_copies(self):
        repo = self.make([_P])
        repo.save(json_mapper.from_dict(_P, _rich_state()))
        a = repo.load(_P)
        a.positions["AAA"].raise_stop(9.9)
        self.assertEqual(repo.load(_P).positions["AAA"].stop_loss, 9.0)

    def test_portfolios_are_kept_apart(self):
        other = PortfolioId("other")
        repo = self.make([_P, other])
        repo.save(json_mapper.from_dict(other, {"balance": 1.0}))
        self.assertEqual(repo.load(_P).balance, 10000.0)
        self.assertEqual(repo.load(other).balance, 1.0)

    def test_unknown_portfolio_is_refused(self):
        repo = self.make([_P])
        with self.assertRaises(UnknownPortfolio):
            repo.load(PortfolioId("nope"))
        with self.assertRaises(UnknownPortfolio):
            repo.save(Portfolio(id=PortfolioId("nope")))


class TestJsonRepositoryContract(RepositoryContract, unittest.TestCase):
    def make(self, ids):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        files = {i: PortfolioFile(root / f"{i}.json", root / "Backup" / str(i)) for i in ids}
        return JsonPortfolioRepository(files, starting_capital=10000.0, log=mock.Mock())


class TestMemoryRepositoryContract(RepositoryContract, unittest.TestCase):
    def make(self, ids):
        return InMemoryPortfolioRepository(ids, starting_capital=10000.0)


class TestUnitOfWork(unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryPortfolioRepository([_P])
        self.repo.save(json_mapper.from_dict(_P, _rich_state()))
        self.bus, self.seen = EventBus(), []
        self.bus.subscribe(StopChanged, self.seen.append)
        self.service = PortfolioService(self.repo, self.bus)

    def test_commit_saves_then_publishes(self):
        with self.service.unit_of_work() as uow:
            uow.get(_P).raise_stop("AAA", 9.5, "trail")
            self.assertEqual(self.seen, [])
            uow.commit()
        self.assertEqual(self.repo.stored[_P]["positions"]["AAA"]["stop_loss"], 9.5)
        self.assertEqual(self.seen, [StopChanged("ai_game", "AAA", 9.0, 9.5, "trail")])

    def test_no_commit_saves_nothing(self):
        with self.service.unit_of_work() as uow:
            uow.get(_P).raise_stop("AAA", 9.5, "trail")
        self.assertEqual(self.repo.stored[_P]["positions"]["AAA"]["stop_loss"], 9.0)
        self.assertEqual(self.seen, [])

    def test_get_loads_once(self):
        with self.service.unit_of_work() as uow:
            self.assertIs(uow.get(_P), uow.get(_P))

    def test_a_failed_save_publishes_nothing(self):
        with mock.patch.object(self.repo, "save", side_effect=StorageError("disk")):
            with self.service.unit_of_work() as uow:
                uow.get(_P).raise_stop("AAA", 9.5, "trail")
                with self.assertRaises(StorageError):
                    uow.commit()
        self.assertEqual(self.seen, [])


class TestStoreSwitch(unittest.TestCase):
    """ADR 0003: JSON by default; only AETHER_PORTFOLIO_STORE=db asks for the database."""

    def test_default_is_json(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(boot.PORTFOLIO_STORE_ENV, None)
            self.assertEqual(boot.portfolio_store_kind(), "json")

    def test_a_database_url_alone_does_not_switch(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://x/y"}):
            os.environ.pop(boot.PORTFOLIO_STORE_ENV, None)
            self.assertIsInstance(boot.bootstrap().portfolio._repository, JsonPortfolioRepository)

    def test_db_fails_loudly_until_it_exists(self):
        for value in ("db", "DB ", "sql"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {boot.PORTFOLIO_STORE_ENV: value}):
                with self.assertRaises(StorageError):
                    boot.bootstrap()

    def test_injected_repository_is_used(self):
        repo = InMemoryPortfolioRepository()
        self.assertIs(boot.bootstrap(portfolio_repository=repo).portfolio._repository, repo)

    def test_default_files_follow_the_redirected_data_dir(self):
        # tests/__init__.py points DEFAULT_DATA_DIR at a temp dir, so this never sees Data/.
        f = boot.default_portfolio_files()[DEFAULT_PORTFOLIO]
        self.assertEqual(f.path, boot.DEFAULT_DATA_DIR / "ai_portfolio_game.json")
        self.assertNotIn(Path(__file__).resolve().parent.parent / "Data", Path(f.path).resolve().parents)

    def test_harness_cleared_the_switch(self):
        self.assertNotIn(boot.PORTFOLIO_STORE_ENV, os.environ)


if __name__ == "__main__":
    unittest.main()
