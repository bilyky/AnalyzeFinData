# market_data service

**Purpose:** market facts every portfolio shares. Today: is the market open. Next: prices (the
E*TRADE -> Google -> cache chain) and the OHLCV store (R&D #78 S5, `plans/aether-services.md`).

**Public API (`api.py`, `API_VERSION` 0.1.0):**
- `SessionService.market_open(now=None) -> (open, message)`: the run gate. A weekday, an NYSE
  session day, and 9:30-16:00 New York time.
- `SessionService.market_hours(now=None) -> bool`: 6:30-13:15 Los Angeles time on a weekday
  that isn't a listed NYSE holiday, and open by E*TRADE's live clock when it answers. No
  answer means the static checks decide; an error means False (skip).
- `run_scope()`: inside one daily run, the live clock is asked **once** and the answer is
  reused. Outside a scope every call asks again. `aether.scenario.runners.run_daily_management`
  opens the scope. This is transitional: from S8 the run passes its session to each stage.
- `regular_hours(now)` and `in_trading_window(now)`: the pure rules.

**Layout:**
- `domain/session.py`: the rules, pure;
- `ports.py`: `MarketClock` (`is_open_now() -> bool | None`);
- `app.py`: `SessionService` and `run_scope`;
- `adapters/etrade_clock.py`: `EtradeMarketClock`, E*TRADE's clock API.

**Wiring:** `aether.bootstrap` builds `App.market_data`. Tests pass `market_clock=` and a
`FixedClock`. `ai_portfolio_game.is_market_open()` / `is_market_hours()` delegate here and still
read "now" themselves, so tests that replace the game's `datetime` keep working.

**Not here yet:** `watchdog.py` and `autonomous_pipeline.py` have their own `is_market_hours`.
They move onto this service later in S5 or in S11.

**Events:** none.
