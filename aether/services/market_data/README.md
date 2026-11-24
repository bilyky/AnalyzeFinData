# market_data service

**Purpose:** market facts every portfolio shares. Today: is the market open. Next: prices (the
E*TRADE -> Google -> cache chain) and the OHLCV store (R&D #78 S5, `plans/aether-services.md`).

**Public API (`api.py`, `API_VERSION` 0.2.0):**
- `SessionService.market_open(now=None) -> (open, message)`: the run gate. A weekday, an NYSE
  session day, and 9:30-16:00 New York time.
- `SessionService.market_hours(now=None) -> bool`: 6:30-13:15 Los Angeles time on a weekday
  that isn't a listed NYSE holiday, and open by E*TRADE's live clock when it answers. No
  answer means the static checks decide; an error means False (skip).
- `run_scope()`: inside one daily run, the live clock is asked **once** and the answer is
  reused. Outside a scope every call asks again. `aether.scenario.runners.run_daily_management`
  opens the scope. This is transitional: from S8 the run passes its session to each stage.
- `regular_hours(now)` and `in_trading_window(now)`: the pure rules.
- `PriceService.live_prices(symbols, today=, market_hours=, workbook_fresh=)`: live prices.
  - On a weekend: the OHLCV cache, then Google for the gaps.
  - After hours with today's workbook already synced: the cache, then Google.
  - Otherwise E*TRADE. With no login, Google prices everything; otherwise Google fills only the
    gaps, and a symbol no source prices is logged as a `SYMBOLOGY ERROR`.
  - Any error: Google prices everything.

  `market_hours` and `workbook_fresh` are callables, asked only when the rule reaches them.
  `PriceService.google` and `.cache` are the single sources behind
  `get_google_prices_fallback` / `get_json_prices_fallback`.
- `missing(symbols, quotes)` and `cached_close_is_current(newest_date, today)`: the pure rules.

**Layout:**
- `domain/session.py`: the rules, pure;
- `ports.py`: `MarketClock` (`is_open_now() -> bool | None`);
- `app.py`: `SessionService` and `run_scope`;
- `adapters/etrade_clock.py`: `EtradeMarketClock`, E*TRADE's clock API.
- `domain/prices.py`, `prices_app.py` (`PriceService`), and `ports.py`: `PriceSource`
  (`quotes(symbols, today=None)`), `BrokerQuotes` (`session()`, `quotes(session, symbols)`);
- `adapters/prices.py`: `EtradeQuotes`, `GooglePrices` (scrapes Google Finance), `OhlcvCloses`
  (the newest current close in `<symbol>_daily.json`).

**Wiring:** `aether.bootstrap` builds `App.market_data`, and `market_prices(cache_dir=...)` builds
the price chain. Until the OHLCV store moves here (next S5 PR), the game passes its own
`SYMBOL_FULL_DIR`. Tests pass `market_clock=` and a
`FixedClock`. `ai_portfolio_game.is_market_open()` / `is_market_hours()` delegate here and still
read "now" themselves, so tests that replace the game's `datetime` keep working.

**Not here yet:** `watchdog.py` and `autonomous_pipeline.py` have their own `is_market_hours`.
They move onto this service later in S5 or in S11.

**Events:** none.
