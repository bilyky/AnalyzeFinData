"""R&D #14: sell_rules.ai_exit_override, the one AI second-opinion exit override.

The game's SELL loop, its scenario-stage copy and the real-account oracle all call it, so this
pins the rule once: a momentum SELL is downgraded by an AI HOLD (-> HOLD) or FLAG-FOR-REVIEW
(-> WATCH); a stop exit and any other action are never changed. Pure function, no mocks.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from aether import sell_rules  # noqa: E402

STOP = sell_rules.exit_decision(90.0, 100.0, 95.0, 1.0, 1.0)[1]   # the real stop reason
SOFT = "momentum decay (S10+L60=-3.0)"


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
        ]
        for label, action, reason, kw, (want_action, want_src) in cases:
            with self.subTest(label):
                got_action, src = sell_rules.ai_exit_override(action, reason, **kw)
                self.assertEqual(got_action, want_action)
                self.assertTrue(src.startswith(want_src) and (bool(src) == bool(want_src)), src)

    def test_stop_reason_is_what_exit_decision_returns(self):
        self.assertTrue(sell_rules.is_stop_exit(STOP))
        self.assertFalse(sell_rules.is_stop_exit(SOFT))


if __name__ == "__main__":
    unittest.main()
