"""Tests for scripts/backtesting/stretch_regime_study.py (R&D #89): the event rule (RSI and the
symbol's own d20 percentile, spacing), outcomes from the next close (no shared price), the lower-low
flag, the features, the verdict functions (H1/H2/H3) and the month-block bootstrap."""
import datetime
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import stretch_regime_study as rs  # noqa: E402


def bars(closes, start=datetime.date(2015, 1, 1), lows=None, highs=None, opens=None, vols=None):
    days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(len(closes))]
    c = list(map(float, closes))
    return {"days": days, "idx": {d: i for i, d in enumerate(days)}, "c": c,
            "l": list(lows) if lows is not None else [x * 0.99 for x in c],
            "h": list(highs) if highs is not None else [x * 1.01 for x in c],
            "o": list(opens) if opens is not None else c[:], "v": list(vols) if vols is not None else [1000.0] * len(c)}


def wiggle(n, seed=1):
    r = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(r.normal(0.0005, 0.01, n))))


class TestEvents(unittest.TestCase):
    def test_a_sharp_drop_after_long_history_is_an_event(self):
        c = wiggle(800)
        drop = [c[-1] * (1 - 0.03 * k) for k in range(1, 11)]
        ev = rs.find_events(np.array(c + drop))
        self.assertTrue([j for j in ev if j >= 800])                         # the drop is caught
        self.assertTrue(all(j >= 504 for j in ev))                           # never before the history exists

    def test_needs_history(self):
        c = wiggle(300)
        drop = [c[-1] * (1 - 0.03 * k) for k in range(1, 11)]
        self.assertEqual(rs.find_events(np.array(c + drop)), [])             # < 504 prior d20 values

    def test_events_spaced(self):
        c = wiggle(800)
        drop = [c[-1] * (1 - 0.02 * k) for k in range(1, 30)]
        ev = rs.find_events(np.array(c + drop))
        self.assertTrue(all(b - a >= rs.GAP for a, b in zip(ev, ev[1:])))

    def test_rsi_simple(self):
        self.assertEqual(rs.rsi_simple(np.arange(1.0, 20.0))[-1], 100.0)
        self.assertEqual(rs.rsi_simple(np.arange(20.0, 1.0, -1))[-1], 0.0)
        self.assertTrue(np.isnan(rs.rsi_simple(np.arange(1.0, 20.0))[13]))

    def test_prior_mean_excludes_today(self):
        pm = rs.prior_mean(np.array([1.0, 2.0, 3.0, 100.0]), 3)
        self.assertEqual(pm[3], 2.0)


class TestOutcomes(unittest.TestCase):
    def test_from_next_close_and_lower_low(self):
        c = [100.0] * 5 + [90.0, 80.0] + [88.0] * 25
        b = bars(c, start=datetime.date(2015, 1, 1))
        spy = bars([100.0] * len(c))
        o = rs.outcomes(b, 5, {"2010-2022": 0.01}, spy)
        self.assertAlmostEqual(o["r20"], (88.0 / 80.0 - 1) * 100)            # from close[6], not close[5]
        self.assertAlmostEqual(o["ex20"], (88.0 / 80.0 - 1 - 0.01) * 100)
        self.assertAlmostEqual(o["spy20"], (88.0 / 80.0 - 1) * 100)
        self.assertEqual(o["ll"], 1.0)                                       # 80*0.99 < 90*0.99
        self.assertAlmostEqual(o["mae"], (80 * 0.99 / 90 - 1) * 100)

    def test_no_lower_low(self):
        c = [100.0] * 5 + [90.0] + [95.0] * 25
        o = rs.outcomes(bars(c), 5, {}, bars([100.0] * len(c)))
        self.assertEqual(o["ll"], 0.0)
        self.assertAlmostEqual(o["mae"], (95 * 0.99 / 90 - 1) * 100)          # window starts the next day
        self.assertIsNone(o["ex20"])

    def test_too_short(self):
        self.assertIsNone(rs.outcomes(bars([100.0] * 20), 5, {}, bars([100.0] * 20)))

    def test_era_of(self):
        self.assertEqual(rs.era_of("2009-12-31"), "2000-2009")
        self.assertEqual(rs.era_of("2010-01-04"), "2010-2022")
        self.assertEqual(rs.era_of("2026-11-16"), "2023+")
        self.assertIsNone(rs.era_of("1999-12-31"))


class TestFeatures(unittest.TestCase):
    def test_values(self):
        c = [100.0] * 260 + [99.0, 98.0, 97.0]
        lows = [x - 1 for x in c]
        highs = [x + 3 for x in c]
        opens = c[:-1] + [96.0]
        vols = [100.0] * 262 + [300.0]
        b = bars(c, lows=lows, highs=highs, opens=opens, vols=vols)
        f = rs.features(b, len(c) - 1, {b["days"][-1]: 0.0})
        self.assertAlmostEqual(f["close_loc"], 0.25)
        self.assertAlmostEqual(f["vol_ratio"], 3.0)
        self.assertAlmostEqual(f["gap"], 96.0 / 98.0 - 1)
        self.assertEqual(f["down_streak"], 3.0)
        self.assertEqual(f["above200"], 0.0)
        self.assertEqual(f["spy_above200"], 0.0)
        self.assertAlmostEqual(f["dd252"], 0.97 - 1)


class TestVerdicts(unittest.TestCase):
    def cell(self, m, t, months=60, events=500):
        return {"month_mean": m, "t_hac": t, "months": months, "events": events}

    def test_h1(self):
        self.assertEqual(rs.h1_verdict({"2010-2022": self.cell(1.0, 2.5), "2023+": self.cell(0.5, 0.3)}),
                         "SNAPBACK_RECENT")
        self.assertEqual(rs.h1_verdict({"2010-2022": self.cell(1.0, 2.5), "2023+": self.cell(-0.5, -0.3)}),
                         "INCONCLUSIVE")                                      # 2023+ sign disagrees
        self.assertEqual(rs.h1_verdict({"2010-2022": self.cell(1.0, 1.5), "2023+": self.cell(0.5, 3.0)}),
                         "INCONCLUSIVE")                                      # 2023+ alone never decides
        self.assertEqual(rs.h1_verdict({"2010-2022": self.cell(-1.0, -2.5), "2023+": self.cell(0.5, 0.3)}),
                         "NO_SNAPBACK")
        self.assertEqual(rs.h1_verdict({"2010-2022": self.cell(1.0, 2.5, months=10), "2023+": self.cell(1, 1)}),
                         "INCONCLUSIVE")

    def test_h2(self):
        r = np.random.default_rng(3)

        def n(mu, k=40):
            return list(r.normal(mu, 1.0, k))
        era = {"early_above": n(0), "late_above": n(2), "early_below": n(0), "late_below": n(2),
               "above": n(1), "below": n(1)}
        self.assertEqual(rs.h2_verdict(era)["verdict"], "ERA_EFFECT")
        regime = {"early_above": n(2), "late_above": n(2), "early_below": n(0), "late_below": n(0),
                  "above": n(2), "below": n(0)}
        self.assertEqual(rs.h2_verdict(regime)["verdict"], "REGIME_NOT_ERA")
        small = dict(era, late_below=n(2, k=5))
        self.assertEqual(rs.h2_verdict(small)["verdict"], "INSUFFICIENT")
        mixed = dict(era, late_below=n(0))
        self.assertEqual(rs.h2_verdict(mixed)["verdict"], "INCONCLUSIVE")
        flat = {k: [0.0, 1.0] * 20 for k in era}                          # no era and no regime difference
        self.assertEqual(rs.h2_verdict(flat)["verdict"], "INCONCLUSIVE")

    def test_h2_cells_split(self):
        ev = [{"date": "2005-01-03", "era": "2000-2009", "spy_above200": 1.0, "ex20": 1.0},
              {"date": "2015-01-05", "era": "2010-2022", "spy_above200": 0.0, "ex20": 2.0},
              {"date": "2024-01-05", "era": "2023+", "spy_above200": 0.0, "ex20": 4.0},
              {"date": "2024-02-05", "era": "2023+", "spy_above200": None, "ex20": 9.0}]
        cells = rs.h2_cells(ev, (-100, 100))
        self.assertEqual(cells["early_above"], [1.0])
        self.assertEqual(sorted(cells["late_below"]), [2.0, 4.0])
        self.assertEqual(cells["above"], [1.0])
        self.assertEqual(len(cells["below"]), 2)

    def test_h3_needs_train_discovery_and_same_sign_test(self):
        def st(diff, p_two, p_pos=None, p_neg=None):
            return {"diff": diff, "p_two": p_two, "p_pos": p_pos, "p_neg": p_neg}
        res = {("etfs", "close_loc"): {"train": st(-0.2, 0.0001), "test": st(-0.1, 0.01, p_pos=0.99, p_neg=0.01)},
               ("etfs", "gap"): {"train": st(0.2, 0.0001), "test": st(-0.1, 0.01, p_pos=0.99, p_neg=0.01)},
               ("stocks", "r5"): {"train": st(0.2, 0.6), "test": st(0.3, 0.001, p_pos=0.0005, p_neg=1.0)}}
        out = rs.h3_evaluate(res)
        self.assertEqual(out["confirmed"], ["etfs/close_loc"])               # gap flips sign; r5 not a train discovery
        self.assertEqual(out["verdicts"]["etfs"], {"verdict": "PREDICTORS_CONFIRMED", "features": ["close_loc"]})
        self.assertEqual(out["verdicts"]["stocks"]["verdict"], "NONE_CONFIRMED")


class TestBootstrap(unittest.TestCase):
    def test_diff_and_p(self):
        ev = []
        for m in range(1, 13):
            for k in range(10):
                ev.append({"date": f"2010-{m:02d}-01", "f": 1.0, "ll": 1.0})        # top: always a lower low
                ev.append({"date": f"2010-{m:02d}-02", "f": 0.0, "ll": float(k < 5)})  # bottom: half
        rs.BINARY.add("f")
        try:
            out = rs.ll_diff_boot(ev, "f", None, np.random.default_rng(0))
        finally:
            rs.BINARY.discard("f")
        self.assertAlmostEqual(out["diff"], 0.5)
        self.assertEqual(out["p_pos"], 0.0)
        self.assertEqual(out["p_two"], 0.0)

    def test_terciles(self):
        ev = [{"date": "2010-01-01", "x": float(i), "ll": float(i < 30)} for i in range(90)]
        cuts = rs.tercile_cuts(ev, "x")
        self.assertEqual(rs.side({"x": 0.0}, "x", cuts), "bottom")
        self.assertEqual(rs.side({"x": 89.0}, "x", cuts), "top")
        self.assertIsNone(rs.side({"x": 45.0}, "x", cuts))
        out = rs.ll_diff_boot(ev, "x", cuts, np.random.default_rng(0))
        self.assertLess(out["diff"], 0)


if __name__ == "__main__":
    unittest.main()
