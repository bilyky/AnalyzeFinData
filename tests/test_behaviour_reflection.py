"""Behaviour reflection in the weekly retrospective, and four report fixes.

aether.behaviour_reflection.reflect summarises the paper game's own buys and sells from the
ledger history (exit types, entry quality vs SPY, score whipsaw, turnover, pyramid after a
Rule B bank). It is pure: these tests feed it synthetic bars, never the real Data/ cache.

Report fixes pinned here:
- the win count took pnl_pct >= 0, so a flat trade counted as a win;
- the circuit-breaker list showed the last 5 of N without saying so;
- the validation table printed `factor` (None for combo patterns) and read `avg_fwd`, a key
  validate_patterns never writes, so every row showed +0.00%;
- live buys wrote buy_dna without the Setup flag, so the DNA showed Setup=None.
"""
import datetime
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import ai_portfolio_game as game
import pattern_discovery as pd
import retrospective_analyzer as ra
from aether import behaviour_reflection as br, ledgers


def _days(start="2026-11-02", n=40):
    d, out = datetime.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


DAYS = _days()


def _series(closes):
    return DAYS[:len(closes)], list(closes)


def _flat(v=100.0):
    return _series([v] * len(DAYS))


def _ramp(start=100.0, step=1.0):
    return _series([start + step * i for i in range(len(DAYS))])


def _tx(day, typ, sym, price=100.0, qty=10, pnl=None, details=""):
    t = {"date": DAYS[day], "time": "07:00:00", "type": typ, "symbol": sym, "price": price, "qty": qty,
         "details": details}
    if pnl is not None:
        t["pnl"] = pnl
    return t


BANK = "Scale-out (Bank-As-You-Go): bank 30% (crossed 1 tier(s), highest +1.5)"
PYR = "Pyramiding Scale-In (Breakout Peak, s10=6.6)"
STOP = "Exit: stop breached (price 114.67 <= stop 118.26) [STP LMT @ market]"


class ExitTypeTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(br.exit_type(STOP), "stop")
        self.assertEqual(br.exit_type("Exit: momentum decay (S10+L60=-2.9)"), "momentum_decay")
        self.assertEqual(br.exit_type(BANK), "rule_b")
        self.assertEqual(br.exit_type("\U0001f504 [MOMENTUM ROTATION] Sold mature position GNE"), "rotation")
        self.assertEqual(br.exit_type("Queued Sell: Technical decay + Strategic Rotation"), "other")
        self.assertEqual(br.exit_type(""), "no_reason")
        self.assertEqual(br.exit_type(None), "no_reason")


class ReflectTest(unittest.TestCase):
    def _reflect(self, history, bars, decisions=None, spy=None):
        return br.reflect(history, lambda s: bars.get(s, ([], [])), spy or _flat(), decisions)

    def test_sells_by_type_and_what_the_stock_did_after(self):
        hist = [_tx(0, "BUY", "UP"), _tx(0, "BUY", "DN"),
                _tx(2, "SELL", "UP", pnl=50.0, details=STOP),            # stopped, then rose 10% (premature)
                _tx(2, "SELL", "DN", pnl=-20.0, details=STOP),           # stopped, then fell (correct)
                _tx(35, "SELL", "UP", pnl=5.0)]                          # too new: under 10 sessions left
        r = self._reflect(hist, {"UP": _ramp(100, 1.0), "DN": _ramp(100, -1.0)}, spy=_ramp(100, 0.5))
        stop = [s for s in r["sells"] if s["type"] == "stop"][0]
        self.assertEqual((stop["month"], stop["n"], stop["pnl"], stop["measured"], stop["rose"]), ("2026-11", 2, 30.0, 2, 1))
        up, dn, spy = 112 / 102 - 1, 88 / 98 - 1, 106 / 101 - 1
        self.assertAlmostEqual(stop["median_after"], (up + dn) / 2 * 100, places=6)
        self.assertAlmostEqual(stop["median_vs_spy"], ((up - spy) + (dn - spy)) / 2 * 100, places=6)
        self.assertEqual(r["sells_skipped"], 1)
        self.assertEqual([s["type"] for s in r["sells"]], ["stop", "no_reason"])

    def test_entry_quality_vs_spy(self):
        hist = [_tx(0, "BUY", "UP"), _tx(0, "BUY", "DN"), _tx(1, "BUY", "NOBARS"),
                _tx(1, "BUY_SCALE_IN", "UP", details=PYR)]               # scale-ins are not new entries
        r = self._reflect(hist, {"UP": _ramp(100, 1.0), "DN": _ramp(100, -1.0)})
        self.assertEqual(r["entries_skipped"], 1)
        e = r["entries"][0]
        self.assertEqual((e["month"], e["measured"], e["beat_spy"]), ("2026-11", 2, 1))
        self.assertAlmostEqual(e["mean_vs_spy"], 0.0, places=6)          # +10% and -10% vs a flat SPY

    def test_score_whipsaw(self):
        hist = [_tx(0, "BUY", "A"), _tx(0, "BUY", "B"), _tx(0, "BUY", "C")]
        dec = [{"date": DAYS[1], "symbol": "A", "s10": 3, "l60": 2},
               {"date": DAYS[2], "symbol": "A", "s10": -4, "l60": 1},   # flips below 0 on day 2
               {"date": DAYS[1], "symbol": "B", "s10": 3, "l60": 2},
               {"date": DAYS[2], "symbol": "B", "s10": 3, "l60": 2},
               {"date": DAYS[3], "symbol": "B", "s10": 3, "l60": 2},
               {"date": DAYS[4], "symbol": "B", "s10": -9, "l60": 0},   # 4th logged day: outside the window
               {"date": DAYS[0], "symbol": "C", "s10": -9, "l60": 0}]   # buy day itself: not after
        self.assertEqual(self._reflect(hist, {}, dec)["whipsaw"], {"checked": 2, "flipped": 1, "no_log": 1})

    def test_turnover_and_days_held(self):
        hist = [_tx(0, "BUY", "A", qty=10), _tx(2, "SELL", "A", qty=3, details=BANK),
                _tx(3, "BUY_SCALE_IN", "A", qty=4, details=PYR), _tx(5, "SELL", "A", qty=11),
                _tx(0, "BUY", "B", qty=5), _tx(10, "SELL", "B", qty=5), _tx(11, "BUY", "C")]
        t = self._reflect(hist, {})["turnover"]
        self.assertEqual(t["months"], [{"month": "2026-11", "buys": 3, "scale_ins": 1, "sells": 3}])
        self.assertEqual(t["closed"], 2)
        self.assertEqual(t["median_days_held"], (7 + 14) / 2)            # A: 11-02..11-09, B: 11-02..11-16

    def test_pyramid_after_bank_nem_case(self):
        hist = [_tx(0, "BUY", "NEM", price=118.21, qty=9),
                _tx(4, "SELL", "NEM", price=125.59, qty=3, pnl=22.14, details=BANK),
                _tx(5, "BUY_SCALE_IN", "NEM", price=120.61, qty=6, details=PYR),
                _tx(7, "SELL", "NEM", price=114.67, qty=12, pnl=-56.88, details=STOP)]
        cases = self._reflect(hist, {})["pyramid_after_bank"]
        self.assertEqual(cases, [{"symbol": "NEM", "bank_date": DAYS[4], "bank_price": 125.59,
                                  "scale_in_date": DAYS[5], "scale_in_price": 120.61, "sessions": 1,
                                  "pnl_after": -56.88, "closed": True}])

    def test_pyramid_counts_each_scale_in_once_and_respects_the_window(self):
        hist = [_tx(0, "BUY", "COIN"), _tx(3, "SELL", "COIN", pnl=15.0, details=BANK),
                _tx(4, "SELL", "COIN", pnl=80.0, details=BANK), _tx(4, "BUY_SCALE_IN", "COIN", details=PYR),
                _tx(9, "SELL", "COIN", pnl=7.0),
                _tx(0, "BUY", "LATE"), _tx(1, "SELL", "LATE", details=BANK),
                _tx(8, "BUY_SCALE_IN", "LATE", details=PYR),                 # 7 sessions later: outside
                _tx(0, "BUY", "NEW"), _tx(1, "SELL", "NEW", details=BANK), _tx(2, "SELL", "NEW", details=STOP),
                _tx(3, "BUY", "NEW"), _tx(4, "BUY_SCALE_IN", "NEW", details=PYR)]   # a new position
        cases = self._reflect(hist, {})["pyramid_after_bank"]
        self.assertEqual([(c["symbol"], c["bank_date"], c["sessions"], c["pnl_after"]) for c in cases],
                         [("COIN", DAYS[4], 0, 7.0)])

    def test_render_has_every_section(self):
        hist = [_tx(0, "BUY", "UP"), _tx(2, "SELL", "UP", pnl=5.0, details=STOP)]
        text = "\n".join(br.render(self._reflect(hist, {"UP": _ramp()})))
        for part in ("BEHAVIOUR REFLECTION", "a) Sells by exit type", "2026-11 stop", "b) Entry quality",
                     "c) Score whipsaw", "d) Turnover", "e) Pyramid after bank", "skipped (under 10 sessions"):
            self.assertIn(part, text)


class RetrospectiveFixesTest(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(ledgers.TRADE_DNA_FILE.parent.name, "Data")        # the harness temp folder
        self.assertNotEqual(ra.GAME_FILE.parent.name, "Data")
        for f in (ledgers.TRADE_DNA_FILE, ledgers.RETRO_REPORT_FILE, ra.GAME_FILE):
            if f.exists():
                f.unlink()

    def tearDown(self):
        if ra.GAME_FILE.exists():
            ra.GAME_FILE.unlink()

    def test_flat_trade_is_not_a_win(self):
        w, f, lo = ra.win_loss_split([{"pnl_pct": 1.0}, {"pnl_pct": 0.0}, {"pnl_pct": -1.0}])
        self.assertEqual((len(w), len(f), len(lo)), (1, 1, 1))

    def test_breaker_list_says_last_5_of_n(self):
        trig = [{"date": f"2026-10-{i:02d}", "time": "10:00", "reason": "r", "spy_return_pct": -2.1,
                 "vxx_return_pct": 9.0} for i in range(1, 8)]
        lines = ra.breaker_lines(trig)
        self.assertIn("    Last 5 of 7:", lines)
        self.assertEqual(sum(1 for x in lines if "-> Reason" in x), 5)
        self.assertNotIn("Last", " ".join(ra.breaker_lines(trig[:3])))

    def test_report_counts_flat_trades_and_has_the_behaviour_section(self):
        trades = [{"symbol": s, "buy_date": "2026-10-01", "buy_price": 10, "sell_date": "2026-10-05",
                   "sell_price": 10, "pnl_pct": p, "holding_days": 4, "buy_dna": {"pgr": "Bu", "score": 8.0}}
                  for s, p in (("W", 2.0), ("F", 0.0), ("L", -1.0))]
        ledgers.TRADE_DNA_FILE.write_text(json.dumps(trades), encoding="utf-8")
        ra.GAME_FILE.write_text(json.dumps({"history": [_tx(0, "BUY", "ZZNOBARS")]}), encoding="utf-8")
        ra.analyze(report_only=True)
        report = ledgers.RETRO_REPORT_FILE.read_text(encoding="utf-8")
        self.assertIn("  - Winning Trades: 1", report)
        self.assertIn("  - Flat Trades (0.00%, not counted as wins): 1", report)
        self.assertIn("  - Realised Win-Rate:   33.33%", report)
        self.assertIn("BEHAVIOUR REFLECTION", report)
        self.assertIn("skipped (under 10 sessions of bars): 1", report)

    def test_missing_game_ledger_is_one_line(self):
        self.assertEqual(len(ra.behaviour_section()), 1)

    def test_a_failing_reflection_is_one_line_not_a_lost_report(self):
        """PR #289 review: an exception in reflect() must not stop the weekly report."""
        path = os.path.join(os.environ["TEMP"] if "TEMP" in os.environ else ".", "_beh_ledger_test.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"history": [{"date": "2026-10-01", "type": "SELL", "symbol": "X", "price": 1.0}]}, f)
        try:
            with mock.patch.object(ra.behaviour_reflection, "reflect", side_effect=ValueError("bad qty")):
                out = ra.behaviour_section(game_file=path)
        finally:
            os.remove(path)
        self.assertEqual(out, ["BEHAVIOUR REFLECTION: failed (ValueError: bad qty)."])


class ForwardReturnEdgeTest(unittest.TestCase):
    def test_exactly_horizon_bars_from_the_end_is_none_not_an_error(self):
        days = [f"2026-01-{i + 1:02d}" for i in range(20)]
        series = (days, [100.0 + i for i in range(20)])
        self.assertIsNone(br.forward_return(series, days[10], horizon=10))      # 10 + 10 == len
        self.assertAlmostEqual(br.forward_return(series, days[9], horizon=10), (119.0 / 109.0 - 1) * 100)


class ValidationRowTest(unittest.TestCase):
    def test_combo_pattern_name_and_average(self):
        v = {"type": "combo_signal", "name": "StrongMF+PositiveChart",
             "validation": {"n": 40, "avg_return": 0.93, "lift": 0.4, "z": 2.5, "confident": True}}
        row = pd.validation_row(v)
        self.assertEqual(row["name"], "StrongMF+PositiveChart")
        self.assertEqual(row["avg_return"], 0.93)
        self.assertTrue(row["confident"])

    def test_factor_pattern_shows_its_value(self):
        v = {"type": "contrarian_reversal", "factor": "lt_trend", "value": "Weak", "validation": {"avg_return": 0.82}}
        self.assertEqual(pd.validation_row(v)["name"], "lt_trend=Weak")

    def test_email_table_uses_the_row_helper(self):
        src = open(pd.__file__, encoding="utf-8").read()
        self.assertNotIn("get('avg_fwd'", src)
        self.assertNotIn("<b>{v.get('factor')}</b>", src)


class BuyDnaSetupTest(unittest.TestCase):
    """Live buys record the Setup flag in buy_dna (it was missing, so the DNA showed Setup=None)."""

    def test_execute_buys_records_setup(self):
        state = {"balance": 10000.0, "equity": 10000.0, "positions": {}, "history": []}
        rules = {"scarcity_allocation_pct": 0.20, "max_allocation_pct": 0.50, "atr_multiplier": 2.5}
        top = [{"sym": "AAA", "price": 10.0, "total": 5.0, "industry": "Software", "bottom_desc": "", "setup": True}]
        with mock.patch.object(game, "calculate_bubble_z_score", return_value=None), \
             mock.patch.object(game, "backtrack_verify", return_value=(True, "Verified")), \
             mock.patch.object(game.instruments, "is_scarcity_asset", return_value=False), \
             mock.patch.object(game, "_log"), \
             mock.patch("risk_utils.calculate_atr", return_value=0.04):
            game._execute_buys(state, top, 1, 0.0, rules, "2026-10-06", "10:00", [], prices={"AAA": 10.0})
        self.assertIs(state["positions"]["AAA"]["buy_dna"]["setup"], True)


if __name__ == "__main__":
    unittest.main()
