"""Drop-risk portfolio study (R&D #117): variant admission, the size hook, drawdown halves and the
verdict."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import drop_risk_portfolio_study as st
import portfolio_backtest as pb
from tests.test_portfolio_backtest import DAYS, _bars


class TestAdmission(unittest.TestCase):
    def test_high_mask_threshold_and_nan(self):
        np.testing.assert_array_equal(st.high_mask([0, 3, 4, 7, np.nan]), [False, False, True, True, False])

    def test_vol_top_is_per_day_and_counts_nan_in_denominator(self):
        # Day A: 10 admitted, vols 1..10 -> only the top one (share 1/10 <= 0.19) is capped;
        # the second (2/10 = 0.2) is not. Day B: 5 admitted -> top share 1/5 = 0.2, none capped.
        # Day C: 6 rows, one NaN, one not admitted with the highest vol -> top admitted capped.
        days = ["A"] * 10 + ["B"] * 5 + ["C"] * 7
        vol = list(range(1, 11)) + [1, 2, 3, 4, 5] + [1, 2, 3, 4, 5, np.nan, 99]
        adm = np.array([True] * 21 + [False])
        top = st.vol_top_mask(days, vol, adm)
        self.assertEqual(np.where(top)[0].tolist(), [9, 19])
        self.assertFalse(top[21])                                  # not admitted -> never capped

    def test_variant_masks(self):
        adm = np.array([True, True, True, False])
        high = np.array([True, False, False, True])
        top = np.array([False, True, False, False])
        m = st.variant_masks(adm, high, top)
        np.testing.assert_array_equal(m["current"], adm)
        np.testing.assert_array_equal(m["skip_high"], [False, True, True, False])
        np.testing.assert_array_equal(m["half_high"], adm)           # same buys, smaller size
        np.testing.assert_array_equal(m["vol_cap"], [True, False, True, False])

    def test_realized_vol_uses_returns_up_to_the_signal_close(self):
        closes = [100.0] * 61 + [200.0]
        self.assertEqual(st.realized_vol(closes, 60), 0.0)           # 60 flat returns
        self.assertIsNone(st.realized_vol(closes, 59))               # only 59 returns
        self.assertGreater(st.realized_vol(closes, 61), 0.0)         # the jump is on its own day
        x = [100 * 1.01 ** k if k % 2 else 100.0 for k in range(61)]
        want = np.std(np.diff(x) / np.array(x[:-1]), ddof=1)
        self.assertAlmostEqual(st.realized_vol(x, 60), want)

    def test_half_size_fn(self):
        f = st.half_size_fn(frozenset({("A", "d1")}))
        self.assertEqual((f("A", "d1"), f("A", "d2"), f("B", "d1")), (0.5, 1.0, 1.0))


class TestSizeHook(unittest.TestCase):
    def _run(self, **kw):
        bars = {"A": _bars([10] * 4), "SPY": _bars([100] * 4)}
        panel = {"A": {DAYS[0]: [6.0, 0.0]}}
        return pb.simulate(panel, bars, DAYS[:4], cost_bps=0.0, initial=10_000.0, **kw)

    def test_default_sizing_is_unchanged(self):
        base = self._run()
        self.assertAlmostEqual(base["curve"][1][2], 0.15 * 10_000.0)  # BALANCED 15% cap
        self.assertEqual(self._run(size_fn=lambda s, d: 1.0)["curve"], base["curve"])

    def test_half_size(self):
        r = self._run(size_fn=lambda s, d: 0.5)
        self.assertAlmostEqual(r["curve"][1][2], 0.075 * 10_000.0)


class TestDrawdown(unittest.TestCase):
    def test_half_drawdowns_reset_the_peak(self):
        # First half: 100 -> 80 (-20%). Second half starts at 80, rises to 90, falls to 81 (-10%);
        # measured on the whole curve the second half would read -20% from the old peak.
        days = [f"2024-01-{d:02d}" for d in range(1, 7)]
        eq = [100, 90, 80, 80, 90, 81]
        curve = [(d, e, 0) for d, e in zip(days, eq)]
        mid = "2024-01-03"
        h1, h2 = st.half_drawdowns(curve, mid)
        self.assertAlmostEqual(h1, -20.0)
        self.assertAlmostEqual(h2, -10.0)
        self.assertAlmostEqual(st.max_drawdown_pct(eq), -20.0)

    def test_half_split_is_calendar_midpoint(self):
        self.assertEqual(st.half_split(["2024-01-01", "2024-01-02", "2024-01-31"]), "2024-01-16")

    def test_dd_diff_sign(self):
        self.assertAlmostEqual(st.dd_diff(-10.0, -15.0), 5.0)        # shallower = positive
        self.assertIsNone(st.dd_diff(None, -15.0))


def _s(dd_gain, t, halves):
    return {"dd_gain": dd_gain, "t_hac": t, "half_gains": halves}


class TestVerdict(unittest.TestCase):
    NULL_VOL = _s(0.0, 0.0, [0.0, 0.0])

    def v(self, skip, half=None, vol=None):
        return st.verdicts({"skip_high": skip, "half_high": half or _s(0.0, 0.0, [0, 0]),
                            "vol_cap": vol or self.NULL_VOL})

    def test_loss_cut(self):
        self.assertEqual(self.v(_s(3.0, -0.5, [1.0, 2.0]))["skip_high"], "LOSS_CUT")

    def test_big_dd_gain_but_return_clearly_worse(self):
        self.assertEqual(self.v(_s(6.0, -1.5, [3.0, 3.0]))["skip_high"], "INCONCLUSIVE")

    def test_t_boundary_and_dd_boundary(self):
        self.assertEqual(self.v(_s(2.0, -1.0, [0.1, 0.1]))["skip_high"], "LOSS_CUT")
        self.assertEqual(self.v(_s(1.99, 3.0, [1.0, 1.0]))["skip_high"], "INCONCLUSIVE")

    def test_one_half_fails(self):
        # Whole-window dd much better and returns better, but one half is deeper.
        self.assertEqual(self.v(_s(5.0, 2.5, [4.0, -0.2]))["skip_high"], "INCONCLUSIVE")
        self.assertEqual(self.v(_s(5.0, 2.5, [4.0, 0.0]))["skip_high"], "INCONCLUSIVE")
        self.assertEqual(self.v(_s(5.0, 2.5, [4.0, None]))["skip_high"], "INCONCLUSIVE")

    def test_good_return_but_small_dd_gain(self):
        self.assertEqual(self.v(_s(0.5, 4.0, [0.5, 0.5]))["skip_high"], "INCONCLUSIVE")

    def test_vol_explains(self):
        vol = _s(3.5, 0.0, [1.0, 1.0])
        out = self.v(_s(3.0, 0.0, [1.0, 1.0]), half=_s(4.0, 0.0, [1.0, 1.0]), vol=vol)
        self.assertEqual(out["skip_high"], "LOSS_CUT+VOL_EXPLAINS")   # vol gain 3.5 >= 3.0
        self.assertEqual(out["half_high"], "LOSS_CUT")                # vol gain 3.5 < 4.0

    def test_vol_bigger_gain_but_fails_its_own_conditions(self):
        vol = _s(5.0, -2.0, [1.0, 1.0])                                # clearly worse returns
        self.assertEqual(self.v(_s(3.0, 0.0, [1.0, 1.0]), vol=vol)["skip_high"], "LOSS_CUT")

    def test_none_stats(self):
        self.assertEqual(self.v(_s(None, 1.0, [1.0, 1.0]))["skip_high"], "INCONCLUSIVE")
        self.assertEqual(self.v(_s(3.0, None, [1.0, 1.0]))["skip_high"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
