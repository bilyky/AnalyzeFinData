"""Unit tests for aether.option_pricing — pure Black-Scholes + realized-vol helpers.

Offline, deterministic, stdlib unittest. No network, no files, no scipy. Verifies the math
properties that the historical options-replay study relies on (put-call parity, delta bounds and
monotonicity, realized-vol on a known series, and the synthetic-chain shape/ordering).
"""
import datetime
import math
import unittest

from aether import option_pricing as op


class TestNormCdf(unittest.TestCase):
    def test_reference_values(self):
        self.assertAlmostEqual(op.norm_cdf(0.0), 0.5, places=9)
        # Symmetry: N(-x) == 1 - N(x).
        for x in (0.3, 1.0, 1.96, 2.5):
            self.assertAlmostEqual(op.norm_cdf(-x), 1.0 - op.norm_cdf(x), places=9)
        # Known tail: N(1.96) ~= 0.975.
        self.assertAlmostEqual(op.norm_cdf(1.96), 0.9750, places=3)


class TestBsPrice(unittest.TestCase):
    def setUp(self):
        self.S, self.K, self.T, self.r, self.sig = 100.0, 100.0, 0.25, 0.03, 0.20

    def test_put_call_parity(self):
        # C - P == S*e^{-qT} - K*e^{-rT}  (q = 0 here).
        c = op.bs_price(self.S, self.K, self.T, self.r, self.sig, "CALL")
        p = op.bs_price(self.S, self.K, self.T, self.r, self.sig, "PUT")
        lhs = c - p
        rhs = self.S - self.K * math.exp(-self.r * self.T)
        self.assertAlmostEqual(lhs, rhs, places=3)

    def test_parity_with_dividend_yield(self):
        q = 0.02
        c = op.bs_price(self.S, self.K, self.T, self.r, self.sig, "CALL", q=q)
        p = op.bs_price(self.S, self.K, self.T, self.r, self.sig, "PUT", q=q)
        lhs = c - p
        rhs = self.S * math.exp(-q * self.T) - self.K * math.exp(-self.r * self.T)
        self.assertAlmostEqual(lhs, rhs, places=3)

    def test_atm_sanity_band(self):
        # ATM call ~ 0.4 * S * sigma * sqrt(T) rule-of-thumb; expect a few $ for these inputs.
        c = op.bs_price(self.S, self.K, self.T, self.r, self.sig, "CALL")
        self.assertTrue(3.0 < c < 5.0, f"ATM call {c} outside sane band")

    def test_price_nonnegative(self):
        # No option (call or put, any strike) is ever worth less than zero.
        for otype in ("CALL", "PUT"):
            for K in (60.0, 80.0, 100.0, 120.0, 140.0):
                px = op.bs_price(self.S, K, self.T, self.r, self.sig, otype)
                self.assertGreaterEqual(px, 0.0, f"{otype} K={K} priced negative: {px}")

    def test_european_call_lower_bound(self):
        # No-arbitrage floor: a European call (q=0) is worth AT LEAST its discounted intrinsic,
        # max(S - K*e^{-rT}, 0). NOTE: European PUTS can legitimately trade BELOW intrinsic, so no
        # analogous put floor is asserted here (asserting one would be wrong, not just weak).
        # bs_price rounds to 4 dp (can shave up to 5e-5), so allow one rounding unit of slack.
        for K in (60.0, 80.0, 100.0, 120.0, 140.0):
            px = op.bs_price(self.S, K, self.T, self.r, self.sig, "CALL")
            lower = max(self.S - K * math.exp(-self.r * self.T), 0.0)
            self.assertGreaterEqual(px + 1e-4, lower,
                                    f"call K={K} below discounted intrinsic {lower:.4f}: {px}")

    def test_degenerate_returns_intrinsic(self):
        # T=0 -> intrinsic; sigma=0 -> intrinsic.
        self.assertAlmostEqual(op.bs_price(110, 100, 0.0, self.r, self.sig, "CALL"), 10.0, places=4)
        self.assertAlmostEqual(op.bs_price(90, 100, 0.5, self.r, 0.0, "PUT"), 10.0, places=4)
        self.assertGreaterEqual(op.bs_price(90, 100, 0.5, self.r, self.sig, "CALL", q=0.0), 0.0)

    def test_call_monotonic_in_strike(self):
        # Call price is non-increasing in strike; put non-decreasing.
        calls = [op.bs_price(self.S, K, self.T, self.r, self.sig, "CALL")
                 for K in range(80, 121, 5)]
        puts = [op.bs_price(self.S, K, self.T, self.r, self.sig, "PUT")
                for K in range(80, 121, 5)]
        self.assertEqual(calls, sorted(calls, reverse=True))
        self.assertEqual(puts, sorted(puts))


class TestBsDelta(unittest.TestCase):
    def setUp(self):
        self.S, self.T, self.r, self.sig = 100.0, 0.25, 0.03, 0.20

    def test_bounds(self):
        for K in range(60, 141, 10):
            cd = op.bs_delta(self.S, K, self.T, self.r, self.sig, "CALL")
            pd = op.bs_delta(self.S, K, self.T, self.r, self.sig, "PUT")
            self.assertTrue(0.0 <= cd <= 1.0, f"call delta {cd} out of [0,1] at K={K}")
            self.assertTrue(-1.0 <= pd <= 0.0, f"put delta {pd} out of [-1,0] at K={K}")

    def test_atm_delta_near_half(self):
        cd = op.bs_delta(self.S, 100.0, self.T, self.r, self.sig, "CALL")
        self.assertTrue(0.5 < cd < 0.58, f"ATM call delta {cd} not near 0.5")

    def test_call_delta_monotonic_decreasing_in_strike(self):
        deltas = [op.bs_delta(self.S, K, self.T, self.r, self.sig, "CALL")
                  for K in range(70, 131, 5)]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_call_put_delta_relationship(self):
        # call_delta - put_delta == e^{-qT} (= 1 at q=0).
        for K in (80.0, 100.0, 120.0):
            cd = op.bs_delta(self.S, K, self.T, self.r, self.sig, "CALL")
            pd = op.bs_delta(self.S, K, self.T, self.r, self.sig, "PUT")
            self.assertAlmostEqual(cd - pd, 1.0, places=3)

    def test_degenerate_indicator(self):
        self.assertEqual(op.bs_delta(110, 100, 0.0, self.r, self.sig, "CALL"), 1.0)
        self.assertEqual(op.bs_delta(90, 100, 0.0, self.r, self.sig, "CALL"), 0.0)
        self.assertEqual(op.bs_delta(90, 100, 0.0, self.r, self.sig, "PUT"), -1.0)


class TestRealizedVol(unittest.TestCase):
    def test_flat_series_none(self):
        self.assertIsNone(op.realized_vol([50.0] * 30))

    def test_too_short_none(self):
        self.assertIsNone(op.realized_vol([100.0, 101.0]))
        self.assertIsNone(op.realized_vol([]))

    def test_known_constant_growth_low_vol(self):
        # Perfectly constant 1%/day growth -> identical log returns -> ~zero stdev (floating-point
        # noise, not an exact 0), so realized vol is negligible.
        closes = [100.0 * (1.01 ** i) for i in range(30)]
        rv = op.realized_vol(closes)
        self.assertTrue(rv is None or rv < 1e-6, f"constant-growth vol should be ~0, got {rv}")

    def test_alternating_returns_positive(self):
        # Alternating up/down gives a real, positive annualized vol.
        closes = [100.0]
        for i in range(30):
            closes.append(closes[-1] * (1.02 if i % 2 == 0 else 1 / 1.02))
        rv = op.realized_vol(closes)
        self.assertIsNotNone(rv)
        self.assertTrue(rv > 0.0)

    def test_window_respected(self):
        # A big early jump outside the window must not affect the estimate.
        tail = [100.0 * (1.005 ** i) for i in range(25)]
        with_spike = [10.0, 200.0] + tail
        self.assertAlmostEqual(op.realized_vol(with_spike, window=21) or 0.0,
                               op.realized_vol(tail, window=21) or 0.0, places=6)


class TestSynthesizeChain(unittest.TestCase):
    def setUp(self):
        self.spot = 100.0
        self.today = datetime.date(2026, 1, 2)
        self.expiry = datetime.date(2026, 2, 6)   # ~35 days
        self.sigma = 0.25

    def test_shape_and_count(self):
        chain = op.synthesize_chain(self.spot, self.expiry, self.today, self.sigma)
        calls = [q for q in chain if q.option_type == "CALL"]
        puts = [q for q in chain if q.option_type == "PUT"]
        self.assertEqual(len(calls), len(puts))
        self.assertGreater(len(calls), 10)
        for q in chain:
            self.assertEqual(q.expiry, self.expiry)
            self.assertGreaterEqual(q.ask, q.bid)
            self.assertGreaterEqual(q.bid, 0.0)

    def test_strikes_on_grid(self):
        chain = op.synthesize_chain(self.spot, self.expiry, self.today, self.sigma)
        for q in chain:
            # Every strike sits on the $2.50 ladder.
            self.assertAlmostEqual(q.strike % op.DEFAULT_STRIKE_STEP, 0.0, places=6)

    def test_delta_populated_and_bounded(self):
        chain = op.synthesize_chain(self.spot, self.expiry, self.today, self.sigma)
        for q in chain:
            self.assertIsNotNone(q.delta)
            if q.option_type == "CALL":
                self.assertTrue(0.0 <= q.delta <= 1.0)
            else:
                self.assertTrue(-1.0 <= q.delta <= 0.0)

    def test_call_price_monotonic_in_strike(self):
        chain = op.synthesize_chain(self.spot, self.expiry, self.today, self.sigma)
        calls = sorted((q for q in chain if q.option_type == "CALL"), key=lambda q: q.strike)
        mids = [q.last for q in calls]
        self.assertEqual(mids, sorted(mids, reverse=True))

    def test_vrp_mult_raises_premiums(self):
        base = op.synthesize_chain(self.spot, self.expiry, self.today, self.sigma)
        stressed = op.synthesize_chain(self.spot, self.expiry, self.today, self.sigma, vrp_mult=1.3)
        atm_base = min((q for q in base if q.option_type == "CALL"),
                       key=lambda q: abs(q.strike - self.spot))
        atm_str = min((q for q in stressed if q.option_type == "CALL"),
                      key=lambda q: abs(q.strike - self.spot))
        self.assertGreater(atm_str.last, atm_base.last)

    def test_degenerate_inputs_empty(self):
        self.assertEqual(op.synthesize_chain(0.0, self.expiry, self.today, self.sigma), [])
        self.assertEqual(op.synthesize_chain(self.spot, self.expiry, self.today, 0.0), [])
        # Expiry on/before today -> T<=0 -> empty.
        self.assertEqual(op.synthesize_chain(self.spot, self.today, self.today, self.sigma), [])


if __name__ == "__main__":
    unittest.main()
