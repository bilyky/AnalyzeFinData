"""Interface-agnostic options + tax adviser (the INTC collar/CPA adviser).

Given a stock position (qty, cost basis, acquisition date), the current price plus
AETHER stop/target *levels*, and an option chain, this module builds a **menu** of
protection / income strategies —

    * collar               (long protective put + short covered call)
    * protective put        (long put only)
    * covered call          (short call only)
    * cash-secured put      (short put to add shares)

— and attaches to each its economics (net cost, max loss/gain, protection floor,
upside cap, breakevens) *and* its U.S.-tax considerations (LTCG/STCG holding period,
IRC §1259 constructive sale, §1092 qualified-covered-call, §1091 wash sale).

DESIGN: the builders and the tax layer are **pure** — no network, no files, no
printing — so the whole engine is unit-testable against a saved chain fixture. The
only I/O is ``fetch_chain`` (a thin adapter over ``ETradeClient.market``) and the two
renderers, kept deliberately separate so terminal / e-mail / web / skill are all thin
interfaces over the same ``AdviserReport``.

The tax notes are *educational considerations tied to each strategy*, never advice —
every report carries the ``TAX_DISCLAIMER``. This module recommends; it never places,
transmits, or executes an order (order placement stays the Phase-3 stub in
``aether/etrade/client.py``).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

from aether.logger import get_logger as _get_logger


_log = _get_logger("options_adviser")

# 1 option contract controls this many shares.
SHARES_PER_CONTRACT = 100
# IRS long-term capital-gain threshold: a holding period of *more than* one year.
LONG_TERM_DAYS = 365
# Heuristic thresholds for the tax flags (deliberately conservative — they only
# *raise a consideration*, they do not decide anything).
_QCC_MIN_DTE = 30          # a covered call with <=30 DTE is at risk of being unqualified
_COLLAR_TIGHT_BAND = 0.10  # call/put band < 10% of spot => flag §1259 constructive sale
_NEAR_LT_DAYS = 45         # within 45 days of long-term => flag holding-period risk
# Auto-selected expiry window when the caller doesn't pin one. ~30-45 DTE is the theta
# sweet spot for these protection/income structures; we take the nearest listed expiry
# at least _MIN_DTE out, preferring one closest to _TARGET_DTE — so the menu never
# defaults to a degenerate 1-DTE front-month (put/call collapse onto one strike).
_MIN_DTE = 30
_TARGET_DTE = 35

TAX_DISCLAIMER = (
    "These are general tax *considerations* tied to each structure, not tax advice. "
    "Rules (IRC §1091 wash sale, §1092 straddle/qualified-covered-call, §1259 "
    "constructive sale) are fact-specific — confirm with a CPA before trading."
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Position:
    symbol: str
    qty: float
    cost_basis: float                       # per share
    price: float                            # current spot
    date_acquired: Optional[datetime.date] = None

    @property
    def contracts(self) -> int:
        """Whole covered contracts this share lot supports (100 sh each)."""
        return int(self.qty // SHARES_PER_CONTRACT)

    @property
    def unrealized_pct(self) -> Optional[float]:
        if not self.cost_basis:
            return None
        return (self.price - self.cost_basis) / self.cost_basis * 100.0


@dataclass
class Levels:
    stop: Optional[float] = None            # AETHER swing-low / ATR stop
    target: Optional[float] = None          # AETHER swing-high resistance


@dataclass
class OptionQuote:
    option_type: str                        # "CALL" | "PUT"
    strike: float
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    delta: Optional[float] = None
    expiry: Optional[datetime.date] = None
    open_interest: int = 0
    volume: int = 0
    in_the_money: Optional[bool] = None

    @property
    def mid(self) -> float:
        if self.bid and self.ask:
            return round((self.bid + self.ask) / 2.0, 2)
        return self.last or self.ask or self.bid


@dataclass
class StrategyLeg:
    action: str                             # "buy" | "sell"
    option_type: str                        # "CALL" | "PUT"
    strike: float
    price: float                            # per-share premium used
    contracts: int
    expiry: Optional[datetime.date] = None

    def cash(self) -> float:
        """Signed cash flow for this leg (+ received, - paid), whole position."""
        sign = 1.0 if self.action == "sell" else -1.0
        return sign * self.price * SHARES_PER_CONTRACT * self.contracts


@dataclass
class Strategy:
    name: str
    kind: str                               # slug: collar / protective_put / ...
    legs: list = field(default_factory=list)
    net_cost: float = 0.0                   # >0 debit (you pay), <0 credit (you receive)
    max_loss: Optional[float] = None        # from current spot, whole position ($)
    max_gain: Optional[float] = None        # from current spot, whole position ($)
    downside_floor: Optional[float] = None  # effective per-share floor price
    upside_cap: Optional[float] = None      # effective per-share cap price
    breakevens: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    tax_flags: list = field(default_factory=list)


@dataclass
class AdviserReport:
    position: Position
    levels: Levels
    spot: float
    expiry: Optional[datetime.date]
    strategies: list = field(default_factory=list)
    generated_at: Optional[datetime.date] = None
    data_source: str = "offline"            # "offline" | "live"


# ---------------------------------------------------------------------------
# Chain normalization + selection  (pure)
# ---------------------------------------------------------------------------

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _ed_to_date(ed) -> Optional[datetime.date]:
    if not isinstance(ed, dict):
        return None
    try:
        return datetime.date(int(ed["year"]), int(ed["month"]), int(ed["day"]))
    except (KeyError, TypeError, ValueError):
        return None


def _one_option(raw: dict, expiry: Optional[datetime.date]) -> Optional[OptionQuote]:
    if not isinstance(raw, dict):
        return None
    greeks = raw.get("OptionGreeks") or {}
    otype = str(raw.get("optionType", "")).upper()
    if otype not in ("CALL", "PUT"):
        return None
    itm = raw.get("inTheMoney")
    if isinstance(itm, str):
        itm = itm.strip().lower() in ("y", "yes", "true", "1")
    return OptionQuote(
        option_type=otype,
        strike=_num(raw.get("strikePrice")),
        bid=_num(raw.get("bid")),
        ask=_num(raw.get("ask")),
        last=_num(raw.get("lastPrice")),
        delta=(_num(greeks.get("delta")) if greeks.get("delta") is not None else None),
        expiry=expiry,
        open_interest=int(_num(raw.get("openInterest"))),
        volume=int(_num(raw.get("volume"))),
        in_the_money=itm,
    )


def normalize_chain(raw_json: dict) -> list:
    """Flatten an E*TRADE ``OptionChainResponse`` into a list of ``OptionQuote``.

    Defensive by design — the live shape is not yet verified against the broker, so
    every field access tolerates absence, and ``OptionPair`` may arrive as a single
    dict rather than a list.
    """
    resp = (raw_json or {}).get("OptionChainResponse", raw_json or {})
    expiry = _ed_to_date(resp.get("SelectedED"))
    out: list = []
    for pair in _as_list(resp.get("OptionPair")):
        if not isinstance(pair, dict):
            continue
        for key in ("Call", "Put"):
            q = _one_option(pair.get(key), expiry)
            if q is not None:
                out.append(q)
    return out


def _side(quotes: list, option_type: str) -> list:
    return [q for q in quotes if q.option_type == option_type.upper()]


def nearest_strike(quotes: list, price: float, option_type: str) -> Optional[OptionQuote]:
    side = _side(quotes, option_type)
    if not side or price is None:
        return None
    return min(side, key=lambda q: abs(q.strike - price))


def by_delta(quotes: list, target_delta: float, option_type: str) -> Optional[OptionQuote]:
    side = [q for q in _side(quotes, option_type) if q.delta is not None]
    if not side:
        return None
    return min(side, key=lambda q: abs(abs(q.delta) - abs(target_delta)))


# ---------------------------------------------------------------------------
# Strategy builders  (pure)
# ---------------------------------------------------------------------------

def _round(x):
    return None if x is None else round(x, 2)


def build_protective_put(pos: Position, levels: Levels, quotes: list) -> Optional[Strategy]:
    contracts = max(pos.contracts, 0)
    if contracts < 1:
        return None
    anchor = levels.stop or pos.price
    put = nearest_strike(quotes, anchor, "PUT")
    if put is None:
        return None
    prem = put.ask or put.mid
    leg = StrategyLeg("buy", "PUT", put.strike, prem, contracts, put.expiry)
    shares = contracts * SHARES_PER_CONTRACT
    # Loss from current spot if stock is put away at the strike, plus premium paid.
    max_loss = ((pos.price - put.strike) + prem) * shares
    floor = put.strike - prem
    s = Strategy(
        name="Protective put", kind="protective_put", legs=[leg],
        net_cost=-leg.cash(), max_loss=_round(max_loss), max_gain=None,
        downside_floor=_round(floor), upside_cap=None,
        breakevens=[_round(pos.price + prem)],
        notes=[f"Floors the exit at ~${floor:,.2f} (strike {put.strike:g} − ${prem:.2f} premium); "
               f"upside stays open. Covers {shares:g} sh."],
    )
    return s


def build_covered_call(pos: Position, levels: Levels, quotes: list) -> Optional[Strategy]:
    contracts = max(pos.contracts, 0)
    if contracts < 1:
        return None
    anchor = levels.target or pos.price
    call = nearest_strike(quotes, anchor, "CALL")
    if call is None:
        return None
    prem = call.bid or call.mid
    leg = StrategyLeg("sell", "CALL", call.strike, prem, contracts, call.expiry)
    shares = contracts * SHARES_PER_CONTRACT
    # Gain from current spot when called away — holds for both OTM and ITM strikes
    # (an ITM strike below spot honestly yields a smaller gain, since intrinsic is
    # already spent capping below the current price). Same form as the collar leg.
    max_gain = ((call.strike - pos.price) + prem) * shares
    s = Strategy(
        name="Covered call", kind="covered_call", legs=[leg],
        net_cost=-leg.cash(), max_loss=None, max_gain=_round(max_gain),
        downside_floor=None, upside_cap=_round(call.strike + prem),
        breakevens=[_round(pos.price - prem)],
        notes=[f"Collects ${prem:.2f}/sh income, caps upside at ~${call.strike:g}; "
               f"downside cushion of ${prem:.2f}/sh only. Covers {shares:g} sh."],
    )
    return s


def build_collar(pos: Position, levels: Levels, quotes: list) -> Optional[Strategy]:
    contracts = max(pos.contracts, 0)
    if contracts < 1:
        return None
    put = nearest_strike(quotes, levels.stop or pos.price, "PUT")
    call = nearest_strike(quotes, levels.target or pos.price, "CALL")
    if put is None or call is None:
        return None
    put_prem = put.ask or put.mid
    call_prem = call.bid or call.mid
    put_leg = StrategyLeg("buy", "PUT", put.strike, put_prem, contracts, put.expiry)
    call_leg = StrategyLeg("sell", "CALL", call.strike, call_prem, contracts, call.expiry)
    shares = contracts * SHARES_PER_CONTRACT
    net_per_share = put_prem - call_prem            # >0 debit, <0 credit
    net_cost = net_per_share * shares
    max_loss = ((pos.price - put.strike) + net_per_share) * shares
    max_gain = ((call.strike - pos.price) - net_per_share) * shares
    floor = put.strike - net_per_share
    cap = call.strike - net_per_share
    kind = "credit" if net_per_share < 0 else "debit"
    s = Strategy(
        name="Collar", kind="collar", legs=[put_leg, call_leg],
        net_cost=_round(net_cost), max_loss=_round(max_loss), max_gain=_round(max_gain),
        downside_floor=_round(floor), upside_cap=_round(cap),
        breakevens=[_round(floor), _round(cap)],
        notes=[f"Brackets the position between ~${put.strike:g} (floor) and ~${call.strike:g} (cap) "
               f"for a net {kind} of ${abs(net_per_share):.2f}/sh. Covers {shares:g} sh."],
    )
    return s


def build_cash_secured_put(pos: Position, levels: Levels, quotes: list,
                           contracts: int = 1) -> Optional[Strategy]:
    """Sell a put at/below the stop to *add* shares at a discount (or keep premium).

    Sized independently of the existing lot (default 1 contract, illustrative)."""
    contracts = max(int(contracts), 1)
    anchor = levels.stop or pos.price
    put = nearest_strike(quotes, anchor, "PUT")
    if put is None:
        return None
    prem = put.bid or put.mid
    leg = StrategyLeg("sell", "PUT", put.strike, prem, contracts, put.expiry)
    shares = contracts * SHARES_PER_CONTRACT
    effective_cost = put.strike - prem
    max_gain = prem * shares                          # keep the premium, unassigned
    max_loss = effective_cost * shares                # stock -> 0 after assignment
    s = Strategy(
        name="Cash-secured put", kind="cash_secured_put", legs=[leg],
        net_cost=-leg.cash(), max_loss=_round(max_loss), max_gain=_round(max_gain),
        downside_floor=None, upside_cap=None,
        breakevens=[_round(effective_cost)],
        notes=[f"Collects ${prem:.2f}/sh now; if assigned, adds {shares:g} sh at an effective "
               f"~${effective_cost:,.2f} (needs ${put.strike * shares:,.0f} cash secured)."],
    )
    return s


# ---------------------------------------------------------------------------
# Tax considerations  (pure)  — see TAX_DISCLAIMER
# ---------------------------------------------------------------------------

def _holding(pos: Position, today: datetime.date):
    if pos.date_acquired is None:
        return None, None
    days = (today - pos.date_acquired).days
    return days, days > LONG_TERM_DAYS


def tax_considerations(strategy: Strategy, pos: Position, spot: float,
                       today: datetime.date) -> list:
    """Return a list of plain-language tax *considerations* for ``strategy``.

    Conservative and additive — each entry flags something to check with a CPA; none
    is a determination. See ``TAX_DISCLAIMER``.
    """
    flags: list = []
    days, is_long = _holding(pos, today)
    appreciated = spot > pos.cost_basis if pos.cost_basis else False

    if days is None:
        flags.append("Acquisition date unknown — holding period (LTCG vs STCG) can't be "
                     "determined; confirm the lot's purchase date.")
    else:
        term = "long-term" if is_long else "short-term"
        code = "LTCG/LTCL" if is_long else "STCG/STCL"
        flags.append(f"Position is {term} today ({days}d held). A forced sale "
                     f"(exercise/assignment) realizes {term} treatment ({code}).")
        if not is_long and (LONG_TERM_DAYS - days) <= _NEAR_LT_DAYS:
            flags.append(f"Only {LONG_TERM_DAYS - days}d from long-term treatment — an option "
                         "that hastens a sale (or tolls the holding period) can lock in STCG.")

    kind = strategy.kind
    # Extract the option legs by side for the structure-specific rules.
    put = next((leg for leg in strategy.legs if leg.option_type == "PUT"), None)
    call = next((leg for leg in strategy.legs
                 if leg.option_type == "CALL" and leg.action == "sell"), None)

    if kind == "collar" and put is not None and call is not None and appreciated:
        band = (call.strike - put.strike) / spot if spot else 1.0
        if put.strike >= pos.cost_basis and band < _COLLAR_TIGHT_BAND:
            flags.append(f"§1259 constructive-sale risk: this collar is tight (band "
                         f"{band*100:.0f}% of spot) on an appreciated lot and removes most "
                         "risk/reward — the IRS may treat it as a deemed sale, taxing the gain "
                         "now. Widen the band or check §1259 before placing.")
        else:
            flags.append("§1259: collar band looks wide enough to likely avoid constructive-sale "
                         "treatment, but confirm it retains meaningful risk & upside.")

    if call is not None:  # covered call, standalone or the short leg of a collar
        itm = call.strike < spot
        dte = (call.expiry - today).days if call.expiry else None
        near_dated = dte is not None and dte <= _QCC_MIN_DTE
        if itm or near_dated:
            why = []
            if itm:
                why.append("in-the-money")
            if near_dated:
                why.append(f"{dte}d to expiry ≤ {_QCC_MIN_DTE}d")
            flags.append("§1092 qualified-covered-call: this short call may be *unqualified* ("
                         + ", ".join(why) + ") — an unqualified call can suspend the shares' "
                         "holding period, converting a would-be LTCG into STCG. Check the QCC "
                         "strike/expiry table for this price.")

    if kind == "cash_secured_put" and pos.cost_basis and spot < pos.cost_basis:
        flags.append("§1091 wash sale: if you've recently sold this name at a loss, selling a put "
                     "(a contract to reacquire) can trigger the wash-sale rule and defer that loss.")

    return flags


# ---------------------------------------------------------------------------
# Report assembly  (pure)
# ---------------------------------------------------------------------------

def build_report(position: Position, levels: Levels, quotes: list, spot: float,
                 expiry: Optional[datetime.date] = None,
                 today: Optional[datetime.date] = None,
                 data_source: str = "offline") -> AdviserReport:
    """Assemble the full four-strategy menu with tax considerations attached."""
    today = today or datetime.date.today()
    builders = (build_collar, build_protective_put, build_covered_call, build_cash_secured_put)
    strategies: list = []
    for b in builders:
        s = b(position, levels, quotes)
        if s is None:
            continue
        s.tax_flags = tax_considerations(s, position, spot, today)
        strategies.append(s)
    if expiry is None and quotes:
        expiry = next((q.expiry for q in quotes if q.expiry), None)
    return AdviserReport(
        position=position, levels=levels, spot=spot, expiry=expiry,
        strategies=strategies, generated_at=today, data_source=data_source,
    )


# ---------------------------------------------------------------------------
# Chain-fetch adapter  (the only network I/O)
# ---------------------------------------------------------------------------

def normalize_expiries(raw_json: dict) -> list:
    """Flatten an E*TRADE ``OptionExpireDateResponse`` into sorted ``datetime.date``.

    Shape-tolerant like ``normalize_chain`` — ``ExpirationDate`` may arrive as a single
    dict or a list, and the wrapper key may be absent. Duplicates are collapsed.
    """
    resp = (raw_json or {}).get("OptionExpireDateResponse", raw_json or {})
    dates = []
    for ed in _as_list(resp.get("ExpirationDate")):
        d = _ed_to_date(ed)
        if d is not None:
            dates.append(d)
    return sorted(set(dates))


def select_expiry(expiries: list, today: Optional[datetime.date] = None, *,
                  min_dte: int = _MIN_DTE,
                  target_dte: int = _TARGET_DTE) -> Optional[datetime.date]:
    """Pick the listed expiry nearest the target holding window.

    Among expiries at least ``min_dte`` days out, return the one whose DTE is closest to
    ``target_dte`` (ties broken toward the nearer date). If none reach ``min_dte`` (only
    very short-dated expiries are listed), fall back to the furthest-dated available so
    the caller never lands on a degenerate 1-DTE front-month menu. ``None`` for empty.

    ``min_dte`` is a **hard floor** and wins when it conflicts with ``target_dte`` (a
    caller wanting a shorter window than the floor must lower ``min_dte`` too — the CLI
    does this via ``min(_MIN_DTE, --dte)``).
    """
    today = today or datetime.date.today()
    future = [d for d in expiries if (d - today).days >= 0]
    pool = future or list(expiries)
    if not pool:
        return None
    eligible = [d for d in pool if (d - today).days >= min_dte]
    if eligible:
        return min(eligible, key=lambda d: (abs((d - today).days - target_dte),
                                            (d - today).days))
    return max(pool, key=lambda d: (d - today).days)


def fetch_chain(symbol: str, client, expiry: Optional[datetime.date] = None,
                strikes: int = 12, *, today: Optional[datetime.date] = None,
                min_dte: int = _MIN_DTE, target_dte: int = _TARGET_DTE):
    """Fetch + normalize a live chain via ``ETradeClient.market``.

    Returns ``(quotes, spot, expiry)``. When ``expiry`` is ``None`` the adapter first
    lists the symbol's available expiries (``option_expiry_dates``) and auto-selects one
    near ``target_dte`` (>= ``min_dte``) — avoiding the broker's default front-month,
    which is often 1-DTE and collapses the collar's put/call onto a single strike. A
    pinned ``expiry`` skips that lookup. The response shape is not yet fully verified
    against the live broker — ``normalize_chain``/``normalize_expiries`` are defensive,
    and the live-validation step exists to confirm/repair the field paths.
    """
    if expiry is None:
        try:
            raw_exp = client.market.option_expiry_dates(symbol, resp_format="json")
            expiry = select_expiry(normalize_expiries(raw_exp), today,
                                   min_dte=min_dte, target_dte=target_dte)
            if expiry is None:
                # Empty or unrecognized expiry list (e.g. the live shape differs from
                # normalize_expiries' assumptions) — make the silent degradation loud.
                _log.warning(
                    "No expiry >= %d DTE resolved for %s (empty/unrecognized expiry "
                    "list); using the broker's front expiry — the menu may collapse "
                    "onto the front month.", min_dte, symbol)
        except Exception as e:
            _log.warning("Expiry auto-select failed (%s); falling back to the "
                         "broker's front expiry.", e)
            expiry = None      # fall back to the broker's default (front) expiry
    raw = client.market.options_chain(
        symbol, expiry=expiry, chain_type="callput", no_of_strikes=strikes,
        resp_format="json",
    )
    quotes = normalize_chain(raw)
    spot = None
    try:
        q = client.market.quotes([symbol])
        spot = _extract_spot(q)
    except Exception:
        spot = None
    resolved_expiry = expiry or next((qt.expiry for qt in quotes if qt.expiry), None)
    return quotes, spot, resolved_expiry


def _extract_spot(quote_resp) -> Optional[float]:
    """Best-effort last price out of a pyetrade quote payload (shape-tolerant)."""
    try:
        qd = (quote_resp.get("QuoteResponse", {}).get("QuoteData", []))
        qd = qd[0] if isinstance(qd, list) else qd
        return _num(qd.get("All", {}).get("lastTrade")) or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Renderers  (thin, over AdviserReport)
# ---------------------------------------------------------------------------

def _fmt(x, money=True):
    if x is None:
        return "—"
    return f"${x:,.2f}" if money else f"{x:g}"


def render_terminal(report: AdviserReport) -> str:
    p = report.position
    lines = []
    lines.append(f"# Options adviser — {p.symbol}  ({report.data_source} data)")
    up = p.unrealized_pct
    lines.append(
        f"Position: {p.qty:g} sh @ ${p.cost_basis:,.2f}  |  spot ${report.spot:,.2f}"
        + (f"  ({up:+.1f}%)" if up is not None else "")
        + f"  |  acquired {p.date_acquired or 'unknown'}"
    )
    lines.append(
        f"AETHER levels: stop {_fmt(report.levels.stop)} · target {_fmt(report.levels.target)}"
        f"  |  expiry {report.expiry or '—'}"
    )
    lines.append("")
    if not report.strategies:
        lines.append("_No strategies could be built — check position size (≥100 sh for covered "
                     "structures) and chain coverage._")
        return "\n".join(lines)

    lines.append("| Strategy | Legs | Net | Max loss | Max gain | Floor | Cap |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in report.strategies:
        legs = "; ".join(
            f"{leg.action} {leg.option_type[:1]}{leg.strike:g}@${leg.price:.2f}×{leg.contracts}"
            for leg in s.legs
        )
        net = ("credit " + _fmt(abs(s.net_cost))) if s.net_cost < 0 else ("debit " + _fmt(s.net_cost))
        lines.append(
            f"| {s.name} | {legs} | {net} | {_fmt(s.max_loss)} | {_fmt(s.max_gain)} "
            f"| {_fmt(s.downside_floor)} | {_fmt(s.upside_cap)} |"
        )
    lines.append("")
    for s in report.strategies:
        lines.append(f"## {s.name}")
        for n in s.notes:
            lines.append(f"- {n}")
        if s.breakevens:
            lines.append(f"- Breakeven(s): {', '.join(_fmt(b) for b in s.breakevens if b is not None)}")
        if s.tax_flags:
            lines.append("- **Tax considerations:**")
            for t in s.tax_flags:
                lines.append(f"    - {t}")
        lines.append("")
    lines.append(f"> {TAX_DISCLAIMER}")
    return "\n".join(lines)


def render_html(report: AdviserReport) -> str:
    p = report.position
    up = p.unrealized_pct
    css = (
        "font-family:Segoe UI,Arial,sans-serif;color:#1a1a1a;max-width:820px;margin:auto;"
    )
    rows = []
    for s in report.strategies:
        legs = "<br>".join(
            f"{leg.action} {leg.option_type} {leg.strike:g} @ ${leg.price:.2f} ×{leg.contracts}"
            for leg in s.legs
        )
        net = (f"<span style='color:#127a2e'>credit {_fmt(abs(s.net_cost))}</span>"
               if s.net_cost < 0 else f"<span style='color:#b23'>debit {_fmt(s.net_cost)}</span>")
        tax = "".join(f"<li>{t}</li>" for t in s.tax_flags)
        rows.append(
            f"<div style='border:1px solid #e0e0e0;border-radius:10px;padding:14px 16px;margin:12px 0'>"
            f"<h3 style='margin:0 0 6px'>{s.name} <span style='font-weight:400;font-size:13px'>({net})</span></h3>"
            f"<div style='font-size:13px;color:#444'>{legs}</div>"
            f"<table style='font-size:13px;margin:8px 0;border-collapse:collapse'>"
            f"<tr><td style='padding:2px 12px 2px 0'>Max loss</td><td>{_fmt(s.max_loss)}</td>"
            f"<td style='padding:2px 12px'>Max gain</td><td>{_fmt(s.max_gain)}</td></tr>"
            f"<tr><td style='padding:2px 12px 2px 0'>Floor</td><td>{_fmt(s.downside_floor)}</td>"
            f"<td style='padding:2px 12px'>Cap</td><td>{_fmt(s.upside_cap)}</td></tr></table>"
            + (f"<div style='font-size:12.5px;color:#555'><b>Tax considerations</b>"
               f"<ul style='margin:4px 0 0 18px;padding:0'>{tax}</ul></div>" if tax else "")
            + "</div>"
        )
    body = "".join(rows) or "<p><i>No strategies could be built for this position/chain.</i></p>"
    return (
        f"<div style='{css}'>"
        f"<h2 style='margin-bottom:2px'>🛡️ Options adviser — {p.symbol}</h2>"
        f"<div style='color:#666;font-size:13px'>{report.data_source} data · "
        f"generated {report.generated_at}</div>"
        f"<p style='font-size:14px'>{p.qty:g} sh @ ${p.cost_basis:,.2f} · spot ${report.spot:,.2f}"
        + (f" ({up:+.1f}%)" if up is not None else "")
        + f" · stop {_fmt(report.levels.stop)} · target {_fmt(report.levels.target)}"
        f" · expiry {report.expiry or '—'}</p>"
        f"{body}"
        f"<p style='font-size:11.5px;color:#888;border-top:1px solid #eee;padding-top:8px'>"
        f"{TAX_DISCLAIMER}</p></div>"
    )
