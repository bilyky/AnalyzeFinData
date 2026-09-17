"""Price-source ports & adapters for the scenario package (hexagonal).

WHAT THIS IS (BUILD phase B1)
-----------------------------
The first, least-abstracted seam of the ``ai_portfolio_game`` -> ``aether/scenario``
refactor (design doc ``plans/scenario-refactor.md``, R&D #44). It reproduces
``ai_portfolio_game.get_live_prices``' exact fallback chain as a composable
``PriceSource``:

    weekend gate            -> local JSON cache, Google-fill the gaps
    after-hours + fresh wb  -> local JSON cache, Google-fill the gaps
    weekday market path     -> E*TRADE (ETradeClient), Google-fill the gaps
    any exception           -> whole-list Google fallback

Built **additively**: nothing here is wired into the root script yet. The root
``get_live_prices`` is untouched until the REPLACE phase (R1), at which point it
delegates to ``make_price_source().get_prices(...)`` with zero behaviour change.

THE ``_pkg()`` SEAM (load-bearing)
----------------------------------
Every seam-bearing collaborator — ``is_market_hours``, the JSON-cache and Google
fallbacks, the E*TRADE client, ``datetime``, ``os``, ``XLSX_FILE``, ``openpyxl`` —
is resolved off the ``ai_portfolio_game`` module **at call time** via :func:`_pkg`
(a lazy ``import ai_portfolio_game``), never bound at import. This mirrors
``aether/etrade/store.py::_pkg()`` and is what keeps the pinned test patches
(``tests/test_game_pricing.py`` does ``mock.patch.object(game, "is_market_hours"...)``,
``mock.patch.object(game.etrade, "fetch_quotes"...)``, ``mock.patch.object(game,
"get_google_prices_fallback"...)``) intercepting after R1 moves the logic here.

The E*TRADE adapter reproduces the production construction verbatim
(``etrade.ETradeClient("production", role="auth")`` -> ``.auth.get_tokens()`` ->
``.market.quotes(symbols, tokens)``); ``ETradeClient`` itself delegates to the
module-level ``etrade.get_tokens`` / ``etrade.fetch_quotes`` free functions, which
are exactly what the pinned tests patch — so the seam survives the move.
"""
from __future__ import annotations

import abc


def _pkg():
    """Return the ``ai_portfolio_game`` module, resolved lazily at call time.

    Imported inside functions (never at module import) so collaborators are looked
    up on the live module object each call — a ``mock.patch.object(game, ...)`` then
    keeps intercepting after the logic physically moves into this package, and there
    is no circular-import hazard with the root script.
    """
    import ai_portfolio_game as _p
    return _p


def _missing(symbols, quotes):
    """The symbols with no usable quote — absent, falsy, or non-positive.

    The exact gap predicate ``get_live_prices`` applies at every fallback step, so
    Google is only ever asked for the symbols still missing a real price.
    """
    return [s for s in symbols if s not in quotes or not quotes[s] or quotes[s] <= 0]


# ===========================================================================
# Port
# ===========================================================================

class PriceSource(abc.ABC):
    """A source of last/close prices for a list of symbols.

    ``get_prices(symbols) -> {symbol: price}`` returns only what the source could
    price; a symbol with no usable quote is simply absent from the dict (never a
    ``0``/``None`` placeholder), so a caller can compute the gap with :func:`_missing`.
    """

    @abc.abstractmethod
    def get_prices(self, symbols):  # pragma: no cover - interface
        """Return ``{symbol: price}`` for whatever this source can price."""


# ===========================================================================
# Leaf adapters (one collaborator each, resolved via _pkg at call time)
# ===========================================================================

class JsonCacheSource(PriceSource):
    """Latest close from the local per-symbol OHLCV JSON caches.

    Thin adapter over ``ai_portfolio_game.get_json_prices_fallback`` (weekend- and
    holiday-tolerant: accepts a close up to 4 calendar days old).
    """

    def get_prices(self, symbols):
        return _pkg().get_json_prices_fallback(symbols)


class GoogleSource(PriceSource):
    """Live quotes scraped from Google Finance — the universal last-resort fill.

    Thin adapter over ``ai_portfolio_game.get_google_prices_fallback``.
    """

    def get_prices(self, symbols):
        return _pkg().get_google_prices_fallback(symbols)


class EtradeSource(PriceSource):
    """Live E*TRADE quotes via ``ETradeClient`` (single-writer auth role).

    Reproduces ``get_live_prices``' production construction exactly so the pinned
    patches of ``etrade.get_tokens`` / ``etrade.fetch_quotes`` (which ``ETradeClient``
    delegates to) keep intercepting. Returns ``{}`` when authentication yields no
    tokens: the chain then treats every symbol as a gap and back-fills the whole
    list from Google — the same dict ``get_live_prices`` returns on auth failure.
    A genuine transport error is NOT swallowed here; it propagates to
    :class:`ChainedPriceSource`'s outer guard, which matches ``get_live_prices``'
    whole-list Google fallback on any exception.
    """

    def __init__(self, env: str = "production"):
        self.env = env

    def get_prices(self, symbols):
        game = _pkg()
        _et = game.etrade.ETradeClient(self.env, role="auth")
        tokens = _et.auth.get_tokens()
        if not tokens:
            return {}
        return _et.market.quotes(symbols, tokens)


class WorkbookSource(PriceSource):
    """Close prices from the local workbook (``Short_Long``, else ``Research``).

    The workbook fallback used by the daily-summary path when live quotes are
    unavailable (e.g. weekends with a stale cache). Preserves the per-site magic
    indices **verbatim, un-unified**:

    * ``Short_Long``: symbol = ``row[1]``, price = ``row[4]`` (rows from 3).
    * ``Research``:   symbol = ``row[3]``, price = ``row[10]`` (rows from 2) — the
      BUY/report prev-close column, deliberately distinct from the SELL loop's
      ``row[8]`` (see ``plans/scenario-refactor.md`` constraint 4; the split is a
      verified intentional inconsistency and is never collapsed).

    This is a pure price *source*: it returns only the prices actually present in
    the workbook. The summary's cost-basis default (``row[...] or cost``) is a
    composition concern of the caller, not of this adapter. Not part of the default
    :func:`make_price_source` chain (``get_live_prices`` never reads the workbook for
    prices — only for a freshness check); it exists for the summary/report seams that
    the REPLACE phase will route through it. Reads the workbook via the package's
    ``openpyxl`` object so tests can patch it.
    """

    def get_prices(self, symbols):
        game = _pkg()
        wanted = set(symbols)
        quotes: dict = {}
        wb = None
        try:
            wb = game.openpyxl.load_workbook(game.XLSX_FILE, read_only=True, data_only=True)
            if "Short_Long" in wb.sheetnames:
                ws = wb["Short_Long"]
                for row in ws.iter_rows(min_row=3, values_only=True):
                    if len(row) > 4:
                        sym = str(row[1] or "").strip().upper()
                        if sym in wanted and sym not in quotes and row[4]:
                            quotes[sym] = row[4]
            elif "Research" in wb.sheetnames:
                ws = wb["Research"]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    if len(row) > 10:
                        sym = str(row[3] or "").strip().upper()
                        if sym in wanted and sym not in quotes and row[10]:
                            quotes[sym] = row[10]
        except Exception as e:
            game._log.warning(f"Workbook price source failed: {e}")
        finally:
            if wb:
                try:
                    wb.close()
                except Exception:
                    pass
        return quotes


# ===========================================================================
# Composite: the get_live_prices fallback chain, statement-for-statement
# ===========================================================================

class ChainedPriceSource(PriceSource):
    """Reproduce ``ai_portfolio_game.get_live_prices``' exact fallback chain.

    The leaf adapters are the injectable seams; this class is the algorithm — it
    mirrors ``get_live_prices`` step for step so R1 is a behaviour-preserving swap:

    1. **Weekend gate** — Sat/Sun bypass E*TRADE entirely: price from the JSON
       cache, then Google-fill any gaps.
    2. **After-hours + fresh-workbook gate** — off-hours on a weekday, if today's
       workbook is already synced, bypass E*TRADE: JSON cache, then Google-fill.
       If the workbook is stale, fall through to E*TRADE.
    3. **Weekday market path** — E*TRADE live quotes, then Google-fill only the
       gaps (dead/delisted/misaligned tickers), preserving good E*TRADE quotes.
    4. **Guard** — any exception anywhere above falls back to a whole-list Google
       scrape, exactly as ``get_live_prices``' outer ``try/except`` does.

    All temporal / filesystem checks resolve through :func:`_pkg` at call time
    (``game.datetime.date``, ``game.is_market_hours``, ``game.os.path``,
    ``game.XLSX_FILE``), so a test that patches those on the module steers this too.
    """

    def __init__(self, json_cache: PriceSource, etrade: PriceSource,
                 google: PriceSource, env: str = "production"):
        self.json_cache = json_cache
        self.etrade = etrade
        self.google = google
        self.env = env

    def _fill_gaps(self, symbols, quotes):
        """Google-fill the still-missing symbols, preserving the quotes we have."""
        missing = _missing(symbols, quotes)
        if missing:
            quotes.update(self.google.get_prices(missing))
        return quotes

    def _workbook_is_fresh(self, game) -> bool:
        """True iff today's workbook file was last modified today (ET-agnostic local
        date compare, matching get_live_prices). Any I/O hiccup reads as not-fresh."""
        try:
            if game.os.path.exists(game.XLSX_FILE):
                mtime = game.os.path.getmtime(game.XLSX_FILE)
                mdate = game.datetime.date.fromtimestamp(mtime)
                return mdate == game.datetime.date.today()
        except Exception:
            pass
        return False

    def get_prices(self, symbols):
        game = _pkg()
        try:
            # 1. Weekend gate — never touch E*TRADE on Sat/Sun.
            if game.datetime.date.today().weekday() in (5, 6):
                return self._fill_gaps(symbols, self.json_cache.get_prices(symbols))

            # 2. After-hours + already-synced workbook -> local cache, skip E*TRADE.
            if not game.is_market_hours() and self._workbook_is_fresh(game):
                return self._fill_gaps(symbols, self.json_cache.get_prices(symbols))

            # 3. Weekday market path — E*TRADE, then Google-fill only the gaps.
            return self._fill_gaps(symbols, self.etrade.get_prices(symbols))
        except Exception:
            # 4. Any failure -> whole-list Google scrape (get_live_prices' outer guard).
            return self.google.get_prices(symbols)


def make_price_source(env: str = "production") -> ChainedPriceSource:
    """Build the default live-price chain (JSON cache -> E*TRADE -> Google).

    The drop-in ``get_live_prices`` will delegate to at R1. ``WorkbookSource`` is
    deliberately not in this chain — ``get_live_prices`` reads the workbook only for
    a freshness check, never as a price source.
    """
    return ChainedPriceSource(
        json_cache=JsonCacheSource(),
        etrade=EtradeSource(env),
        google=GoogleSource(),
        env=env,
    )
