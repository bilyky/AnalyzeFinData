"""OHLCV cache use cases: read closes, how old the cache is, heal a stale one, the 50-day SMA.

Moved as-is from ``ai_portfolio_game`` (``_load_closes``, ``_cache_age_days``,
``_heal_symbol_cache``, ``_sma50``), with the same log lines (R&D #78 S5).

- ``repair`` is the refresh call, injected (today ``rapidapi.repair_missing``, a root module
  this package must not import). It returns a dict with ``updated`` and maybe ``locked``.
- ``attempted`` is the once-per-process heal guard: each symbol is healed at most once,
  except when another process held the RapidAPI lock (that was never a real attempt).
"""
from aether.logger import get_logger
from aether.services.market_data.domain import ohlcv


class OhlcvService:
    def __init__(self, store, repair, attempted: set, *, sma, log=None):
        self._store = store
        self._repair = repair
        self._attempted = attempted
        if not callable(sma):  # #327 review: a missing rule used to read like a data problem
            raise TypeError("OhlcvService needs sma= (sell_rules.sma_from_closes)")
        self._sma = sma  # sell_rules.sma_from_closes (injected; the trading rule stays there)
        self._log = log or get_logger("ai_game")

    def series(self, symbol):
        """The raw daily series, or None when there is no cache file (bad JSON raises)."""
        return self._store.series(symbol)

    def closes(self, symbol) -> list:
        """Sorted daily closes, or [] when the file is missing or unreadable."""
        try:
            ts = self._store.series(symbol)
            return [] if ts is None else ohlcv.closes(ts)
        except Exception:
            return []

    def age_days(self, symbol, today):
        """Age in days of the newest raw bar, or None if the cache is missing, empty or unreadable."""
        try:
            ts = self._store.series(symbol)
            if ts is None:
                return None
            dates = sorted(ts.keys())
            if not dates:
                return None
            return ohlcv.age_days(dates[-1], today)
        except Exception:
            return None

    def heal(self, symbol, today) -> bool:
        """Pull fresh OHLCV history for a stale symbol; at most once per symbol per process."""
        if symbol in self._attempted:
            return False
        self._attempted.add(symbol)
        try:
            res = self._repair([symbol], str(today))
            if res.get("locked"):
                # Another process holds the RapidAPI lock — this was never a real attempt.
                # Release the once-per-run guard so a later pass can still heal this symbol.
                self._attempted.discard(symbol)
                self._log.info(f"[Self-Healer] {symbol} heal deferred — RapidAPI busy in another process.")
                return False
            if res.get("updated", 0) > 0:
                self._log.info(f"[Self-Healer] Healed {symbol} OHLCV cache.")
                return True
            self._log.warning(f"[Self-Healer] On-demand refresh for {symbol} yielded 0 updates.")
            return False
        except Exception as e:
            self._log.warning(f"[Self-Healer] Failed to heal {symbol} cache: {e}")
            return False

    def sma50(self, symbol, today, max_stale_days=ohlcv.MAX_STALE_DAYS, heal=None):
        """50-day SMA of cached closes, healing a missing or stale cache first (``heal``)."""
        heal = heal or (lambda s: self.heal(s, today))
        log = self._log
        try:
            needs_healing = False
            if not self._store.path(symbol).exists():
                needs_healing = True
                log.warning(f"[sma50] {symbol} OHLCV cache missing — winner protection unavailable.")
            else:
                try:
                    ts = self._store.series(symbol)
                    dates = sorted(ts.keys())
                    if not dates:
                        needs_healing = True
                    else:
                        last_bar_date = dates[-1]
                        stale_days = ohlcv.age_days(last_bar_date, today)
                        if stale_days > max_stale_days:
                            needs_healing = True
                            log.warning(f"[sma50] {symbol} OHLCV cache stale by {stale_days}d (last bar: {last_bar_date}).")
                except Exception:
                    needs_healing = True

            if needs_healing:
                if not heal(symbol):
                    log.warning(f"[sma50] {symbol} cache heal failed; winner protection unavailable this run.")
                    return None

            # Re-read the newly healed/fresh cache file
            ts = self._store.series(symbol)
            if ts is None:
                return None
            dates = sorted(ts.keys())
            if not dates:
                return None
            return self._sma(ohlcv.closes(ts), 50)
        except Exception as e:
            log.warning(f"Failed to calculate 50-day SMA for {symbol}: {e}")
            return None
