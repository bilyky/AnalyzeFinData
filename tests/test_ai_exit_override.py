"""R&D #14: sell_rules.ai_exit_override, the one AI second-opinion exit override.

The game's SELL loop, its scenario-stage copy and the real-account oracle all call it, so this
pins the rule once: a momentum SELL of a position safely in profit (> 1 ATR above cost) is
downgraded by an AI HOLD (-> HOLD) or FLAG-FOR-REVIEW (-> WATCH); a stop exit, a loser, a
marginal gain and any other action are never changed. Pure function, no mocks.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether import sell_rules  # noqa: E402

STOP = sell_rules.exit_decision(90.0, 100.0, 95.0, 1.0, 1.0)[1]   # the real stop reason
SOFT = "momentum decay (S10+L60=-3.0)"
WIN = {"price": 110.0, "cost": 100.0, "atr": 5.0}   # +2 ATR: safely in profit


class TestAiExitOverride(unittest.TestCase):
    def test_table(self):
        cases = [
            # (label, action, reason, kwargs, expected (action, source-prefix))
            ("no verdict keeps the sell", "SELL", SOFT, {}, ("SELL", "")),
            ("live HOLD -> HOLD", "SELL", SOFT, {"live_verdicts": {"p": {"verdict": "hold", "note": "n"}}},
             ("HOLD", "Real-time AI Shadow Heuristic (p) returned HOLD: n")),
            ("live FLAG -> WATCH", "SELL", SOFT, {"live_verdicts": {"p": "FLAG-FOR-REVIEW"}},
             ("WATCH", "Real-time AI Shadow Heuristic (p) returned FLAG-FOR-REVIEW")),
            ("live SELL verdict doesn't override", "SELL", SOFT, {"live_verdicts": {"p": "SELL"}}, ("SELL", "")),
            ("stored key", "SELL", SOFT, {"holding": {"ai_verdict": {"verdict": "HOLD"}}},
             ("HOLD", "Stored position ai_verdict returned HOLD")),
            ("stored verdicts dict", "SELL", SOFT, {"holding": {"verdicts": {"q": "FLAG-FOR-REVIEW"}}},
             ("WATCH", "Stored position verdicts (q) returned FLAG-FOR-REVIEW")),
            ("live wins over stored", "SELL", SOFT,
             {"live_verdicts": {"p": "FLAG-FOR-REVIEW"}, "holding": {"shadow_verdict": "HOLD"}},
             ("WATCH", "Real-time AI Shadow Heuristic (p)")),
            ("stored key wins over stored dict", "SELL", SOFT,
             {"holding": {"verdict": "HOLD", "verdicts": {"q": "FLAG-FOR-REVIEW"}}},
             ("HOLD", "Stored position verdict returned HOLD")),
            ("a non-overriding key falls through", "SELL", SOFT,
             {"holding": {"shadow_verdict": "SELL", "ai_verdict": "HOLD"}},
             ("HOLD", "Stored position ai_verdict returned HOLD")),
            # The P0: a stop exit always executes, from any verdict source.
            ("stop + live HOLD", "SELL", STOP, {"live_verdicts": {"p": "HOLD"}}, ("SELL", "")),
            ("stop + stored HOLD", "SELL", STOP, {"holding": {"shadow_verdict": "HOLD"}}, ("SELL", "")),
            ("caller-flagged stop (oracle)", "SELL", "CRITICAL STOP BREACH: ...",
             {"holding": {"shadow_verdict": "HOLD"}, "is_stop": True}, ("SELL", "")),
            ("caller says not a stop", "SELL", "MOMENTUM COLLAPSE: ...",
             {"holding": {"shadow_verdict": "HOLD"}, "is_stop": False}, ("HOLD", "Stored position shadow_verdict")),
            ("REVIEW is never touched", "REVIEW", "soft exit … review", {"live_verdicts": {"p": "HOLD"}}, ("REVIEW", "")),
            ("HOLD is never touched", "HOLD", "", {"live_verdicts": {"p": "FLAG-FOR-REVIEW"}}, ("HOLD", "")),
            # Winners only: a loser or a marginal gain sells, whatever the AI says.
            ("loser sells", "SELL", SOFT, {"price": 95.0, "live_verdicts": {"p": "HOLD"}}, ("SELL", "")),
            ("+0.5 ATR sells", "SELL", SOFT, {"price": 102.5, "live_verdicts": {"p": "HOLD"}}, ("SELL", "")),
            ("exactly 1 ATR sells", "SELL", SOFT, {"price": 105.0, "live_verdicts": {"p": "HOLD"}}, ("SELL", "")),
            ("just over 1 ATR holds", "SELL", SOFT, {"price": 105.01, "live_verdicts": {"p": "HOLD"}}, ("HOLD", "Real-time")),
            ("unknown ATR sells", "SELL", SOFT, {"atr": None, "live_verdicts": {"p": "HOLD"}}, ("SELL", "")),
            # Unknown cost (None, or 0 from an empty Oracle sheet cell) is not safely in profit.
            ("unknown cost sells", "SELL", SOFT, {"cost": None, "live_verdicts": {"p": "HOLD"}}, ("SELL", "")),
            ("zero cost sells", "SELL", SOFT, {"cost": 0.0, "live_verdicts": {"p": "HOLD"}}, ("SELL", "")),
        ]
        for label, action, reason, kw, (want_action, want_src) in cases:
            with self.subTest(label):
                got_action, src, _ = sell_rules.ai_exit_override(action, reason, **{**WIN, **kw})
                self.assertEqual(got_action, want_action)
                self.assertTrue(src.startswith(want_src) and (bool(src) == bool(want_src)), src)

    def test_ignored_reason_says_why(self):
        _, _, why = sell_rules.ai_exit_override("SELL", STOP, **WIN, live_verdicts={"p": "HOLD"})
        self.assertIn("stop exits are never overridden", why)
        _, _, why = sell_rules.ai_exit_override("SELL", SOFT, price=102.5, cost=100.0, atr=5.0,
                                                live_verdicts={"p": "HOLD"})
        self.assertIn("not safely in profit (+0.50 ATR vs cost", why)
        _, _, why = sell_rules.ai_exit_override("SELL", SOFT, **WIN, live_verdicts={"p": "SELL"})
        self.assertEqual(why, "")   # no overriding verdict: nothing was ignored

    def test_ratchet_and_override_share_the_threshold(self):
        self.assertFalse(sell_rules.safely_in_profit(105.0, 100.0, 5.0))
        self.assertTrue(sell_rules.safely_in_profit(105.01, 100.0, 5.0))
        self.assertFalse(sell_rules.safely_in_profit(200.0, 100.0, None))
        self.assertFalse(sell_rules.safely_in_profit(200.0, None, 5.0))
        self.assertFalse(sell_rules.safely_in_profit(200.0, 0.0, 5.0))

    def test_unknown_cost_is_logged_not_raised(self):
        _, _, why = sell_rules.ai_exit_override("SELL", SOFT, price=110.0, cost=None, atr=5.0,
                                                live_verdicts={"p": "HOLD"})
        self.assertIn("unknown price or cost", why)

    def test_stop_reason_is_what_exit_decision_returns(self):
        self.assertTrue(sell_rules.is_stop_exit(STOP))
        self.assertFalse(sell_rules.is_stop_exit(SOFT))


if __name__ == "__main__":
    unittest.main()
