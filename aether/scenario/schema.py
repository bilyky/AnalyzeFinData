"""Entity views over the raw scenario dicts (hexagonal schema, BUILD phase B2).

WHAT THIS IS
------------
The second seam of the ``ai_portfolio_game`` -> ``aether/scenario`` refactor
(design doc ``plans/scenario-refactor.md``, R&D roadmap). Where B1 (``prices.py``)
gave the *action* ports, this gives the *entity* views: ``Portfolio``,
``Position``, ``Order`` and ``Quote`` — typed lenses over the plain JSON dicts the
game already threads by hand (the ``state`` dict from ``load_game``, each entry of
``state["positions"]``, each ``state["queued_orders"]`` entry, and the
``{symbol: price}`` map ``get_live_prices`` returns).

VIEWS, NOT REPLACEMENTS  (the load-bearing invariant)
-----------------------------------------------------
Each view **wraps** a raw dict as its backing store; it never owns a copy:

* :meth:`from_dict` wraps — it stores the very dict passed in, no copy.
* :meth:`to_dict` returns that **same object** (identity), not a rebuild.
* ``.positions`` / ``.history`` / ``.queued_orders`` / ``.raw`` hand back the live
  underlying containers, and every setter writes straight through to the dict.

This is mandatory, not stylistic: ``circuit_breaker.enforce_circuit_breaker``,
``options.resolve_expiring_options`` and ``options.execute_weekly_covered_call_pass``
all mutate the raw ``state`` / position dicts **by reference** (e.g. ``options.py``
does ``pos["written_call"] = {...}`` and ``del pos["written_call"]``; the SELL loop
does ``pos["highest_close_since_acq"] = ...`` and ``pos["banked_pct"] = ...``). A
view that copied on construction would silently drop those mutations. Because the
view is a lens over the live dict, a change made through the view — or by any
legacy code still holding the same dict — is visible to both. Precedent:
``options_adviser.normalize_chain`` builds ``OptionQuote`` from a raw dict without
copying it.

Nothing here is wired into ``ai_portfolio_game`` yet — B2 is additive; the REPLACE
phase routes call-sites through these views one PR at a time, each swap behaviour-
preserving because the view *is* the dict.
"""
from __future__ import annotations

from aether.scenario.prices import _missing


# ===========================================================================
# Position — a lens over one entry of state["positions"]
# ===========================================================================

class Position:
    """A single open position: a view over ``state["positions"][symbol]``.

    The symbol is the *key* in the positions map, not a field of the position
    dict, so it is carried alongside the view (optional — helpers that don't need
    it still work when it is ``None``). Read accessors use the same ``.get``
    defaults as the call-sites in ``ai_portfolio_game`` (``qty`` -> 0, ``cost`` ->
    0.0, ``stop_loss`` -> ``None``); the setters write straight through to the raw
    dict so the by-reference mutation contract holds.
    """

    __slots__ = ("_d", "symbol")

    def __init__(self, raw: dict, symbol: str | None = None):
        self._d = raw
        self.symbol = symbol

    @classmethod
    def from_dict(cls, raw: dict, symbol: str | None = None) -> "Position":
        """Wrap ``raw`` (no copy)."""
        return cls(raw, symbol)

    def to_dict(self) -> dict:
        """Return the backing dict itself (identity — never a copy)."""
        return self._d

    @property
    def raw(self) -> dict:
        """The live backing dict."""
        return self._d

    # --- read accessors (defaults mirror the game's call-sites) ---
    @property
    def qty(self):
        return self._d.get("qty", 0)

    @property
    def cost(self) -> float:
        return self._d.get("cost", 0.0)

    @property
    def stop_loss(self):
        return self._d.get("stop_loss")

    @property
    def is_scarcity(self) -> bool:
        return self._d.get("is_scarcity", False)

    @property
    def banked_pct(self) -> float:
        # The SELL loop coerces this to float and treats a missing/None value as 0.0.
        return float(self._d.get("banked_pct", 0.0) or 0.0)

    @property
    def highest_close_since_acq(self) -> float:
        return self._d.get("highest_close_since_acq", 0.0)

    @property
    def buy_dna(self) -> dict:
        """The entry-DNA sub-dict (``buy_date``/``pgr``/``s10``/``l60``/``score``/
        ``z_score``/``industry``); ``{}`` for a legacy position that predates it."""
        return self._d.get("buy_dna") or {}

    @property
    def written_call(self):
        """The covered-call liability sub-dict, or ``None`` when no call is written.
        Set and removed in place by ``aether/options.py``."""
        return self._d.get("written_call")

    @property
    def verdicts(self) -> dict:
        return self._d.get("verdicts", {})

    def has_written_call(self) -> bool:
        return "written_call" in self._d

    def market_value(self, price) -> float:
        """``qty * price``, falling back to cost basis when the quote is missing or
        non-positive — the exact per-position rule ``_live_equity`` applies, kept in
        one place so the two never drift."""
        if not price or price <= 0:
            price = self._d.get("cost", 0.0)
        return self._d.get("qty", 0) * price

    # --- setters (write through to the raw dict) ---
    @stop_loss.setter
    def stop_loss(self, value):
        self._d["stop_loss"] = value

    @is_scarcity.setter
    def is_scarcity(self, value: bool):
        self._d["is_scarcity"] = value

    @banked_pct.setter
    def banked_pct(self, value: float):
        self._d["banked_pct"] = value

    @highest_close_since_acq.setter
    def highest_close_since_acq(self, value: float):
        self._d["highest_close_since_acq"] = value

    def __repr__(self) -> str:
        sym = self.symbol or "?"
        return f"Position({sym}: qty={self.qty}, cost={self.cost})"


# ===========================================================================
# Order — a lens over one entry of state["queued_orders"]
# ===========================================================================

class Order:
    """A queued strategic order: ``{"type": "BUY"|"SELL", "symbol", "reason"}``.

    The exact three-key shape appended by the SELL-decision and after-hours-BUY
    stages (``state.setdefault("queued_orders", []).append({...})``) and consumed
    by the queued-order executor.
    """

    __slots__ = ("_d",)

    def __init__(self, raw: dict):
        self._d = raw

    @classmethod
    def from_dict(cls, raw: dict) -> "Order":
        """Wrap ``raw`` (no copy)."""
        return cls(raw)

    def to_dict(self) -> dict:
        """Return the backing dict itself (identity)."""
        return self._d

    @property
    def raw(self) -> dict:
        return self._d

    @property
    def type(self):
        return self._d.get("type")

    @property
    def symbol(self):
        return self._d.get("symbol")

    @property
    def reason(self):
        return self._d.get("reason")

    def is_buy(self) -> bool:
        return self._d.get("type") == "BUY"

    def is_sell(self) -> bool:
        return self._d.get("type") == "SELL"

    def __repr__(self) -> str:
        return f"Order({self.type} {self.symbol})"


# ===========================================================================
# Quote — a lens over the {symbol: price} book PriceSource.get_prices returns
# ===========================================================================

class Quote:
    """A read view over a ``{symbol: price}`` quote book.

    This is the plain dict of last/close prices that flows through
    ``get_live_prices`` -> ``_execute_buys`` and that :class:`PriceSource`
    (``prices.py``) produces. "Usable" is the *same* predicate the price chain
    applies — a symbol is missing when it is absent, falsy, or non-positive — so
    :func:`_missing` is reused rather than re-stated here (one definition of the
    gap rule for the whole package).
    """

    __slots__ = ("_d",)

    def __init__(self, raw: dict):
        self._d = raw

    @classmethod
    def from_dict(cls, raw: dict) -> "Quote":
        """Wrap ``raw`` (no copy)."""
        return cls(raw)

    def to_dict(self) -> dict:
        """Return the backing dict itself (identity)."""
        return self._d

    @property
    def raw(self) -> dict:
        return self._d

    def price(self, symbol, default=None):
        """The quoted price for ``symbol`` (``default`` if absent)."""
        return self._d.get(symbol, default)

    def is_usable(self, symbol) -> bool:
        """True iff ``symbol`` has a real, positive quote (not absent/falsy/≤0)."""
        p = self._d.get(symbol)
        return bool(p) and p > 0

    def missing(self, symbols) -> list:
        """The subset of ``symbols`` with no usable quote — the gap the chain fills."""
        return _missing(symbols, self._d)

    def __contains__(self, symbol) -> bool:
        return self.is_usable(symbol)

    def __repr__(self) -> str:
        return f"Quote({len(self._d)} symbols)"


# ===========================================================================
# Portfolio — a lens over the whole state dict
# ===========================================================================

class Portfolio:
    """A view over the game ``state`` dict (the object ``load_game`` returns).

    Exposes the documented top-level keys — ``balance``, ``equity``, ``positions``,
    ``history``, ``start_date``, ``profile``, ``profile_mode``, ``queued_orders`` —
    as typed accessors. ``positions``/``history``/``queued_orders`` return the
    **live** underlying containers (``queued_orders`` via ``setdefault`` so it is
    always a real list, exactly as the game does), so ``pop``/``append``/assignment
    through them mutate the same objects legacy code holds. Scalar setters
    (``balance``/``equity``/``profile``/``profile_mode``) write straight through.
    """

    __slots__ = ("_d",)

    def __init__(self, raw: dict):
        self._d = raw

    @classmethod
    def from_dict(cls, raw: dict) -> "Portfolio":
        """Wrap ``raw`` (no copy)."""
        return cls(raw)

    def to_dict(self) -> dict:
        """Return the backing state dict itself (identity — never a copy)."""
        return self._d

    @property
    def raw(self) -> dict:
        return self._d

    # --- scalar fields ---
    @property
    def balance(self) -> float:
        return self._d.get("balance", 0.0)

    @balance.setter
    def balance(self, value: float):
        self._d["balance"] = value

    @property
    def equity(self) -> float:
        return self._d.get("equity", 0.0)

    @equity.setter
    def equity(self, value: float):
        self._d["equity"] = value

    @property
    def start_date(self):
        return self._d.get("start_date")

    @property
    def profile(self):
        return self._d.get("profile")

    @profile.setter
    def profile(self, value):
        self._d["profile"] = value

    @property
    def profile_mode(self):
        return self._d.get("profile_mode")

    @profile_mode.setter
    def profile_mode(self, value):
        self._d["profile_mode"] = value

    # --- live containers (identity preserved) ---
    @property
    def positions(self) -> dict:
        """The live ``{symbol: position_dict}`` map."""
        return self._d.setdefault("positions", {})

    @property
    def history(self) -> list:
        """The live transaction-history list."""
        return self._d.setdefault("history", [])

    @property
    def queued_orders(self) -> list:
        """The live queued-order list (created on first access, as the game does)."""
        return self._d.setdefault("queued_orders", [])

    # --- typed views over the live containers ---
    def position(self, symbol) -> Position | None:
        """A :class:`Position` view of ``symbol``, or ``None`` if not held."""
        raw = self._d.get("positions", {}).get(symbol)
        return Position(raw, symbol) if raw is not None else None

    def position_views(self):
        """Iterate ``(symbol, Position)`` over every open position (live views)."""
        for sym, raw in self._d.get("positions", {}).items():
            yield sym, Position(raw, sym)

    def orders(self) -> list:
        """A list of :class:`Order` views over the queued orders (live backing dicts)."""
        return [Order(o) for o in self._d.get("queued_orders", [])]

    def __repr__(self) -> str:
        return (f"Portfolio(balance={self.balance}, equity={self.equity}, "
                f"positions={len(self._d.get('positions', {}))})")


# ===========================================================================
# ResearchRow — a positional lens over one Research-sheet row (BUILD phase B4)
# ===========================================================================

class ResearchRow:
    """A read view over one row of the workbook **Research** sheet.

    Unlike the other views in this module, the backing store is a **positional
    tuple**, not a dict: ``ws.iter_rows(min_row=2, values_only=True)`` yields the
    row as a tuple of cell values, and the game addresses its columns by hard
    magic indices scattered across ``ai_portfolio_game.py``. This class is the one
    place those indices are named, so the row-scan sites can read a field by name
    instead of repeating ``row[24]``. It wraps the tuple by reference (no copy);
    :meth:`to_row` returns the same object (identity), exactly like the dict views'
    ``to_dict``.

    THE PREV-CLOSE SPLIT IS DELIBERATE — DO NOT UNIFY
    -------------------------------------------------
    The Research sheet stores a previous close that the game reads from **two
    different columns depending on the call-site**, a verified intentional
    inconsistency (design doc ``plans/scenario-refactor.md``, constraint 4):

    * :attr:`prev_close_sell` = ``row[8]``  — read only in the SELL-decision loop
      (``ai_portfolio_game.py`` ~L1626: ``float(row[8] or pos["cost"])``).
    * :attr:`prev_close_buy`  = ``row[10]`` — read in BUY screening (~L1902), the
      report/get-live-prices fallback (~L1183) and the after-hours queue (~L2151).

    There is **no** unified ``prev_close`` accessor on purpose: each caller keeps
    its existing column so the REPLACE phase stays behaviour-preserving. Unifying
    them would silently change one site's value.

    PURE POSITIONAL, NO COERCION
    ----------------------------
    Every field accessor returns the **raw** cell value (or ``None`` when the row
    is shorter than that index — openpyxl yields ragged rows, which is why the
    call-sites guard with ``len(row) > 10``). No ``or 0`` / ``or "Neutral"`` /
    ``_to_float`` coercion is baked in, because the game applies *different*
    coercions to the same column at different sites (e.g. ``row[9]`` is
    ``_to_float``'d in BUY screening but ``repr``'d raw in the incomplete-S/R log).
    Each caller keeps its own coercion. The only exceptions are the two documented
    *derived* conveniences below, each mirroring exactly one call-site expression.

    The magic indices are Research-sheet specific. The ``Short_Long`` fallback
    sheet has a different layout (``row[1]``=symbol, ``row[4]``=price) and is **not**
    modelled here.
    """

    __slots__ = ("_r",)

    # Column index map (Research sheet). Kept as class constants so the numbers
    # live in exactly one place and the accessors below read by name.
    COL_SYMBOL = 3
    COL_INDUSTRY = 4
    COL_PGR = 6
    COL_PREV_CLOSE_SELL = 8
    COL_STOP = 9
    COL_PREV_CLOSE_BUY = 10
    COL_TARGET = 11
    COL_SETUP = 20
    COL_S10 = 24
    COL_L60 = 25

    def __init__(self, raw):
        self._r = raw

    @classmethod
    def from_row(cls, raw) -> "ResearchRow":
        """Wrap ``raw`` (the row tuple; no copy)."""
        return cls(raw)

    def to_row(self):
        """Return the backing row itself (identity — never a copy)."""
        return self._r

    @property
    def raw(self):
        """The live backing row tuple."""
        return self._r

    def _cell(self, i):
        """The raw cell at index ``i``, or ``None`` when the row is too short."""
        r = self._r
        return r[i] if i < len(r) else None

    # --- positional named accessors (raw cell, no coercion) ---
    @property
    def symbol(self):
        return self._cell(self.COL_SYMBOL)

    @property
    def industry(self):
        return self._cell(self.COL_INDUSTRY)

    @property
    def pgr(self):
        return self._cell(self.COL_PGR)

    @property
    def prev_close_sell(self):
        """Previous close as read by the SELL loop (``row[8]``). See class note."""
        return self._cell(self.COL_PREV_CLOSE_SELL)

    @property
    def stop(self):
        return self._cell(self.COL_STOP)

    @property
    def prev_close_buy(self):
        """Previous close as read by BUY/report/after-hours (``row[10]``). See class note."""
        return self._cell(self.COL_PREV_CLOSE_BUY)

    @property
    def target(self):
        return self._cell(self.COL_TARGET)

    @property
    def setup(self):
        return self._cell(self.COL_SETUP)

    @property
    def s10(self):
        return self._cell(self.COL_S10)

    @property
    def l60(self):
        return self._cell(self.COL_L60)

    # --- derived conveniences (each mirrors exactly one call-site expression) ---
    def is_active_setup(self) -> bool:
        """Whether this row is an active setup — the ``_active_setup_symbols``
        predicate verbatim (``ai_portfolio_game.py`` ~L302):
        ``row[3] and str(row[20] or '') in ('1', 'OK', 1)``. Uses the project's
        ``'OK'``/``''`` Setup-field convention."""
        return bool(self.symbol) and str(self.setup or "") in ("1", "OK", 1)

    def total_score(self) -> float:
        """Combined S10+L60 score — the BUY-screening expression verbatim
        (~L1844): ``(row[24] or 0.0) + (row[25] or 0.0)``."""
        return (self.s10 or 0.0) + (self.l60 or 0.0)

    def __repr__(self) -> str:
        return f"ResearchRow(symbol={self.symbol!r}, s10={self.s10}, l60={self.l60})"
