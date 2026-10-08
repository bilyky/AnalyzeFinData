"""Tests for scripts/diagnostics/ohlcv_placeholder_audit.py (read-only placeholder audit)."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "diagnostics"))
import ohlcv_placeholder_audit as audit


def _real(px, vol=1000):
    return {"1. open": str(px), "2. high": str(px + 1), "3. low": str(px - 1),
            "4. close": str(px), "5. volume": str(vol)}


def _placeholder(px):
    return {"1. open": str(px), "2. high": str(px), "3. low": str(px), "4. close": str(px),
            "5. volume": "0", "provisional": True}


class TestAuditSeries(unittest.TestCase):
    def setUp(self):
        # 40 weekdays of real bars from Mon 2026-06-01, then edits below.
        days, d = [], 0
        start = datetime.date(2026, 6, 1)
        while len(days) < 40:
            day = start + datetime.timedelta(days=d)
            if day.weekday() < 5:
                days.append(day.isoformat())
            d += 1
        self.ts = {day: _real(100) for day in days}
        self.ts["2026-06-06"] = _placeholder(100)            # Saturday, interior
        self.ts[days[20]] = _placeholder(100)                # weekday, interior
        self.ts["2026-08-01"] = _placeholder(100)            # Saturday, trailing

    def test_classifies_trailing_interior_weekend(self):
        s = audit.audit_series(self.ts, "2026-01-01")
        self.assertEqual((s["trailing"], s["interior"], s["weekend"]), (1, 2, 2))
        self.assertEqual(s["by_month"]["2026-06"] + s["by_month"]["2026-07"], 2)
        self.assertEqual(s["last_date"], "2026-08-01")

    def test_since_filters_counts(self):
        s = audit.audit_series(self.ts, "2026-07-01")
        self.assertEqual((s["trailing"], s["interior"], s["weekend"]), (1, 0, 1))

    def test_stranded_placeholders_deflate_live_atr(self):
        s = audit.audit_series(self.ts, "2026-01-01")
        self.assertLess(s["atr_ratio"], 1.0)                 # flat bars shrink live ATR
        clean = {k: v for k, v in self.ts.items() if not v.get("provisional")}
        self.assertAlmostEqual(audit.audit_series(clean, "2026-01-01")["atr_ratio"], 1.0)


class TestStaleStops(unittest.TestCase):
    def test_newest_real_bar_decides_staleness_after_the_fix(self):
        today = datetime.date(2026, 10, 6)
        # Real bars end 2026-09-15 (21 d old); placeholders keep the series "current".
        lagging = {f"2026-09-{d:02d}": _real(100) for d in range(1, 16)}
        lagging.update({f"2026-10-0{d}": _placeholder(100) for d in range(1, 6)})
        fresh = {f"2026-10-0{d}": _real(100) for d in range(1, 6)}
        per_sym = {"LAG": audit.audit_series(lagging, "2026-01-01"),
                   "OK": audit.audit_series(fresh, "2026-01-01")}
        st = audit.stale_stops(per_sym, today, limit=10)
        self.assertEqual((st["before"], st["after"]), (0, 1))
        self.assertEqual(st["newly_stale"], ["LAG"])

    def test_no_real_bar_is_stale(self):
        per_sym = {"DEAD": audit.audit_series({"2026-10-01": _placeholder(1)}, "2026-01-01")}
        self.assertEqual(audit.stale_stops(per_sym, datetime.date(2026, 10, 6))["after"], 1)


if __name__ == "__main__":
    unittest.main()
