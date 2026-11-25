"""Market-data service: the OHLCV cache (R&D #78 S5). Temp folders and fakes only."""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_portfolio_game as game
import sell_rules
from aether.services.market_data.adapters.ohlcv_json import JsonOhlcvStore
from aether.services.market_data.api import MAX_STALE_DAYS, OhlcvService, age_days, closes, is_stale

TODAY = datetime.date(2026, 11, 25)


def _bars(n, last="2026-11-24", start=100.0):
    d = datetime.date.fromisoformat(last)
    return {"Time Series (Daily)": {(d - datetime.timedelta(days=i)).isoformat(): {"4. close": str(start + i)}
                                    for i in range(n)}}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.repair = mock.Mock(return_value={"updated": 0})
        self.attempted = set()
        self.log = mock.Mock()
        self.svc = OhlcvService(JsonOhlcvStore(self.dir), self.repair, self.attempted,
                                sma=sell_rules.sma_from_closes, log=self.log)

    def write(self, sym, data):
        Path(self.dir, f"{sym}_daily.json").write_text(data if isinstance(data, str) else json.dumps(data))


class TestRules(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(closes({"2026-11-02": {"4. close": "2"}, "2026-11-01": {"4. close": "1"}}), [1.0, 2.0])
        self.assertEqual(age_days("2026-11-15", TODAY), 10)
        self.assertFalse(is_stale(10))
        self.assertTrue(is_stale(11))
        self.assertTrue(is_stale(None))
        self.assertEqual(MAX_STALE_DAYS, 10)
        self.assertEqual(game._MAX_STALE_DAYS, MAX_STALE_DAYS)  # one home


class TestWiring(unittest.TestCase):
    def test_the_sma_rule_is_required(self):
        # #327 review: without it sma50 returned None with a misleading "data" log line.
        with self.assertRaises(TypeError):
            OhlcvService(JsonOhlcvStore("."), mock.Mock(), set())
        with self.assertRaises(TypeError):
            OhlcvService(JsonOhlcvStore("."), mock.Mock(), set(), sma=None)


class TestReads(_Base):
    def test_closes_and_age(self):
        self.write("AAA", _bars(3))
        self.assertEqual(self.svc.closes("AAA"), [102.0, 101.0, 100.0])  # oldest first
        self.assertEqual(self.svc.age_days("AAA", TODAY), 1)

    def test_missing_empty_and_broken(self):
        self.write("EMPTY", {"Time Series (Daily)": {}})
        self.write("BAD", "{broken")
        for sym in ("NONE", "EMPTY", "BAD"):
            with self.subTest(sym=sym):
                self.assertEqual(self.svc.closes(sym), [])
                self.assertIsNone(self.svc.age_days(sym, TODAY))
        self.assertIsNone(self.svc.series("NONE"))
        with self.assertRaises(ValueError):
            self.svc.series("BAD")


class TestHeal(_Base):
    def test_outcomes_and_the_once_per_process_guard(self):
        self.repair.return_value = {"updated": 1}
        self.assertTrue(self.svc.heal("AAA", TODAY))
        self.repair.assert_called_once_with(["AAA"], "2026-11-25")
        self.assertFalse(self.svc.heal("AAA", TODAY))  # once per process
        self.assertEqual(self.repair.call_count, 1)

    def test_a_locked_api_releases_the_guard(self):
        self.repair.return_value = {"locked": True}
        self.assertFalse(self.svc.heal("AAA", TODAY))
        self.assertNotIn("AAA", self.attempted)
        self.assertIn("deferred", self.log.info.call_args[0][0])

    def test_zero_updates_and_errors(self):
        self.assertFalse(self.svc.heal("AAA", TODAY))
        self.assertIn("yielded 0 updates", self.log.warning.call_args[0][0])
        self.repair.side_effect = RuntimeError("down")
        self.assertFalse(self.svc.heal("BBB", TODAY))
        self.assertIn("Failed to heal BBB cache: down", self.log.warning.call_args[0][0])


class TestSma50(_Base):
    def test_fresh_cache(self):
        self.write("AAA", _bars(60))
        self.assertAlmostEqual(self.svc.sma50("AAA", TODAY), sum(100.0 + i for i in range(50)) / 50)
        self.repair.assert_not_called()

    def test_stale_cache_heals_then_reads_the_new_file(self):
        self.write("AAA", _bars(60, last="2026-10-01"))

        def heal(sym):
            self.write(sym, _bars(60, start=200.0))
            return True

        self.assertAlmostEqual(self.svc.sma50("AAA", TODAY, heal=heal), sum(200.0 + i for i in range(50)) / 50)
        self.assertIn("stale by 55d", self.log.warning.call_args_list[0][0][0])

    def test_a_failed_heal_means_no_sma(self):
        self.assertIsNone(self.svc.sma50("NONE", TODAY, heal=lambda s: False))
        self.assertIn("heal failed", self.log.warning.call_args[0][0])

    def test_default_heal_uses_the_guard(self):
        self.svc.sma50("NONE", TODAY)
        self.assertEqual(self.attempted, {"NONE"})


class TestGameUsesTheService(unittest.TestCase):
    def test_game_passes_its_folder_guard_and_refresh(self):
        with mock.patch.object(game, "market_ohlcv", wraps=game.market_ohlcv) as mo:
            game._load_closes("NONE")
        kw = mo.call_args.kwargs
        self.assertEqual(kw["symbol_dir"], game.SYMBOL_FULL_DIR)
        self.assertIs(kw["attempted"], game._HEAL_ATTEMPTED)
        self.assertIs(kw["sma"], sell_rules.sma_from_closes)
        with mock.patch.object(game.rapidapi, "repair_missing", return_value={"updated": 0}) as rep:
            kw["repair"](["X"], "2026-11-25")
        rep.assert_called_once_with(["X"], "2026-11-25")

    def test_sma50_heals_through_the_game_patch_point(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(game, "SYMBOL_FULL_DIR", Path(d)), \
                mock.patch.object(game, "_heal_symbol_cache", return_value=False) as heal, \
                mock.patch.object(game, "_log"):
            self.assertIsNone(game._sma50("NONE"))
        heal.assert_called_once_with("NONE")


if __name__ == "__main__":
    unittest.main()
