"""R&D #130 breaker stop tightening: the two stop policies, the per-episode measures and the verdict."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import breaker_stop_study as bs  # noqa: E402

ATR = [2.0] * 21


def _approx(case, got, want):
    case.assertAlmostEqual(got[0], want[0])
    case.assertEqual(got[1], want[1])


class TestSimulate(unittest.TestCase):
    def test_no_breaker_policies_agree(self):
        c = [100.0] + [99.0] * 20
        r = bs.simulate(c, ATR, [False] * 21, 2.5)
        self.assertEqual(r["ORIG"], r["TIGHT"])
        self.assertAlmostEqual(r["ORIG"][0], -0.01)

    def test_tightened_stop_sells_a_dip_the_original_keeps(self):
        # S0 = 100 - 5 = 95; tight on day 0 = 98; day 1 closes 97 -> TIGHT out, ORIG holds to 104
        c = [100.0, 97.0] + [104.0] * 19
        r = bs.simulate(c, ATR, [True] * 21, 2.5)
        _approx(self, r["TIGHT"], (-0.03, 1))
        self.assertEqual(r["ORIG"][1], None)
        self.assertAlmostEqual(r["ORIG"][0], 0.04)

    def test_tightened_stop_cuts_a_crash(self):
        c = [100.0, 98.5, 85.0] + [80.0] * 18            # tight 98 from day 0; day 2 at 85 is below both stops
        r = bs.simulate(c, ATR, [True] * 21, 2.5)
        self.assertEqual((r["TIGHT"][1], r["ORIG"][1]), (2, 2))
        c = [100.0, 98.5, 96.0] + [80.0] * 18            # day 2: 96 <= tight 98 but > S0 95
        r = bs.simulate(c, ATR, [True] * 21, 2.5)
        _approx(self, r["TIGHT"], (-0.04, 2))
        self.assertEqual(r["ORIG"][1], 3)

    def test_breaker_off_restores_the_original_stop(self):
        # tight 98 on day 0, breaker off from day 1 -> stop back to 95; day 2 closes 96: no exit
        c = [100.0, 99.0, 96.0] + [99.0] * 18
        flags = [True] + [False] * 20
        r = bs.simulate(c, ATR, flags, 2.5)
        self.assertIsNone(r["TIGHT"][1])
        self.assertAlmostEqual(r["TIGHT"][0], -0.01)

    def test_tight_stop_is_never_lowered_and_rises_with_price(self):
        # day 1 at 106 with the breaker on: tight -> 104; day 2 closes 103.5 -> out at +3.5%
        c = [100.0, 106.0, 103.5] + [100.0] * 18
        r = bs.simulate(c, ATR, [True] * 21, 2.5)
        _approx(self, r["TIGHT"], (0.035, 2))
        c = [100.0, 99.0, 98.5] + [100.0] * 18           # 99 - 2 = 97 < 98: stays 98; 98.5 > 98 holds
        self.assertIsNone(bs.simulate(c, ATR, [True] * 21, 2.5)["TIGHT"][1])
        c = [100.0, 99.0, 97.5] + [100.0] * 18           # day 1 would lower it to 97; kept at 98, so 97.5 exits
        _approx(self, bs.simulate(c, ATR, [True] * 21, 2.5)["TIGHT"], (-0.025, 2))


def _stats(a_t, a_halves, b_t, b_halves):
    return {"A": {"t": a_t, "first_half_mean": a_halves[0], "second_half_mean": a_halves[1]},
            "B": {"t": b_t, "first_half_mean": b_halves[0], "second_half_mean": b_halves[1]}}


class TestVerdict(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(bs.verdict(_stats(-0.5, (-1, 1), -3, (-1, -2))), "KEEP_TIGHTENING")
        self.assertEqual(bs.verdict(_stats(-3, (-1, -2), 0.5, (1, -1))), "DROP_TIGHTENING")
        self.assertEqual(bs.verdict(_stats(-3, (-1, -2), -3, (-1, -2))), "TRADE_OFF")
        self.assertEqual(bs.verdict(_stats(2.5, (1, 2), 0, (0, 0))), "KEEP_TIGHTENING")
        self.assertEqual(bs.verdict(_stats(0.5, (1, -1), 0.5, (1, -1))), "INCONCLUSIVE")

    def test_halves_must_agree(self):
        self.assertEqual(bs.verdict(_stats(-3, (-1, 0.2), 0.5, (1, -1))), "INCONCLUSIVE")
        self.assertEqual(bs.verdict(_stats(-3, (-1, -2), -3, (-1, None))), "DROP_TIGHTENING")


class TestEpisode(unittest.TestCase):
    def test_run_episode_means(self):
        days = [f"d{i:02d}" for i in range(25)]
        flat = {"idx": {d: i for i, d in enumerate(days)}, "c": [100.0] * 25, "atr": [2.0] * 25}
        dip = {"idx": {d: i for i, d in enumerate(days)}, "c": [100.0, 97.0] + [104.0] * 23, "atr": [2.0] * 25}
        e = bs.run_episode(0, days, [True] * 25, {"FLAT": flat, "DIP": dip}, 2.5)
        self.assertEqual(e["stocks"], 2)
        self.assertAlmostEqual(e["A"], (0.0 + (-0.03 - 0.04)) / 2)
        self.assertEqual((e["stopped_tight"], e["stopped_orig"], e["given_up_n"]), (1, 0, 1))
        self.assertAlmostEqual(e["given_up_mean"], 104.0 / 97.0 - 1)


if __name__ == "__main__":
    unittest.main()
