"""
Unit tests for primal_funcs.gann_sq9_levels (Gann Square-of-Nine √-level layer).
No API calls, no external dependencies — runs with python -m unittest.

  python -m unittest tests.test_primal_funcs -v
"""
import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primal_funcs import gann_sq9_levels


class TestGannSq9Levels(unittest.TestCase):
    def _lvl(self, by_rot, deg, kind):
        return by_rot[deg][kind]

    def test_convention_b_anchor_100(self):
        # Convention B (default): 0.25 √-increment per 90°.
        by_rot, flat = gann_sq9_levels(100.0)
        self.assertAlmostEqual(self._lvl(by_rot, 90,  'resistance'), 105.0625, places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 90,  'support'),     95.0625, places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 180, 'resistance'), 110.25,   places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 180, 'support'),     90.25,   places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 270, 'resistance'), 115.5625, places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 270, 'support'),     85.5625, places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 360, 'resistance'), 121.0,    places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 360, 'support'),     81.0,    places=4)

    def test_convention_a_anchor_100(self):
        # Convention A: 0.5 √-increment per 90° (classic table).
        by_rot, _ = gann_sq9_levels(100.0, sqrt_inc_per_turn=2.0)
        self.assertAlmostEqual(self._lvl(by_rot, 90,  'resistance'), 110.25, places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 90,  'support'),     90.25, places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 180, 'resistance'), 121.0,  places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 180, 'support'),     81.0,  places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 360, 'resistance'), 144.0,  places=4)
        self.assertAlmostEqual(self._lvl(by_rot, 360, 'support'),     64.0,  places=4)

    def test_flat_sorted_ascending(self):
        _, flat = gann_sq9_levels(100.0)
        levels = [lvl for (_kind, _deg, lvl) in flat]
        self.assertEqual(levels, sorted(levels))
        # 8 rungs for 4 rotations × {support, resistance}
        self.assertEqual(len(flat), 8)

    def test_negative_root_clamped(self):
        # anchor 1.0, 360°, Convention A: root=1, k=2 → root-k=-1 → clamp to 0.
        by_rot, _ = gann_sq9_levels(1.0, sqrt_inc_per_turn=2.0)
        self.assertEqual(self._lvl(by_rot, 360, 'support'), 0.0)
        self.assertAlmostEqual(self._lvl(by_rot, 360, 'resistance'), 9.0, places=4)

    def test_nonpositive_anchor_returns_empty(self):
        self.assertEqual(gann_sq9_levels(0), ({}, []))
        self.assertEqual(gann_sq9_levels(-5), ({}, []))
        self.assertEqual(gann_sq9_levels("bad"), ({}, []))

    def test_custom_rotations(self):
        by_rot, flat = gann_sq9_levels(100.0, rotations=(180,))
        self.assertEqual(set(by_rot.keys()), {180})
        self.assertEqual(len(flat), 2)


if __name__ == "__main__":
    unittest.main()
