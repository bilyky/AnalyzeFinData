"""Circuit breaker: one-day move and 10-day drawdown measured against the right session (fix 2026-11-24)."""
import datetime
import unittest
from unittest import mock

from aether import circuit_breaker as cb


def _series(closes, last_date):
    """Dated bars ending on ``last_date`` (business days, oldest first)."""
    days, d = [], last_date
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    days.reverse()
    return [{"date": x, "close": c, "open": c, "high": c, "low": c} for x, c in zip(days, closes)]


class TestMeasures(unittest.TestCase):
    def test_a_morning_run_compares_the_live_price_with_yesterdays_close(self):
        # 2026-11-24: cache to Mon 722.29, Fri 689.81; pre-market 714.46.
        closes = [759.81, 754.37, 739.95, 732.44, 730.20, 715.87, 689.81, 722.29]
        day, dd = cb.breaker_measures(closes, 714.46, last_is_today=False)
        self.assertAlmostEqual(day, (714.46 / 722.29 - 1) * 100)            # -1.08%, not +3.6%
        self.assertAlmostEqual(dd, (714.46 / 759.81 - 1) * 100)
        old_day, _ = cb.breaker_measures(closes, 714.46, last_is_today=True)  # the old reading
        self.assertGreater(old_day, 3)

    def test_the_window_keeps_yesterday_when_live_is_today(self):
        closes = [100.0] * 9 + [110.0]                                       # yesterday's close is the peak
        _, dd = cb.breaker_measures(closes, 104.0, last_is_today=False)
        self.assertAlmostEqual(dd, (104 / 110 - 1) * 100)
        _, dd_old = cb.breaker_measures(closes, 104.0, last_is_today=True)    # yesterday dropped out
        self.assertAlmostEqual(dd_old, 0.0)

    def test_no_live_price_reads_the_last_two_closes(self):
        self.assertAlmostEqual(cb.breaker_measures([100.0, 97.0])[0], -3.0)
        self.assertEqual(cb.breaker_measures([100.0]), (0.0, 0.0))

    def test_thresholds(self):
        self.assertIn("Single-Day", cb.breaker_reason(-2.0, 0.0))
        self.assertIn("Drawdown", cb.breaker_reason(-1.0, -5.0))
        self.assertIn("Volatility", cb.breaker_reason(0.0, 0.0, 15.0))
        self.assertIsNone(cb.breaker_reason(-1.99, -4.99, 14.9))


class TestCheckUsesTheRightSession(unittest.TestCase):
    def _check(self, series, live):
        with mock.patch.object(cb, "load_spy_history", return_value=series), \
                mock.patch.object(cb, "load_vxx_prev_close", return_value=None), \
                mock.patch.object(cb, "is_market_opening_window", return_value=False):
            return cb.check_systemic_risk(prices={"SPY": live})

    def test_a_flat_morning_after_a_drop_is_not_a_capitulation(self):
        # Yesterday fell from 104 to 100; this morning SPY is 101 (+1%). The old code compared 101 with 104
        # (-2.9%) and froze buys.
        today = datetime.datetime.now(cb.pytz.timezone("America/New_York")).date()
        yesterday = today - datetime.timedelta(days=1)
        series = _series([104.0] * 14 + [100.0], yesterday)
        self.assertEqual(self._check(series, 101.0), (False, ""))

    def test_a_real_drop_this_morning_still_trips_it(self):
        today = datetime.datetime.now(cb.pytz.timezone("America/New_York")).date()
        series = _series([100.0] * 15, today - datetime.timedelta(days=1))
        active, reason = self._check(series, 97.5)
        self.assertTrue(active)
        self.assertIn("Single-Day Capitulation (SPY fell -2.50%", reason)

    def test_a_bar_dated_today_is_replaced_by_the_live_price(self):
        today = datetime.datetime.now(cb.pytz.timezone("America/New_York")).date()
        series = _series([100.0] * 14 + [99.0], today)                       # today's bar already cached
        active, reason = self._check(series, 97.5)                           # vs yesterday's 100: -2.5%
        self.assertTrue(active)
        self.assertIn("-2.50%", reason)


class TestVxxAndLedger(unittest.TestCase):
    """#318 review: the VXX previous close and the DNA ledger's SPY move had the same two-sessions-back bug."""

    def test_vxx_prev_close_is_the_last_session_before_today(self):
        ts = {"2026-11-20": {"4. close": "40.0"}, "2026-11-23": {"4. close": "30.0"}}
        self.assertEqual(cb.prev_session_close(ts, today="2026-11-24"), 30.0)     # morning: yesterday's close
        ts["2026-11-24"] = {"4. close": "35.0"}
        self.assertEqual(cb.prev_session_close(ts, today="2026-11-24"), 30.0)     # evening: today's bar is skipped
        self.assertIsNone(cb.prev_session_close({"2026-11-24": {"4. close": "1"}}, today="2026-11-24"))

    def test_a_real_vxx_spike_on_a_morning_run_trips_the_breaker(self):
        # The reviewer's probe: VXX 40.0 (11-20), 30.0 (11-23), live 35.0 = +16.7% vs yesterday (was read as -12.5%).
        today = datetime.datetime.now(cb.pytz.timezone("America/New_York")).date()
        y1, y2 = today - datetime.timedelta(days=1), today - datetime.timedelta(days=2)
        ts = {y2.isoformat(): {"4. close": "40.0"}, y1.isoformat(): {"4. close": "30.0"}}
        with mock.patch.object(cb, "VXX_FILE") as f, mock.patch("builtins.open", mock.mock_open(read_data="")),                 mock.patch.object(cb.json, "load", return_value={"Time Series (Daily)": ts}):
            f.exists.return_value = True
            prev = cb.load_vxx_prev_close()
        self.assertEqual(prev, 30.0)
        series = _series([100.0] * 15, y1)
        with mock.patch.object(cb, "load_spy_history", return_value=series),                 mock.patch.object(cb, "load_vxx_prev_close", return_value=prev),                 mock.patch.object(cb, "is_market_opening_window", return_value=False):
            active, reason = cb.check_systemic_risk(prices={"SPY": 100.0, "VXX": 35.0})
        self.assertTrue(active)
        self.assertIn("Volatility Capitulation", reason)

    def test_the_ledger_records_the_move_against_yesterday(self):
        today = datetime.datetime.now(cb.pytz.timezone("America/New_York")).date()
        series = _series([104.0] * 14 + [100.0], today - datetime.timedelta(days=1))
        with mock.patch.object(cb, "load_vxx_prev_close", return_value=None),                 mock.patch.object(cb.ledgers, "TRADE_DNA_FILE", cb.Path(self._tmp()) / "dna.json"):
            cb.log_circuit_breaker_trigger_dna("test", {"positions": {}}, prices={"SPY": 101.0}, _spy_series=series)
        import json as _json
        rec = _json.loads((cb.Path(self._dir) / "dna.json").read_text(encoding="utf-8"))
        rows = rec if isinstance(rec, list) else rec.get("trades", rec.get("records", [rec]))
        self.assertEqual(rows[-1]["spy_return_pct"], 1.0)        # +1.00% vs yesterday's 100, not -2.88% vs 104

    def _tmp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._dir = self._td.name
        return self._dir


if __name__ == "__main__":
    unittest.main()
