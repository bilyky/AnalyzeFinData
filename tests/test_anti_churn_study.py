"""round_trips / reentry_study in the anti-churn study: a trip closes only when the share
count returns to zero, so scale-outs and option assignments never read as a close."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "backtesting"))
import anti_churn_study as acs


def _tx(date, kind, qty, price=10.0, pnl=None, details="", sym="KE"):
    return {"date": date, "type": kind, "symbol": sym, "qty": qty, "price": price,
            "pnl": pnl, "details": details}


# KE's real shape: a losing trip, a re-entry 3 days later that pyramids, banks a
# scale-out, and ends with the rest called away by a covered call.
_KE = [
    _tx("2026-08-14", "BUY", 60, 26.72),
    _tx("2026-09-15", "SELL", 60, 24.40, pnl=-139.38),
    _tx("2026-09-18", "BUY", 40, 24.85),
    _tx("2026-09-21", "BUY_SCALE_IN", 21, 25.19),
    _tx("2026-09-23", "SELL", 18, 26.37, pnl=25.11, details="Scale-out (Bank-As-You-Go): bank 30%"),
    _tx("2026-09-23", "OPTION_WRITE", 43, 0.01, pnl=0.43),
    _tx("2026-09-25", "OPTION_ASSIGNMENT", 43, 28.00, pnl=130.29),
]


class TestRoundTrips(unittest.TestCase):
    def test_scale_out_and_assignment_do_not_split_the_trip(self):
        trips = acs.round_trips(_KE)
        self.assertEqual([(t["open_date"], t["close_date"]) for t in trips],
                         [("2026-08-14", "2026-09-15"), ("2026-09-18", "2026-09-25")])
        self.assertAlmostEqual(trips[1]["pnl"], 25.11 + 0.43 + 130.29)
        self.assertEqual(trips[1]["qty"], 0)

    def test_unclosed_trip_stays_open(self):
        trips = acs.round_trips(_KE[:5])
        self.assertIsNone(trips[-1]["close_date"])
        self.assertEqual(trips[-1]["qty"], 43)

    def test_trip_pnl_reconciles_with_ledger(self):
        self.assertAlmostEqual(sum(t["pnl"] for t in acs.round_trips(_KE)),
                               sum(t["pnl"] or 0 for t in _KE))


class TestReentry(unittest.TestCase):
    def test_reentry_uses_the_whole_round_trip_pnl(self):
        sweep = acs.reentry_study(_KE)
        self.assertEqual(sweep[5]["n_blocked"], 1)
        self.assertAlmostEqual(sweep[5]["total_pnl"], 155.83)

    def test_profitable_prior_trip_is_not_a_loss_exit(self):
        winning = [_tx("2026-08-01", "BUY", 10), _tx("2026-08-05", "SELL", 10, pnl=50.0),
                   _tx("2026-08-06", "BUY", 10)]
        self.assertEqual(acs.reentry_study(winning)[30]["n_blocked"], 0)


if __name__ == "__main__":
    unittest.main()
