"""
Project AETHER Intraday Stop-Breach Monitor.
Performs 30-minute interval audits of held E*TRADE positions against their ATR-based stop-loss levels.
Includes state-persistence (cures 30-min duplicate email spam) and dynamic, AI-powered qualitative analyses.
"""
import sys
import os
import json
import datetime
from pathlib import Path
import openpyxl

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BASE_DIR))

import notify
import ai_client
from aether_logger import get_logger
from data_api import _SL  # canonical Short_Long column map (single source of truth)
from ai_portfolio_game import get_live_prices

_log = get_logger("monitor")

# Anchor state/workbook paths to the project root, not the process CWD — the
# scheduled task may launch from anywhere, and CWD-relative paths would silently
# yield "no positions found" and skip every cycle.
XLSX_FILE = _BASE_DIR / "Data" / "state_of_the_day.xlsx"
STATE_FILE = _BASE_DIR / "Data" / "intraday_monitor_state.json"

# Re-alert an existing breach only when it materially deteriorates: the price
# drops at least this fraction below the last-alerted price, or the stop moves.
# Without this, ordinary sub-percent ticks flip "changed" on every 30-min run and
# re-send the alert — the duplicate-email spam this monitor is meant to cure.
_MATERIAL_DROP_PCT = 0.01
# An alerted breach is forgotten (so a later breach alerts again) only once the price is
# this far back above the stop; a bounce around the stop must not re-send the email.
_CLEAR_MARGIN_PCT = 0.02
# A breach this deep: first seen, it is flagged as urgent; already reported under the same stop,
# the stop is probably out of date (PROD: hand-set stops 22% to 10x above the price).
_STALE_STOP_PCT = 0.20

def _today() -> str:
    return str(datetime.date.today())


def load_state() -> dict:
    """Load the last active stop-breach state from disk."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            _log.warning(f"Failed to load monitor state: {e}")
    return {"last_breached": {}}

def save_state(state: dict):
    """Save the active stop-breach state to disk atomically (temp file + replace),
    so a crash or overlapping run can't leave a truncated JSON that resets state."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        _log.warning(f"Failed to save monitor state: {e}")

def get_monitored_positions() -> list:
    """Load positions and stops from Short_Long sheet."""
    positions = []
    try:
        wb = openpyxl.load_workbook(XLSX_FILE, data_only=True, read_only=True)
        if "Short_Long" not in wb.sheetnames:
            return []
        
        ws = wb["Short_Long"]
        for row in ws.iter_rows(min_row=3, values_only=True):
            sym  = row[_SL["sym"]]  if len(row) > _SL["sym"]  else None
            stop = row[_SL["stop"]] if len(row) > _SL["stop"] else None
            if (isinstance(sym, str) and sym.strip() and sym.strip() != "Symb"
                    and isinstance(stop, (int, float)) and stop > 0):
                positions.append({"symbol": sym.strip().upper(), "stop": stop})
    except Exception as e:
        _log.error(f"Error loading positions from workbook: {e}")
    return positions

def generate_ai_analysis(sym: str, price: float, stop: float) -> str:
    """Generate a highly specific, professional quantitative analysis for the stop breach."""
    if not ai_client.primary():
        return f"ATR Stop-Loss Floor Breached. Technical momentum is currently negative, representing an immediate capital preservation exit risk."
        
    system_prompt = (
        "You are AETHER, an expert hedge-fund quantitative analyst.\n"
        "Provide a highly concise, professional 2-sentence analysis and reasoning of why this stock has hit its stop-loss, "
        "what the primary technical risk factor is, and what action the trader should take immediately. "
        "Be concise, direct, and authoritative."
    )
    user_prompt = f"Asset {sym} has breached its ATR stop-loss floor. Current Price: ${price:.2f} | Stop-Loss level: ${stop:.2f}."
    try:
        raw_reply = ai_client.evaluate(system_prompt, user_prompt, max_tokens=150, tier="cheap")
        return raw_reply.strip()
    except Exception as e:
        _log.warning(f"Failed calling AI analyzer for {sym}: {e}")
        return "ATR Stop-Loss Floor Breached. Technical momentum is currently negative, representing an immediate capital preservation exit risk."

def monitor():
    _log.console(f"[{datetime.datetime.now()}] Starting Intraday Stop Monitor...")
    monitored = get_monitored_positions()
    if not monitored:
        _log.info("No positions with valid stops found to monitor.")
        return

    _log.console(f"Monitoring {len(monitored)} positions.")

    symbols = [p["symbol"] for p in monitored]
    quotes = get_live_prices(symbols)

    # Load previous state. "alerted" remembers what each email already reported, so a
    # breach is emailed once, not again each time the price crosses back over the stop
    # (PROD 2026-11-03: 7 emails in one day, the same 9-12 breaches in each).
    state = load_state()
    last_breached = state.get("last_breached", {})
    alerted = dict(state.get("alerted", last_breached))
    alerted_before = dict(alerted)   # as of the last run; `alerted` is updated below
    blind_alerted = state.get("blind_alerted", "")

    current_breaches = {}
    missing = []
    for p in monitored:
        sym = p["symbol"]
        stop = p["stop"]
        last_price = quotes.get(sym)
        if last_price and last_price > 0:
            if last_price <= stop:
                current_breaches[sym] = {"stop": stop, "price": last_price}
        else:
            missing.append(sym)
            # No quote is not a recovery: keep a known breach as it was, so it is neither
            # "cleared" now nor re-sent as new once quotes return.
            if sym in last_breached:
                current_breaches[sym] = last_breached[sym]

    # One line for all missing quotes, and one email a day when most are missing (PROD 11-09
    # 11:15-12:16: DNS failed, and each run logged a warning per position and alerted no one).
    if missing:
        shown = ", ".join(missing[:8]) + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else "")
        _log.warning(f"Could not fetch live price for {len(missing)}/{len(monitored)} positions: {shown}")
        today = _today()
        if len(missing) * 2 >= len(monitored) and blind_alerted != today:
            try:
                notify.send_email(
                    f"AETHER ALERT: stop monitor has no prices ({len(missing)}/{len(monitored)} positions)",
                    f"The intraday stop monitor got no live price for {len(missing)} of {len(monitored)} "
                    "monitored positions, so their stops are not being checked. Check E*TRADE access "
                    "and the network on the PROD machine (the run's log shows the error).\n\n"
                    f"Missing: {', '.join(missing)}")
                blind_alerted = today
            except Exception as e:
                _log.error(f"Could not send the no-prices alert: {e}")

    # Alert a breach that is new, whose stop moved (e.g. a trailing/ratchet adjustment), or
    # whose price made a new low at least _MATERIAL_DROP_PCT below what we last alerted on.
    # A small tick, a bounce or a recovery does not send an email.
    new_breaches, modified_breaches = [], []
    for s, cur in current_breaches.items():
        prev = alerted.get(s)
        if prev is None:
            new_breaches.append(s)
        elif (abs(cur["stop"] - prev.get("stop", cur["stop"])) > 1e-9
              or cur["price"] < prev["price"] * (1 - _MATERIAL_DROP_PCT)):
            modified_breaches.append(s)
    cleared_breaches = [s for s in last_breached if s not in current_breaches]
    actionable_symbols = sorted(new_breaches + modified_breaches)

    # Forget a symbol once it is no longer monitored (sold) or has recovered clearly.
    monitored_stops = {p["symbol"]: p["stop"] for p in monitored}
    for s in list(alerted):
        px = quotes.get(s)
        if s not in monitored_stops or (px and px > monitored_stops[s] * (1 + _CLEAR_MARGIN_PCT)):
            del alerted[s]
    for s in actionable_symbols:
        alerted[s] = current_breaches[s]

    if actionable_symbols:
        diff_msgs = []
        if new_breaches:
            diff_msgs.append(f"🔴 NEW BREACHES DETECTED: {', '.join(sorted(new_breaches))}")
        if modified_breaches:
            diff_msgs.append(f"🔄 EXISTING BREACH PRICES UPDATED: {', '.join(sorted(modified_breaches))}")
        if cleared_breaches:
            diff_msgs.append(f"🟢 BREACHES CLEARED/EXITED: {', '.join(cleared_breaches)}")

        _log.warning("Stop breach state changed! Dispatching rich, AI-analyzed alert email...")

        subject = f"AETHER ALERT: Stop Breach Detected ({len(current_breaches)} Position{'s' if len(current_breaches) > 1 else ''})"

        body_parts = [
            "The following stop-loss breaches have been detected in your active E*TRADE portfolio.\n",
            "📊 DIFFERENCE SUMMARY:",
            "\n".join(f"  * {m}" for m in diff_msgs),
            "\n" + "=" * 80,
            "🔬 QUALITATIVE ANALYSIS & RISK REASONING FOR ACTIONABLE BREACHES:",
            "=" * 80 + "\n"
        ]

        for sym in actionable_symbols:
            data = current_breaches[sym]
            price = data["price"]
            stop = data["stop"]

            deep = price < stop * (1 - _STALE_STOP_PCT)
            # "Out of date" only for a breach that was ALREADY this deep at the last alert, under
            # the same stop. A first-seen deep breach, or one that was shallow at the last alert
            # and has since crashed through the 20% bar, may be a real crash and must read as the
            # most urgent case, not as a stop to delete.
            prev = alerted_before.get(sym)   # None for a new breach
            if (deep and prev and abs(prev.get("stop", stop) - stop) <= 1e-9
                    and prev.get("price", price) < stop * (1 - _STALE_STOP_PCT)):
                reasoning = (f"The price is {(1 - price / stop) * 100:.0f}% below the stop and was already "
                             "below it at the last alert, so the stop may be out of date. Update or remove "
                             "it in the workbook if it no longer applies.")
            else:
                _log.console(f"Running live AI risk analysis and reasoning for {sym}...")
                reasoning = generate_ai_analysis(sym, price, stop)
                if deep:
                    seen = "first seen this run" if sym in new_breaches else "deeper than at the last alert"
                    reasoning = (f"DEEP BREACH, {seen}: the price is {(1 - price / stop) * 100:.0f}% "
                                 f"below the stop. {reasoning}")

            body_parts.append(
                f"📈 {sym} — BREACH UPDATE!"
                f"\n  * Current Price:   ${price:.2f}"
                f"\n  * Stop-Loss Level: ${stop:.2f}"
                f"\n  * Technical Delta: {round(((price - stop)/stop)*100, 2):+.2f}%"
                f"\n  * AI Risk Analysis & Reasoning:"
                f"\n    {reasoning}"
                f"\n\n" + "-" * 80
            )

        # Include a quiet list of other active, unchanged breaches
        unchanged_breaches = [s for s in current_breaches if s not in actionable_symbols]
        if unchanged_breaches:
            body_parts.append(
                "ℹ️ OTHER UNCHANGED ACTIVE BREACHES (Already Alerted):\n" +
                ", ".join(f"{s} (${current_breaches[s]['price']:.2f})" for s in sorted(unchanged_breaches)) + "\n"
            )

        body_parts.append(
            "\nAction required: These positions are trading below their capital-preservation stop-loss levels. "
            "Please review and execute these exits manually in your broker immediately to prevent further drawdown."
        )

        notify.send_email(subject, "\n".join(body_parts))
        _log.info(f"Sent rich consolidated alert email for {len(current_breaches)} breaches.")
    elif current_breaches:
        _log.info(f"Stop breach state unchanged ({len(current_breaches)} active breaches, all already alerted). Bypassing duplicate email dispatch.")
    elif cleared_breaches:
        # If breaches exist in memory but are now cleared (e.g. they were sold!)
        _log.info(f"All stop breaches cleared (bypassed email notification to avoid noise): {', '.join(cleared_breaches)}")
    else:
        _log.info("No stop breaches detected.")

    # Save the current state for the next run
    saved = {"last_breached": current_breaches, "alerted": alerted}
    if blind_alerted:
        saved["blind_alerted"] = blind_alerted   # the day the no-prices email went out
    save_state(saved)

if __name__ == "__main__":
    monitor()
