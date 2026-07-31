"""
Red-green tests for the Rubber-Band Reversal (RBR) detector
(aether/patterns.py :: rubber_band_reversal_score) and its scoring wiring
(aether/scoring.py :: short_score / long_score).

The pattern: a big, fast overreaction drawdown followed by a confirmed higher-low
green reversal bar. Validated in scripts/backtesting/pullback_recovery_study.py
(reliable pocket: depth <= -15%, speed <= 5, strong close, no gap). Anchor case is
INTC's Jul-2026 earnings gap — a deep + fast + GAP setup. That gap cohort did NOT
clear the gate on its own, so it is WATCH-ONLY: detected and tagged for the monitor,
but scores 0.0 (no buy weight) until it clears its own gate.

Pure price/volume, NO look-ahead — the last test proves future bars are ignored.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import patterns
from aether import scoring


def _series(overrides, n=40):
    """Build an n-bar OHLCV dict. Default flat bar = 100; `overrides` maps a bar
    index to a dict of any of o/h/l/c/v to set. Keys sort lexicographically."""
    ts = {}
    for i in range(n):
        o = h = l = c = 100.0
        h += 0.5
        l -= 0.5
        b = overrides.get(i, {})
        o = b.get("o", o); h = b.get("h", h); l = b.get("l", l)
        c = b.get("c", c); vv = b.get("v", 10000)
        ts[f"2026-01-{i + 1:02d}"] = {
            "1. open": str(o), "2. high": str(h), "3. low": str(l),
            "4. close": str(c), "5. volume": str(vv),
        }
    return ts


# A clean reliable-pocket drop: pre-high 105 @ idx32, trough low 88 @ idx36
# (-16.2%, 4 bars, no gap), confirmed higher-low green strong-close bar @ idx39.
_POCKET = {
    32: {"o": 104, "h": 105.5, "l": 103, "c": 105},   # pre-high (swing high close)
    33: {"o": 105, "h": 105, "l": 99.5, "c": 100},
    34: {"o": 100, "h": 100, "l": 94.5, "c": 95},
    35: {"o": 95, "h": 95, "l": 89.5, "c": 90},
    36: {"o": 90, "h": 90, "l": 88, "c": 89},          # trough (low 88)
    37: {"o": 89, "h": 91, "l": 89, "c": 90},          # recovering
    38: {"o": 90, "h": 92, "l": 90, "c": 91},
    39: {"o": 91, "h": 95, "l": 91, "c": 94.5},        # confirmed: HL 91>88, green, top of range
}
_LAST = "2026-01-40"   # date_str of bar index 39


class RBRDetectorTest(unittest.TestCase):
    def test_reliable_pocket_fires_full_score(self):
        # GREEN: deep(-16%) + fast(4 bars) + strong close + no gap => the +2.0 pocket.
        score, names = patterns.rubber_band_reversal_score(_series(_POCKET), _LAST)
        self.assertEqual(score, 2.0)
        self.assertIn("RBR↑", names)

    def test_no_recovery_bar_does_not_fire(self):
        # RED: still falling — the trough is TODAY, so there is no confirmation yet.
        knife = dict(_POCKET)
        knife[37] = {"o": 89, "h": 90, "l": 86, "c": 87}
        knife[38] = {"o": 87, "h": 88, "l": 84, "c": 85}
        knife[39] = {"o": 85, "h": 85, "l": 82, "c": 83}   # new low today => t_idx == i
        score, names = patterns.rubber_band_reversal_score(_series(knife), _LAST)
        self.assertEqual(score, 0.0)
        self.assertEqual(names, [])

    def test_weak_close_does_not_fire(self):
        # RED: higher low + green, but the close sits in the LOWER half of the range
        # (range_pos 0.14 < 0.50) — a limp bounce, not a real reversal.
        weak = dict(_POCKET)
        weak[39] = {"o": 91, "h": 98, "l": 91, "c": 92}
        score, _ = patterns.rubber_band_reversal_score(_series(weak), _LAST)
        self.assertEqual(score, 0.0)

    def test_shallow_drawdown_does_not_fire(self):
        # RED: only a ~5% dip — below the -10% overreaction floor. No RBR.
        shallow = {
            32: {"o": 100, "h": 101, "l": 100, "c": 101},
            36: {"o": 100, "h": 100, "l": 96, "c": 96.5},   # ~-4.5% trough
            39: {"o": 97, "h": 99, "l": 97, "c": 98.5},
        }
        score, _ = patterns.rubber_band_reversal_score(_series(shallow), _LAST)
        self.assertEqual(score, 0.0)

    def test_gap_cohort_is_watch_only(self):
        # The INTC signature: deep(-17%) + near-fast(6 bars) + an overnight GAP-down.
        # High mean but lower reliability (win ~0.52), and it never cleared its own
        # gate => WATCH-ONLY: detected + tagged 'RBR↑(gap,watch)' for the monitor, but
        # it scores 0.0 so it adds NO buy weight.
        gap = {
            31: {"o": 104, "h": 105.5, "l": 103, "c": 105},   # pre-high
            32: {"o": 102, "h": 102, "l": 100.5, "c": 101},
            33: {"o": 97, "h": 98, "l": 95, "c": 96},         # gap-down: 97 < 101*0.97
            34: {"o": 96, "h": 96, "l": 93, "c": 94},
            35: {"o": 94, "h": 94, "l": 91, "c": 92},
            36: {"o": 92, "h": 92, "l": 89, "c": 90},
            37: {"o": 90, "h": 90, "l": 87, "c": 88},         # trough (low 87), speed 6
            38: {"o": 88, "h": 90, "l": 88, "c": 89},
            39: {"o": 89, "h": 94, "l": 89, "c": 93.5},       # confirmed, strong close
        }
        score, names = patterns.rubber_band_reversal_score(_series(gap), _LAST)
        self.assertEqual(score, 0.0)                          # no buy weight
        self.assertEqual(names, ["RBR↑(gap,watch)"])          # but still detected + tagged

    def test_near_fast_no_gap_scores_reduced(self):
        # GREEN: deep(-17%) but speed 6 (one slower than the pocket) and NO gap => the
        # near-fast +1.25 tier. Same geometry as the gap case with the gap-down removed,
        # proving the had_gap discriminator is what demotes the cohort, not the speed.
        near = {
            31: {"o": 104, "h": 105.5, "l": 103, "c": 105},   # pre-high
            32: {"o": 105, "h": 105, "l": 100.5, "c": 101},   # no gap (105 !< 101.85)
            33: {"o": 101, "h": 101, "l": 97.5, "c": 98},     # no gap (101 !< 97.97)
            34: {"o": 98, "h": 98, "l": 94.5, "c": 95},
            35: {"o": 95, "h": 95, "l": 91.5, "c": 92},
            36: {"o": 92, "h": 92, "l": 88.5, "c": 89},
            37: {"o": 89, "h": 89, "l": 87, "c": 88},         # trough (low 87), speed 6
            38: {"o": 88, "h": 90, "l": 88, "c": 89},
            39: {"o": 89, "h": 94, "l": 89, "c": 93.5},       # confirmed, strong close
        }
        score, names = patterns.rubber_band_reversal_score(_series(near), _LAST)
        self.assertEqual(score, 1.25)
        self.assertEqual(names, ["RBR↑"])

    def test_no_look_ahead(self):
        # The score on the confirmation bar must NOT change when future bars exist.
        # Append 5 wild future bars; querying the confirmation date must still be +2.0.
        future = dict(_POCKET)
        for j, cl in zip(range(40, 45), (50, 40, 30, 20, 10)):   # a post-signal crash
            future[j] = {"o": cl, "h": cl + 1, "l": cl - 1, "c": cl}
        score, _ = patterns.rubber_band_reversal_score(_series(future, n=45), _LAST)
        self.assertEqual(score, 2.0)

    def test_insufficient_history_returns_zero(self):
        score, names = patterns.rubber_band_reversal_score(_series(_POCKET, n=20), "2026-01-20")
        self.assertEqual((score, names), (0.0, []))


class RBRScoringWiringTest(unittest.TestCase):
    """The factor must feed short/long score with a POSITIVE weight (bullish),
    never the contrarian pattern path — and must be a no-op when absent (toggleable)."""

    def test_short_score_adds_positive_contribution(self):
        base = scoring.short_score({})
        with_rbr = scoring.short_score({"vrecovery_score": 2.0})
        self.assertAlmostEqual(with_rbr - base, 2.0 * 0.5, places=6)   # +1.0

    def test_long_score_adds_positive_contribution(self):
        base = scoring.long_score({})
        with_rbr = scoring.long_score({"vrecovery_score": 2.0})
        self.assertAlmostEqual(with_rbr - base, 2.0 * 0.25, places=6)  # +0.5

    def test_absent_field_is_noop(self):
        # Backward compatibility: a fields dict with no vrecovery_score is unchanged.
        self.assertEqual(scoring.short_score({"money_flow": "Strong"}),
                         scoring.short_score({"money_flow": "Strong", "vrecovery_score": 0.0}))


if __name__ == "__main__":
    unittest.main()
