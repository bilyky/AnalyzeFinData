"""Characterization test: runners.run_daily_management == run_daily_ai_management.

Each case runs the root ``ai_portfolio_game.run_daily_ai_management`` and the B7
runner under the same patches, on fresh copies of the same fixtures, and
requires the same observable result:

* the state passed to ``save_game`` and the transactions passed to
  ``update_excel_log``;
* every call on the game logger, in order;
* the decision log, the closed-trade DNA, the alert emails and the
  covered-call pass calls;
* the exception raised, if any.

The cases cover every path out of the root: market closed, missing workbook,
stale workbook, no prices, an unexpected error, and full runs in and out of
market hours (exits, scale-out, queued orders, buys, rotation, pyramiding).
Nothing touches the network or the real Data folder.
"""
import copy
import datetime as _real_datetime
import json
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
import aether.scenario as pkg  # noqa: E402
from aether.scenario import runners  # noqa: E402
from tests._helpers import research_workbook  # noqa: E402


class _Clock:
    """``datetime`` stand-in with a fixed today/now; everything else is the real module."""

    class date(_real_datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 30)

    class datetime(_real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 30, 10, 15, 0, tzinfo=tz)

    def __getattr__(self, name):
        return getattr(_real_datetime, name)


_TODAY = "2026-10-30"


def _row(sym, price, s10, l60, setup="OK", stop=None, target=None, prev_sell=None, prev_buy=None, pgr="Bullish"):
    r = [None] * 26
    r[0], r[3], r[4], r[6] = 1, sym, "Tech", pgr
    r[8], r[9], r[10], r[11] = prev_sell, stop, price if prev_buy is None else prev_buy, target
    r[20], r[24], r[25] = setup, s10, l60
    return r


# Held: WIN (profit, near peak -> scale-out + pyramid candidate), LOSE (below its stop -> exit),
# OLD (mature, weak score -> rotation fodder). Candidates: NEW1/NEW2 pass the buy gates,
# GAP gaps down 10%, LOWRR fails R:R, FLOOR is under the s10 floor.
_ROWS = [
    _row("WIN", 120.0, 4.0, 5.0, prev_sell=118.0),
    _row("LOSE", 80.0, -3.0, -4.0, prev_sell=82.0),
    _row("OLD", 55.0, 0.5, 0.5, prev_sell=55.0),
    _row("NEW1", 50.0, 5.0, 6.0, stop=45.0, target=65.0),
    _row("NEW2", 30.0, 4.5, 4.0, stop=27.0, target=39.0),
    _row("GAP", 45.0, 6.0, 6.0, stop=40.0, target=60.0, prev_buy=50.0),
    _row("LOWRR", 100.0, 2.5, 3.0, stop=95.0, target=104.0),  # not elite, so R:R applies
    _row("FLOOR", 20.0, 0.1, 9.0, stop=18.0, target=26.0),
    _row("QUEUED", 40.0, 3.0, 3.0),
]
_PRICES = {"WIN": 120.0, "LOSE": 78.0, "OLD": 55.0, "NEW1": 50.0, "NEW2": 30.0, "GAP": 45.0,
           "LOWRR": 100.0, "FLOOR": 20.0, "QUEUED": 40.0, "SPY": 500.0}


def _state():
    return {
        "balance": 40000.0, "equity": 60000.0, "start_date": "2026-01-02", "history": [],
        "profile": "BALANCED", "profile_mode": "ADAPTIVE",
        "queued_orders": [{"type": "BUY", "symbol": "QUEUED", "reason": "operator"}],
        "positions": {
            "WIN": {"qty": 50, "cost": 100.0, "stop_loss": 95.0, "highest_close_since_acq": 121.0},
            "LOSE": {"qty": 40, "cost": 90.0, "stop_loss": 80.0, "highest_close_since_acq": 92.0},
            "OLD": {"qty": 60, "cost": 50.0, "stop_loss": 50.0, "highest_close_since_acq": 56.0},
        },
    }


def _scale_out(price, cost, *args, **kwargs):
    """Only WIN (cost 100) banks a slice; the plan is not told the symbol."""
    return (0.25, "bank 25% (test)") if cost == 100.0 else (0.0, "none (test)")


class _Run:
    """Patch the world, run one entry point, and record what it did."""

    def __init__(self, *, market_open=True, market_hours=True, exists=True, stale=None,
                 prices=None, load_error=None, manual_profile="BALANCED", force=True, extra=None):
        self.market_open, self.market_hours = market_open, market_hours
        self.exists, self.stale = exists, stale
        self.prices = _PRICES if prices is None else prices
        self.load_error, self.manual_profile, self.force = load_error, manual_profile, force
        # extra = {"positions": {...}, "rows": [...], "prices": {...}} added on top of the fixture
        self.extra = extra or {}

    def __call__(self, entry):
        log = mock.MagicMock()
        save, excel, email, dna, decisions, calls = (mock.MagicMock() for _ in range(6))
        path = mock.MagicMock()
        path.exists.return_value = self.exists
        state = _state()
        state["positions"].update(copy.deepcopy(self.extra.get("positions", {})))
        rows = _ROWS + list(self.extra.get("rows", []))
        live = {**self.prices, **self.extra.get("prices", {})} if self.prices else {}
        load = mock.MagicMock(side_effect=self.load_error, return_value=state)
        # Snapshot at call time: later code may keep mutating the same objects.
        snap = {}
        save.side_effect = lambda s: snap.__setitem__("saved", copy.deepcopy(s))
        excel.side_effect = lambda s, t: snap.__setitem__("txs", copy.deepcopy(t))
        patches = [
            mock.patch.object(game, "datetime", _Clock()),
            mock.patch.object(game, "_log", log),
            mock.patch.object(game, "is_market_open", return_value=(self.market_open, "closed (test)")),
            mock.patch.object(game, "is_market_hours", return_value=self.market_hours),
            mock.patch.object(game, "load_game", load),
            mock.patch.object(game, "save_game", save),
            mock.patch.object(game, "update_excel_log", excel),
            mock.patch.object(game, "XLSX_FILE", path),
            mock.patch.object(game, "workbook_staleness", return_value=self.stale),
            mock.patch.object(game.notify, "send_email", email),
            mock.patch.object(game.openpyxl, "load_workbook", side_effect=lambda *a, **k: research_workbook(*rows)),
            mock.patch.object(game, "get_live_prices", return_value=dict(live)),
            mock.patch.object(game, "get_market_regime", return_value="BALANCED"),
            mock.patch.object(game, "_has_strong_setups_today", return_value=False),
            mock.patch.object(game, "_cache_stale", return_value=False),
            mock.patch.object(game, "_heal_symbol_cache", return_value=False),
            mock.patch.object(game, "is_bottom_confirmed", return_value=(False, "")),
            mock.patch.object(game, "backtrack_verify", return_value=(True, "3-day trend ok (test)")),
            mock.patch.object(game, "_load_symbol_today_cache", return_value=None),
            mock.patch.object(game, "_sma50", return_value=110.0),
            mock.patch.object(game, "log_closed_trade_dna", dna),
            mock.patch.object(game.decision_eval, "log_decisions", decisions),
            mock.patch.object(game.instruments, "is_scarcity_asset", return_value=False),
            mock.patch.object(game.risk_utils, "calculate_atr", return_value=2.0),
            mock.patch.object(game.risk_utils, "scale_out_plan", side_effect=_scale_out),
            mock.patch.object(game.options, "execute_weekly_covered_call_pass", calls),
            mock.patch("ai_client.evaluate", return_value=""),
        ]
        raised = None
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            game._HEAL_ATTEMPTED.clear()
            try:
                entry(force=self.force, manual_profile=self.manual_profile)
            except Exception as e:  # recorded and compared, not swallowed
                raised = (type(e).__name__, str(e))
        return {
            "raised": raised,
            "saved": snap.get("saved"),
            "save_calls": save.call_count,
            "txs": snap.get("txs"),
            "log": [(c[0], c[1]) for c in log.mock_calls],
            "emails": [c.args for c in email.call_args_list],
            "dna": [(c.args[0], c.args[2], c.args[3]) for c in dna.call_args_list],
            "decisions": [copy.deepcopy(c.args[0]) for c in decisions.call_args_list],
            "calls": calls.call_count,
        }


# Golden snapshot of the game's own output for every case below, recorded from
# run_daily_ai_management before the game was switched to the runner. Once the game
# calls the runner, "runner == game" compares the runner with itself; the golden file is
# what still pins the old behaviour. Regenerate only on purpose:
#   AETHER_UPDATE_GOLDEN=1 python -m unittest tests.test_scenario_runner
_GOLDEN_FILE = Path(__file__).resolve().parent / "fixtures" / "scenario_runner_golden.json"
_UPDATE_GOLDEN = os.environ.get("AETHER_UPDATE_GOLDEN") == "1"
_RECORDED = {}


def _jsonable(result):
    """The result as it round-trips through the golden file (tuples become lists)."""
    return json.loads(json.dumps(result, ensure_ascii=False))


def _golden():
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def tearDownModule():
    if _UPDATE_GOLDEN and _RECORDED:
        _GOLDEN_FILE.write_text(json.dumps(_RECORDED, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                encoding="utf-8", newline="\n")


class TestRunnerMatchesRoot(unittest.TestCase):
    def _same(self, run, **expect):
        root = _jsonable(run(game.run_daily_ai_management))
        mine = _jsonable(run(runners.run_daily_management))
        for key in root:
            with self.subTest(key=key):
                self.assertEqual(mine[key], root[key])
        case = self._testMethodName
        if _UPDATE_GOLDEN:
            _RECORDED[case] = root
        else:
            golden = _golden()
            self.assertIn(case, golden, f"no golden entry for {case}; regenerate on purpose")
            for key in golden[case]:
                with self.subTest(golden=key):
                    self.assertEqual(mine[key], golden[case][key])
        for key, check in expect.items():
            with self.subTest(expect=key):
                self.assertTrue(check(root), f"case precondition {key!r} failed: {root[key.split(':')[0]]!r}")
        return root

    def test_market_closed(self):
        self._same(_Run(market_open=False, force=False),
                   save_calls=lambda r: r["save_calls"] == 0)

    def test_market_closed_but_forced_still_runs(self):
        self._same(_Run(market_open=False, force=True),
                   save_calls=lambda r: r["save_calls"] == 1, txs=lambda r: bool(r["txs"]))

    def test_missing_workbook(self):
        self._same(_Run(exists=False), save_calls=lambda r: r["save_calls"] == 1,
                   txs=lambda r: r["txs"] == [])

    def test_stale_workbook(self):
        self._same(_Run(stale="workbook is from yesterday"),
                   emails=lambda r: len(r["emails"]) == 1, save_calls=lambda r: r["save_calls"] == 1)

    def test_no_prices_raises_after_saving(self):
        self._same(_Run(prices={}), raised=lambda r: r["raised"] and r["raised"][0] == "RuntimeError",
                   save_calls=lambda r: r["save_calls"] == 1)

    def test_unexpected_error_is_logged_not_raised(self):
        self._same(_Run(load_error=ValueError("corrupt game file")),
                   raised=lambda r: r["raised"] is None, save_calls=lambda r: r["save_calls"] == 0,
                   log=lambda r: any(name == "exception" for name, _ in r["log"]))

    def test_full_run_in_market_hours(self):
        root = self._same(_Run())
        types = sorted({(t["type"], t["symbol"]) for t in root["txs"]})
        # The fixture has to exercise the stages, or matching proves little.
        self.assertIn(("BUY", "QUEUED"), types)
        self.assertIn(("SELL", "LOSE"), types)
        self.assertTrue(any(t == "BUY" and s.startswith("NEW") for t, s in types), types)
        self.assertNotIn(("BUY", "GAP"), types)        # -10% gap, no confirmed bottom
        self.assertNotIn(("BUY", "LOWRR"), types)
        self.assertIn(("BUY_SCALE_IN", "OLD"), types)  # pyramiding ran
        self.assertIn(("SELL", "WIN"), types)          # WIN banked a slice (scale-out)
        logged = " | ".join(str(a[0]) for _, a in root["log"] if a)
        self.assertIn("CNXC Trap): GAP", logged)
        self.assertIn("Risk-Reward Gate): LOWRR", logged)
        self.assertIn("Momentum Floor): FLOOR", logged)
        self.assertEqual(root["calls"], 1)
        self.assertTrue(root["decisions"])

    def test_full_run_after_hours(self):
        root = self._same(_Run(market_hours=False))
        queued = [(q["type"], q["symbol"]) for q in root["saved"]["queued_orders"]]
        self.assertIn(("SELL", "LOSE"), queued)
        self.assertTrue(any(t == "BUY" for t, _ in queued), queued)
        self.assertEqual(root["calls"], 0)

    def test_aggressive_run_rotates(self):
        # Rotation (R&D #27) runs only for AGGRESSIVE in market hours; NEW1 (11.0) is the
        # elite buy and OLD (mature, score 1.0) is the position it replaces.
        root = self._same(_Run(manual_profile="AGGRESSIVE"))
        rotated = [t for t in root["txs"] if "[MOMENTUM ROTATION]" in t.get("details", "")]
        self.assertEqual([t["symbol"] for t in rotated], ["OLD"])

    def test_ai_override_on_exits(self):
        # R&D #14 / #195: a stored AI verdict downgrades a momentum SELL of a position safely
        # in profit (FADE: HOLD -> HOLD, FLAG: FLAG-FOR-REVIEW -> WATCH); a stop exit always
        # sells (SLIP). All three are soft exits (s10 + l60 < 0) below their 50-DMA (110),
        # so winner protection doesn't apply.
        extra = {
            "positions": {
                "FADE": {"qty": 20, "cost": 90.0, "stop_loss": 80.0, "highest_close_since_acq": 106.0,
                         "shadow_verdict": "HOLD"},
                "FLAG": {"qty": 20, "cost": 90.0, "stop_loss": 80.0, "highest_close_since_acq": 106.0,
                         "verdicts": {"gemini": "FLAG-FOR-REVIEW"}},
                "SLIP": {"qty": 20, "cost": 90.0, "stop_loss": 85.0, "highest_close_since_acq": 95.0,
                         "ai_verdict": "HOLD"},
            },
            "rows": [_row("FADE", 105.0, -2.0, -1.0, prev_sell=105.0),
                     _row("FLAG", 105.0, -2.0, -1.0, prev_sell=105.0),
                     _row("SLIP", 84.0, -2.0, -1.0, prev_sell=84.0)],
            "prices": {"FADE": 105.0, "FLAG": 105.0, "SLIP": 84.0},
        }
        root = self._same(_Run(extra=extra))
        sold = {t["symbol"] for t in root["txs"] if t["type"] == "SELL"}
        self.assertNotIn("FADE", sold)
        self.assertNotIn("FLAG", sold)
        self.assertIn("SLIP", sold)
        actions = {e["symbol"]: e["rules_action"] for batch in root["decisions"] for e in batch}
        self.assertEqual((actions["FADE"], actions["FLAG"], actions["SLIP"]), ("HOLD", "WATCH", "SELL"))
        logged = " | ".join(str(a[0]) for _, a in root["log"] if a)
        self.assertIn("[AI EXIT OVERRIDE] FADE", logged)
        self.assertIn("SLIP: AI verdict not applied", logged)

    def test_adaptive_profile(self):
        self._same(_Run(manual_profile=None))


class TestGoldenFile(unittest.TestCase):
    def test_golden_has_exactly_the_cases(self):
        cases = {n for n in dir(TestRunnerMatchesRoot) if n.startswith("test_")}
        self.assertEqual(set(_golden()), cases)


class TestPackageExport(unittest.TestCase):
    def test_runner_is_exported_from_the_package(self):
        self.assertIs(pkg.run_daily_management, runners.run_daily_management)
        self.assertIn("run_daily_management", pkg.__all__)


if __name__ == "__main__":
    unittest.main()
