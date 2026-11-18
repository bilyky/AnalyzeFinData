"""aether.core (the shared kernel) and aether.bootstrap (R&D #78 S3). No mocks."""
import datetime
import unittest
from dataclasses import dataclass

from aether.bootstrap import App, bootstrap
from aether.core import (DEFAULT_PORTFOLIO, AetherError, DomainError, DomainEvent, EventBus, FixedClock,
                         PortfolioId, StorageError, SystemClock)

_UTC = datetime.timezone.utc


@dataclass(frozen=True)
class _Opened(DomainEvent):
    symbol: str


@dataclass(frozen=True)
class _Closed(DomainEvent):
    symbol: str


class TestPortfolioId(unittest.TestCase):
    def test_valid_ids(self):
        for value in ("ai_game", "real_3", "p", "a" * 40):
            with self.subTest(value=value):
                self.assertEqual(str(PortfolioId(value)), value)

    def test_invalid_ids_are_refused(self):
        for value in ("", "AI", "1st", "has-dash", "a/b", "../x", "a" * 41, None, 7):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    PortfolioId(value)

    def test_value_equality_and_default(self):
        self.assertEqual(PortfolioId("ai_game"), DEFAULT_PORTFOLIO)
        self.assertEqual(len({PortfolioId("x"), PortfolioId("x")}), 1)


class TestClock(unittest.TestCase):
    def test_system_clock_is_utc_aware_and_current(self):
        before = datetime.datetime.now(_UTC)
        now = SystemClock().now()
        self.assertIs(now.tzinfo, _UTC)
        self.assertLessEqual(abs((now - before).total_seconds()), 5)

    def test_fixed_clock_holds_converts_and_advances(self):
        ny = datetime.timezone(datetime.timedelta(hours=-5))
        clock = FixedClock(datetime.datetime(2026, 11, 26, 10, 0, tzinfo=ny))
        self.assertEqual(clock.now(), datetime.datetime(2026, 11, 26, 15, 0, tzinfo=_UTC))
        self.assertEqual(clock.now(), clock.now())
        clock.advance(datetime.timedelta(minutes=30))
        self.assertEqual(clock.now(), datetime.datetime(2026, 11, 26, 15, 30, tzinfo=_UTC))

    def test_fixed_clock_refuses_naive_time(self):
        with self.assertRaises(ValueError):
            FixedClock(datetime.datetime(2026, 11, 26, 10, 0))


class TestEventBus(unittest.TestCase):
    def test_events_go_out_in_order_to_matching_handlers_in_subscription_order(self):
        bus, seen = EventBus(), []
        bus.subscribe(_Opened, lambda e: seen.append(("opened-1", e.symbol)))
        bus.subscribe(DomainEvent, lambda e: seen.append(("any", type(e).__name__)))
        bus.subscribe(_Opened, lambda e: seen.append(("opened-2", e.symbol)))
        bus.publish([_Opened("AAA"), _Closed("BBB")])
        self.assertEqual(seen, [("opened-1", "AAA"), ("any", "_Opened"), ("opened-2", "AAA"), ("any", "_Closed")])

    def test_handler_error_propagates(self):
        bus = EventBus()

        def boom(_):
            raise RuntimeError("ledger write failed")

        bus.subscribe(_Closed, boom)
        with self.assertRaises(RuntimeError):
            bus.publish([_Closed("AAA")])

    def test_type_checks(self):
        bus = EventBus()
        with self.assertRaises(TypeError):
            bus.subscribe(str, print)
        with self.assertRaises(TypeError):
            bus.publish(["not an event"])

    def test_events_are_immutable(self):
        event = _Opened("AAA")
        with self.assertRaises(AttributeError):
            event.symbol = "BBB"


class TestErrors(unittest.TestCase):
    def test_hierarchy(self):
        self.assertTrue(issubclass(DomainError, AetherError))
        self.assertTrue(issubclass(StorageError, AetherError))
        self.assertTrue(issubclass(AetherError, Exception))


class TestBootstrap(unittest.TestCase):
    def test_default_wiring(self):
        app = bootstrap()
        self.assertIsInstance(app, App)
        self.assertIsInstance(app.clock, SystemClock)
        self.assertIsInstance(app.events, EventBus)

    def test_injected_clock_and_fresh_bus_per_app(self):
        clock = FixedClock(datetime.datetime(2026, 1, 2, tzinfo=_UTC))
        a, b = bootstrap(clock=clock), bootstrap(clock=clock)
        self.assertIs(a.clock, clock)
        self.assertIsNot(a.events, b.events)


if __name__ == "__main__":
    unittest.main()
