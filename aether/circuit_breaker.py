"""
Project AETHER: Systemic Crash "Circuit Breaker" (Systemic Risk Gate).

Audits broad market conditions (SPY single-day return, rolling 10-day drawdown,
VXX Volatility ETF proxy, and opening stabilization windows) to dynamically
freeze buying, tighten stop-losses, and prevent whipsaws on idiosyncratic
single-stock gap-downs. Features AETHER Elastic Memory to automatically cache and 
restore stop-losses to their original wide levels once market panic stabilizes,
and backfeeds systemic trigger events to the Failure DNA Ledger for weekly retrospectives.
"""

import json
import datetime
import os
import pytz
from pathlib import Path
from aether import ledgers, paths, risk_utils
from aether.logger import get_logger as _get_logger

_log = _get_logger("circuit_breaker")

BASE_DIR = Path(__file__).resolve().parent.parent
SPY_FILE = Path(paths.ohlcv_dir()) / "SPY_daily.json"   # $AETHER_CACHE_DIR-aware (aether.paths)
VXX_FILE = Path(paths.ohlcv_dir()) / "VXX_daily.json"

# Transient key on the run's state: the breaker's verdict for the buy stage (deploy_capital), so it
# acts on the same check with no second market-data read. finalize_run removes it before saving.
FREEZE_KEY = "_breaker_freeze"
# The breaker's verdict of its last run, in its own file (ledgers.BREAKER_LAST_RUN_FILE, this module is the only
# writer) so reports can say whether the breaker was on (owner 2026-11-25: the email said "AGGRESSIVE" while the
# breaker had frozen buys and tightened stops). Not in the game state: a market-wide verdict is not portfolio data
# (#326 review; the #288 -> #297 pattern).


def _save_record(record: dict) -> None:
    p = Path(ledgers.BREAKER_LAST_RUN_FILE)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        _log.warning(f"Could not save the breaker's run record: {e}")


def load_record() -> dict:
    try:
        return json.loads(Path(ledgers.BREAKER_LAST_RUN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def load_spy_history() -> list[dict]:
    """Load sorted daily price series for the SPY ETF from the local cache."""
    if not SPY_FILE.exists():
        return []
    try:
        with open(SPY_FILE, "r") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        # Parse into a sorted list of daily records
        series = []
        for d in sorted(ts.keys()):
            try:
                series.append({
                    "date": d,
                    "close": float(ts[d]["4. close"]),
                    "open": float(ts[d]["1. open"]),
                    "high": float(ts[d]["2. high"]),
                    "low": float(ts[d]["3. low"]),
                })
            except (ValueError, KeyError):
                continue
        return series
    except Exception as e:
        _log.warning(f"Failed to load SPY history for circuit breaker: {e}")
        return []

def _today_et() -> str:
    return datetime.datetime.now(pytz.timezone("America/New_York")).date().isoformat()


def prev_session_close(ts: dict, today: str | None = None) -> float | None:
    """The close of the last bar dated before ``today`` (ET): the session a live price is compared with.
    A morning run's cache ends yesterday, so that is the last bar; an evening cache already holds today's bar,
    so it is the one before it. (Before 2026-11-24 it was always the second-to-last bar: two sessions back on
    a morning run, the same off-by-one as the SPY move; #318 review.)"""
    today = today or _today_et()
    before = [d for d in sorted(ts) if d < today]
    if not before:
        return None
    try:
        return float(ts[before[-1]]["4. close"])
    except (KeyError, TypeError, ValueError):
        return None


def load_vxx_prev_close() -> float | None:
    """The previous session's VXX close from the local cache (prev_session_close). None if unavailable
    (disables the VXX check)."""
    if not VXX_FILE.exists():
        return None
    try:
        with open(VXX_FILE, "r") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
        return prev_session_close(ts)
    except Exception:
        return None

def is_market_opening_window() -> bool:
    """True if we are within the 30-minute opening stabilization window (9:30 AM - 10:00 AM EST)."""
    tz = pytz.timezone("America/New_York")
    now = datetime.datetime.now(tz)
    
    # Exclude weekends
    if now.weekday() >= 5:
        return False
        
    opening_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    opening_end = now.replace(hour=10, minute=0, second=0, microsecond=0)
    
    return opening_start <= now <= opening_end

def is_single_stock_gap_frozen(symbol: str, live_price: float, prev_close: float) -> bool:
    """True if an open position has gapped down > 8% at the open, holding stop wide to prevent whipsaw."""
    if not is_market_opening_window():
        return False
        
    if not prev_close or prev_close <= 0 or not live_price or live_price <= 0:
        return False
        
    gap_pct = ((live_price - prev_close) / prev_close) * 100
    return gap_pct <= -8.0

DAY_DROP_PCT = -2.0          # single-day capitulation
DRAWDOWN_PCT = -5.0          # rolling 10-day drawdown
DRAWDOWN_WINDOW = 10
VXX_SURGE_PCT = 15.0


def breaker_measures(closes: list, live: float | None = None, last_is_today: bool = True) -> tuple[float, float]:
    """(one-day return %, drawdown % from the 10-session peak) the way the breaker measures them.

    ``closes``: SPY daily closes, oldest first. ``live``: today's live price, if any.
    ``last_is_today``: the last close is today's own bar, so ``live`` replaces it. When it is an earlier session
    (the 07:00 run reads a cache that ends yesterday), ``live`` is today's price and is compared with that close.
    Before 2026-11-24 the live price always replaced the last close and the one-day move was measured against the
    close before it, i.e. two sessions back on a morning run: on 11-24 that read Friday's 689.81 for a day that
    opened 1.1% under Monday's 722.29 (+3.5% instead of -1.1%)."""
    s = [float(c) for c in closes]
    if live and live > 0:
        if last_is_today and s:
            s[-1] = float(live)
        else:
            s.append(float(live))
    if len(s) < 2:
        return 0.0, 0.0
    day = (s[-1] / s[-2] - 1) * 100 if s[-2] else 0.0
    peak = max(s[-DRAWDOWN_WINDOW:])
    return day, (s[-1] / peak - 1) * 100 if peak else 0.0


def breaker_reason(day_pct: float, drawdown_pct: float, vxx_pct: float = 0.0) -> str | None:
    """The active-session trigger, or None. One home for the thresholds (the G1 study replays this)."""
    if day_pct <= DAY_DROP_PCT:
        return f"Single-Day Capitulation (SPY fell {day_pct:+.2f}% in a single session)."
    if drawdown_pct <= DRAWDOWN_PCT:
        return f"Rolling 10-day Drawdown Breach (SPY is down {drawdown_pct:+.2f}% from its 10-day peak)."
    if vxx_pct >= VXX_SURGE_PCT:
        return f"Volatility Capitulation (VXX Volatility ETF surged {vxx_pct:+.2f}% on systemic panic)."
    return None


def check_systemic_risk(prices=None) -> tuple[bool, str]:
    """
    Audit systemic risk factors to determine if the Circuit Breaker is active.
    Returns (is_active, reason).
    """
    # When a live prices dict is supplied (even empty), SPY must be present and positive.
    # prices=None means "no live feed, fall back to cached history" — no freeze.
    live_spy_price = prices.get("SPY") if prices is not None else None
    if prices is not None and not (live_spy_price and live_spy_price > 0):
        _log.warning("CRITICAL WARNING: Unable to fetch live price for SPY! Defensively triggering Caution Freeze.")
        return True, "Data Feed Outage: Live SPY price unavailable. Caution freeze active."

    spy_series = load_spy_history()
    if not spy_series:
        return False, ""
        
    if len(spy_series) < 15:  # need at least 15 days for rolling drawdown check
        return False, ""
        
    # 1. Single-day move and 2. rolling 10-day drawdown, measured once (breaker_measures). A bar without a date
    #    keeps the old reading (the live price replaces it); a bar dated before today is an earlier session.
    today_et = _today_et()
    last_date = spy_series[-1].get("date")
    spy_return, spy_drawdown = breaker_measures([r["close"] for r in spy_series], live_spy_price,
                                                last_is_today=(last_date is None or last_date >= today_et))

    # VXX Volatility Proxy: surge > +15% signals systemic panic
    vxx_prev = load_vxx_prev_close()
    vxx_price = (prices or {}).get("VXX") if prices else None
    vxx_return = ((vxx_price - vxx_prev) / vxx_prev) * 100 if (vxx_price and vxx_prev) else 0.0
    volatility_capitulation = vxx_return >= 15.0
    
    # 3. Apply Whipsaw Protection (Opening Stabilization Window)
    # If the market opened with a gap-down, we freeze buying immediately,
    # but we DO NOT tighten stops or execute panic-sells during the first 30 minutes!
    if is_market_opening_window():
        if spy_return <= DAY_DROP_PCT or spy_drawdown <= DRAWDOWN_PCT or volatility_capitulation:
            vxx_desc = f", VXX: {vxx_return:+.1f}%" if volatility_capitulation else ""
            return True, f"Market Open Freeze (SPY: {spy_return:+.2f}%, Drawdown: {spy_drawdown:+.2f}%{vxx_desc}). Whipsaw protection active: stops held wide."
        return False, ""

    # 4. Enforce Systemic Rejection Triggers (Active Session after 10:00 AM EST)
    reason = breaker_reason(spy_return, spy_drawdown, vxx_return if volatility_capitulation else 0.0)
    return (True, reason) if reason else (False, "")

def breaker_note(today: str | None = None) -> str | None:
    """One line for reports when the breaker was on in today's run, else None."""
    rec = load_record()
    if not rec.get("on") or rec.get("date") != (today or _today_et()):
        return None
    note = f"Circuit breaker ON this run: {rec.get('reason') or 'no reason recorded'}. New buys and scale-ins frozen"
    if rec.get("tightened"):
        note += f"; stops tightened to 1x ATR on {', '.join(rec['tightened'])}"
    return note + "."


def log_circuit_breaker_trigger_dna(reason: str, state: dict, prices=None, _spy_series=None):
    """Backfeed the systemic trigger event directly into the unified Trade History DNA Ledger."""
    dna_file = ledgers.TRADE_DNA_FILE
    dna_file.parent.mkdir(parents=True, exist_ok=True)
    today = str(datetime.date.today())

    records = []
    if dna_file.exists():
        try:
            with open(dna_file, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception:
            records = []

    for r in records:  # deduplicate: only one trigger per day
        if isinstance(r, dict) and r.get("type") == "CIRCUIT_BREAKER_TRIGGER" and r.get("date") == today:
            return

    spy_series = _spy_series if _spy_series is not None else load_spy_history()
    spy_return = 0.0
    if spy_series and len(spy_series) >= 2:
        last_date = spy_series[-1].get("date")
        spy_return = round(breaker_measures([r["close"] for r in spy_series], (prices or {}).get("SPY"),
                                            last_is_today=(last_date is None or last_date >= _today_et()))[0], 2)

    vxx_prev = load_vxx_prev_close()
    live_vxx = (prices or {}).get("VXX") if vxx_prev else None
    vxx_return = round(((live_vxx - vxx_prev) / vxx_prev) * 100, 2) if (vxx_prev and live_vxx) else 0.0
    
    open_positions = [
        {"symbol": sym, "cost": pos.get("cost", 0.0),
         "stop_loss": pos.get("stop_loss", 0.0), "is_scarcity": pos.get("is_scarcity", False)}
        for sym, pos in state.get("positions", {}).items()
    ]
    record = {
        "type": "CIRCUIT_BREAKER_TRIGGER",
        "date": today,
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
        "reason": reason,
        "spy_return_pct": spy_return,
        "vxx_return_pct": vxx_return,
        "portfolio_equity": state.get("equity", 0.0),
        "cash_balance": state.get("balance", 0.0),
        "open_positions": open_positions,
        "profile": state.get("profile", "DEFENSIVE")
    }
    
    records.append(record)
    
    try:
        with open(dna_file, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=4)
        _log.warning(f"  [Breaker Backfeed] Logged systemic trigger DNA to unified ledger (trade_history_dna.json)!")
    except Exception as e:
        _log.error(f"Failed to log circuit breaker trigger DNA: {e}")

def enforce_circuit_breaker(state, prices=None) -> list[str]:
    """
    If the Circuit Breaker is active, dynamically freeze buying and tighten stop-losses
    on all active positions to protect capital.
    If the Circuit Breaker is inactive, automatically restore any previously tightened stops.
    """
    is_active, reason = check_systemic_risk(prices)
    positions = state.get("positions", {})
    record = {"date": _today_et(), "on": bool(is_active), "reason": reason or None, "tightened": []}
    if is_active:
        state[FREEZE_KEY] = reason     # the buy stage freezes new buys and scale-ins on this
    else:
        state.pop(FREEZE_KEY, None)
    
    if not is_active:
        # AETHER Elastic Memory: If the breaker is inactive, restore original wide stops cleanly!
        restored_symbols = []
        # Restore only a stop the breaker still owns. If a ratchet (profit-lock, breakeven) raised it
        # after the tightening, keep that higher stop: restoring would loosen it (PROD review 11-09).
        # A position without breaker_stop_loss predates this record and is restored as before.
        kept_symbols = []
        for sym, pos in positions.items():
            if "original_stop_loss" in pos:
                original = pos.pop("original_stop_loss")
                tightened = pos.pop("breaker_stop_loss", None)
                if tightened is not None and pos.get("stop_loss", 0.0) > tightened:
                    kept_symbols.append(sym)
                    continue
                pos["stop_loss"] = original
                restored_symbols.append(sym)

        if restored_symbols:
            _log.info(f"  [Breaker Recovery] Market stabilized. Restored stop-losses to original levels on: {', '.join(restored_symbols)}")
        if kept_symbols:
            _log.info(f"  [Breaker Recovery] Kept stops raised after the tightening (not restored): {', '.join(kept_symbols)}")
        _save_record(record)
        return []
        
    _log.warning(f"CIRCUIT BREAKER TRIGGERED! Reason: {reason}")
    log_circuit_breaker_trigger_dna(reason, state, prices, _spy_series=load_spy_history())
    
    # 1. Freeze all buying (clears any queued buy orders)
    queued = state.get("queued_orders", [])
    original_count = len(queued)
    state["queued_orders"] = [q for q in queued if q.get("type") != "BUY"]
    if len(state["queued_orders"]) < original_count:
        _log.info(f"  [Breaker] Cleared {original_count - len(state['queued_orders'])} queued buy orders.")
        
    # 2. Tighten Stop-Losses on all active positions to lock in gains
    tightened_symbols = []
    if "Whipsaw protection active" not in reason and "Data Feed Outage" not in reason:
        for sym, pos in positions.items():
            if pos.get("is_scarcity", False):
                continue
            cur_stop = pos.get("stop_loss", 0.0)
            live_price = (prices or {}).get(sym) or pos.get("cost", 0.0)
            atr = risk_utils.calculate_atr(sym)
            if atr and atr > 0:
                tight_stop = round(live_price - atr, 2)
                if tight_stop > cur_stop and tight_stop < live_price:
                    if "original_stop_loss" not in pos:
                        pos["original_stop_loss"] = cur_stop
                    elif cur_stop != pos.get("breaker_stop_loss"):
                        # A ratchet raised the stop since the last tightening: restore that one later.
                        pos["original_stop_loss"] = max(pos["original_stop_loss"], cur_stop)
                    pos["stop_loss"] = tight_stop
                    pos["breaker_stop_loss"] = tight_stop
                    tightened_symbols.append(sym)

    if tightened_symbols:
        _log.warning(f"  [Breaker] Tightened stop-losses to 1.0x ATR on: {', '.join(tightened_symbols)} "
                     "(scarcity holdings are left wide)")
    record["tightened"] = list(tightened_symbols)
    _save_record(record)
    return tightened_symbols
