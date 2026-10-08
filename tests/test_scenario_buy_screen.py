"""B6 tests for aether.scenario.steps.screen_buys.

``screen_buys`` is the BUY screening block lifted out of
``run_daily_ai_management`` — it runs after :func:`execute_exits` and hands
slot sizing plus a ranked candidate list to momentum rotation and BUY
execution. It buys nothing. These tests pin the behaviours it carries over from
the root, unchanged:

1. **Slots** — ``determine_max_positions`` gets the cash ratio (0 when equity
   <= 1), the held count and the base cap; ``available_slots`` and
   ``min_cash_required`` follow from it.
2. **Scan** — held rows only record a score; excluded instruments, the s10
   floor, an inactive setup or no price drop a row before any gate.
3. **Gates, in order** — Zero-Trust freshness (one heal), CNXC gap guard
   (``row[10]``), R:R and 5% target gain on explicit S/R (elite waiver;
   incomplete S/R only logs), then the profile threshold or a confirmed bottom.
4. **Overbought guard** (R&D #32) and the final sort.

Every collaborator is patched on the live module and ``ws`` is a hermetic fake —
no network or disk. Re-export and root parity are pinned in
``test_scenario_steps`` and ``test_scenario_parity``.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ai_portfolio_game as game  # noqa: E402
from aether.scenario.steps import screen_buys  # noqa: E402


_TODAY = "2026-10-08"
_RULES = {"max_positions": 5, "cash_buffer_pct": 0.2, "min_score_threshold": 5.0}


class _FakeWS:
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=2, values_only=True):
        return list(self._rows)


def _row(sym="AAA", setup="OK", s10=3.0, l60=4.0, stop=90.0, target=130.0, prev=100.0,
         pgr="Bullish", industry="Tech"):
    """A Research-sheet row that passes every gate at price 100 unless overridden."""
    r = [None] * 26
    r[3], r[4], r[6] = sym, industry, pgr
    r[9], r[10], r[11] = stop, prev, target
    r[20], r[24], r[25] = setup, s10, l60
    return tuple(r)


def _state(balance=500.0, equity=1000.0, positions=None):
    return {"balance": balance, "equity": equity, "positions": dict(positions or {})}


class _Harness:
    """Patch every collaborator screen_buys resolves via _pkg()."""

    def __init__(self, *, max_pos=None, excluded=(), floor=2.0, stale=(), heal_fixes=(),
                 age=None, bottom=(False, ""), elite=False, cache=None, min_rr=2.0):
        self.log = mock.MagicMock()
        self.max_pos = mock.MagicMock(side_effect=lambda ratio, held, base: base if max_pos is None else max_pos)
        self.excluded = mock.MagicMock(side_effect=lambda s: s in excluded)
        self.floor = mock.MagicMock(return_value=floor)
        healed = set()

        def _stale(s, max_stale_days):
            return s in stale and s not in healed

        def _heal(s):
            if s in heal_fixes:
                healed.add(s)
                return True
            return False

        self.stale = mock.MagicMock(side_effect=_stale)
        self.heal = mock.MagicMock(side_effect=_heal)
        self.age = mock.MagicMock(return_value=age)
        self.bottom = mock.MagicMock(return_value=bottom)
        self.elite = mock.MagicMock(return_value=elite)
        self.series = mock.MagicMock(return_value=("H", "L", "C", None))
        self.stop = mock.MagicMock(return_value={"stop": 88.0, "source": "swing"})
        self.target = mock.MagicMock(return_value={"target": 120.0, "source": "fractal"})
        self.cache = mock.MagicMock(side_effect=lambda s, today: (cache or {}).get(s))
        self.min_rr = min_rr

    def __enter__(self):
        self._stack = ExitStack()
        for ctx in (
            mock.patch.object(game, "_log", self.log),
            mock.patch.object(game, "determine_max_positions", self.max_pos),
            mock.patch.object(game.instruments, "is_excluded", self.excluded),
            mock.patch.object(game, "adaptive_s10_floor", self.floor),
            mock.patch.object(game, "_cache_stale", self.stale),
            mock.patch.object(game, "_heal_symbol_cache", self.heal),
            mock.patch.object(game, "_cache_age_days", self.age),
            mock.patch.object(game, "is_bottom_confirmed", self.bottom),
            mock.patch.object(game.risk_utils, "is_elite_breakout_candidate", self.elite),
            mock.patch.object(game.risk_utils, "_load_ohlcv_series", self.series),
            mock.patch.object(game.risk_utils, "resolve_stop_detailed", self.stop),
            mock.patch.object(game.risk_utils, "resolve_target_detailed", self.target),
            mock.patch.object(game, "_load_symbol_today_cache", self.cache),
            mock.patch.object(game.CFG, "system_default_min_rr", self.min_rr),
        ):
            self._stack.enter_context(ctx)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    def logged(self):
        calls = self.log.info.call_args_list + self.log.warning.call_args_list
        return " | ".join(str(c.args[0]) for c in calls)


def _run(rows, state=None, prices=None, rules=_RULES, profile="BALANCED"):
    state = _state() if state is None else state
    prices = {"AAA": 100.0} if prices is None else prices
    return screen_buys(state, prices, rules, _FakeWS(rows), profile, _TODAY)


def _syms(out):
    return [b["sym"] for b in out["top_buys"]]


class TestSlots(unittest.TestCase):
    def test_slots_and_cash_buffer(self):
        state = _state(balance=300.0, equity=1000.0, positions={"H1": {}, "H2": {}})
        with _Harness() as h:
            out = _run([], state=state)
        h.max_pos.assert_called_once_with(0.3, 2, 5)
        self.assertEqual((out["max_positions"], out["available_slots"]), (5, 3))
        self.assertEqual(out["min_cash_required"], 200.0)
        self.assertNotIn("Slot Expansion", h.logged())

    def test_expansion_is_logged_and_widens_slots(self):
        with _Harness(max_pos=7) as h:
            out = _run([], state=_state(positions={"H1": {}}))
        self.assertEqual((out["max_positions"], out["available_slots"]), (7, 6))
        self.assertIn("from 5 to 7", h.logged())

    def test_degenerate_equity_gives_zero_cash_ratio(self):
        with _Harness() as h:
            _run([], state=_state(balance=1.0, equity=1.0))
        self.assertEqual(h.max_pos.call_args.args[0], 0.0)


class TestScan(unittest.TestCase):
    def test_held_symbol_only_records_its_score(self):
        state = _state(positions={"AAA": {}})
        with _Harness() as h:
            out = _run([_row(s10=3.0, l60=None), _row(sym=None)], state=state)
        self.assertEqual(out["active_position_scores"], {"AAA": 3.0})
        self.assertEqual(out["top_buys"], [])
        h.excluded.assert_not_called()

    def test_excluded_instrument_is_skipped_before_the_floor(self):
        with _Harness(excluded={"AAA"}) as h:
            out = _run([_row()])
        self.assertEqual(out["top_buys"], [])
        h.floor.assert_not_called()

    def test_s10_floor_uses_cash_pct_and_logs_only_live_setups(self):
        with _Harness(floor=3.5) as h:
            out = _run([_row(s10=3.0), _row(sym="BBB", s10=3.0, setup="")],
                       state=_state(balance=250.0, equity=1000.0), prices={"AAA": 100.0, "BBB": 50.0})
        h.floor.assert_called_with(25.0)
        self.assertEqual(out["top_buys"], [])
        self.assertIn("Momentum Floor): AAA", h.logged())
        self.assertNotIn("BBB", h.logged())

    def test_s10_at_the_floor_passes(self):
        with _Harness(floor=3.0):
            self.assertEqual(_syms(_run([_row(s10=3.0)])), ["AAA"])

    def test_inactive_setup_or_no_price_is_skipped_silently(self):
        for row, prices in ((_row(setup=""), {"AAA": 100.0}), (_row(), {})):
            with self.subTest(setup=row[20], prices=prices), _Harness() as h:
                out = _run([row], prices=prices)
                self.assertEqual(out["top_buys"], [])
                h.stale.assert_not_called()

    def test_legacy_setup_values_count_as_active(self):
        for setup in ("1", 1):
            with self.subTest(setup=setup), _Harness():
                self.assertEqual(_syms(_run([_row(setup=setup)])), ["AAA"])


class TestFreshnessGate(unittest.TestCase):
    def test_stale_after_heal_is_rejected_with_age(self):
        for age, desc in ((None, "missing"), (4, "4d stale")):
            with self.subTest(age=age), _Harness(stale={"AAA"}, age=age) as h:
                out = _run([_row()])
                self.assertEqual(out["top_buys"], [])
                h.heal.assert_called_once_with("AAA")
                self.assertIn(f"OHLCV cache {desc}", h.logged())
                h.bottom.assert_not_called()

    def test_heal_that_refreshes_lets_the_row_through(self):
        with _Harness(stale={"AAA"}, heal_fixes={"AAA"}) as h:
            out = _run([_row()])
        self.assertEqual(_syms(out), ["AAA"])
        self.assertEqual(h.stale.call_args.kwargs, {"max_stale_days": game._MAX_STALE_DAYS})


class TestGapGuard(unittest.TestCase):
    def test_gap_down_of_8pct_needs_a_confirmed_bottom(self):
        with _Harness() as h:
            out = _run([_row(prev=100.0)], prices={"AAA": 92.0})
        self.assertEqual(out["top_buys"], [])
        self.assertIn("CNXC Trap): AAA", h.logged())
        with _Harness(bottom=(True, "capitulation")):
            out = _run([_row(prev=100.0, stop=80.0, target=130.0)], prices={"AAA": 92.0})
        self.assertEqual(_syms(out), ["AAA"])

    def test_smaller_gap_or_no_prev_close_passes(self):
        for prev in (99.0, None, 0):
            with self.subTest(prev=prev), _Harness():
                out = _run([_row(prev=prev, stop=80.0)], prices={"AAA": 91.2})
                self.assertEqual(_syms(out), ["AAA"])


class TestRiskRewardGate(unittest.TestCase):
    def test_low_rr_is_rejected_unless_elite(self):
        row = _row(stop=95.0, target=108.0)  # R:R 8/5 = 1.6, gain 8%
        with _Harness() as h:
            self.assertEqual(_run([row])["top_buys"], [])
        self.assertIn("Reward-to-Risk ratio of 1.6:1", h.logged())
        with _Harness(elite=True) as h:
            self.assertEqual(_syms(_run([row])), ["AAA"])
        self.assertIn("Breakout Waiver] Waived 2.0:1", h.logged())
        h.elite.assert_called_once_with(7.0, 3.0)

    def test_rr_exactly_at_minimum_passes(self):
        with _Harness():
            self.assertEqual(_syms(_run([_row(stop=95.0, target=110.0)])), ["AAA"])

    def test_stop_at_or_above_price_is_rejected(self):
        with _Harness() as h:
            self.assertEqual(_run([_row(stop=100.0, target=130.0)])["top_buys"], [])
        self.assertIn("ratio of 0.0:1", h.logged())

    def test_small_target_gain_is_rejected_unless_elite(self):
        row = _row(stop=99.0, target=104.0)  # R:R 4.0, gain 4%
        with _Harness(min_rr=1.0) as h:
            self.assertEqual(_run([row])["top_buys"], [])
        self.assertIn("target gain of 4.0% is less than the required 5.0%", h.logged())
        with _Harness(min_rr=1.0, elite=True) as h:
            self.assertEqual(_syms(_run([row])), ["AAA"])
        self.assertIn("Waived 5.0% target upside", h.logged())

    def test_incomplete_sr_logs_a_thesis_and_does_not_gate(self):
        for stop, target in ((None, 130.0), (90.0, None), ("", "")):
            with self.subTest(stop=stop, target=target), _Harness(excluded=set()) as h:
                out = _run([_row(stop=stop, target=target)])
                self.assertEqual(_syms(out), ["AAA"])
                h.series.assert_called_once_with("AAA")
                self.assertEqual(h.stop.call_args, mock.call(
                    100.0, highs="H", lows="L", closes="C", exclude_swing=False))
                self.assertEqual(h.target.call_args, mock.call(
                    100.0, highs="H", lows="L", closes="C", exclude_swing=False))
                self.assertIn("[Synthesized Risk Thesis] AAA @ $100.0: stop $88.0 (swing) / target $120.0 (fractal)",
                              h.logged())


class TestProfileThreshold(unittest.TestCase):
    def test_below_threshold_without_bottom_is_rejected(self):
        with _Harness() as h:
            out = _run([_row(s10=2.0, l60=2.9)])
        self.assertEqual(out["top_buys"], [])
        self.assertIn("Combined score 4.9 is below the BALANCED minimum of 5.0", h.logged())

    def test_threshold_is_inclusive(self):
        with _Harness():
            self.assertEqual(_syms(_run([_row(s10=2.0, l60=3.0)])), ["AAA"])

    def test_confirmed_bottom_admits_a_low_score(self):
        with _Harness(bottom=(True, "2B")):
            out = _run([_row(s10=2.0, l60=0.0)])
        self.assertEqual(out["top_buys"][0]["bottom_desc"], " (Bottom Confirmed: 2B)")

    def test_candidate_fields(self):
        with _Harness():
            out = _run([_row(pgr=None, industry="Energy")])
        self.assertEqual(out["top_buys"], [{
            "sym": "AAA", "price": 100.0, "total": 7.0, "pgr": "Neutral", "s10": 3.0,
            "l60": 4.0, "bottom_desc": "", "industry": "Energy"}])


class TestOverboughtGuardAndSort(unittest.TestCase):
    def _two(self, cache):
        rows = [_row(), _row(sym="BBB", s10=3.0, l60=3.5)]
        with _Harness(cache=cache) as h:
            out = _run(rows, prices={"AAA": 100.0, "BBB": 100.0})
        return out, h

    def test_penalty_can_reorder_candidates(self):
        out, h = self._two({"AAA": {"checklist_stocks": {"strengthCount": 0, "timingCount": 0}}})
        self.assertEqual([(b["sym"], b["total"]) for b in out["top_buys"]], [("BBB", 6.5), ("AAA", 5.5)])
        self.assertIn("-1.5 score penalty to AAA", h.logged())
        h.cache.assert_any_call("AAA", _TODAY)

    def test_weak_industry_is_penalised(self):
        out, _ = self._two({"AAA": {"checklist_stocks": {"industry": "Weak"}}})
        self.assertEqual(out["top_buys"][1]["total"], 5.5)

    def test_no_penalty_cases(self):
        for cache in ({}, {"AAA": {}}, {"AAA": {"checklist_stocks": {"strengthCount": 0, "timingCount": 1}}},
                      {"AAA": {"checklist_stocks": {"strengthCount": 1, "timingCount": 0}}}):
            with self.subTest(cache=cache):
                out, _ = self._two(cache)
                self.assertEqual([(b["sym"], b["total"]) for b in out["top_buys"]], [("AAA", 7.0), ("BBB", 6.5)])


if __name__ == "__main__":
    unittest.main()
