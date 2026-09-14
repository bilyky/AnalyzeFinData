"""CLI for the options + CPA/tax adviser (default symbol: INTC).

One interface over the interface-agnostic engine in ``aether/options_adviser.py``.
Sources the position + AETHER stop/target levels, fetches an option chain (offline
sample by default, live E*TRADE with ``--live``), builds the four-strategy menu, and
delivers it — **e-mail by default** (HTML), ``--print`` for the terminal.

    python options_adviser.py                         # INTC, offline chain, e-mail
    python options_adviser.py --print --no-email      # terminal only
    python options_adviser.py --symbol AMD --print
    python options_adviser.py --live --print          # ban-safe single live fetch

The live path performs exactly one ``options_chain`` fetch through the shared,
ban-hardened token/breaker in ``aether.etrade`` — it never opens a browser. It also
serves as the validation of the still-unverified ``options_chain`` shape: if the live
payload differs from the assumed schema, ``normalize_chain`` returns nothing and this
prints a clear diagnostic rather than guessing.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import data_api
from aether import notify
from aether import options_adviser as oa
from aether.etrade.client import ETradeClient
from aether.logger import get_logger as _get_logger


_log = _get_logger("options_adviser")

_FIXTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "intc_option_chain.json"


def _research_levels(symbol: str):
    """(spot, Levels) from the Research sheet, or (None, empty) if unavailable."""
    try:
        for row in data_api.read_research().get("rows", []):
            if str(row.get("symbol", "")).upper() == symbol.upper():
                return row.get("price"), oa.Levels(stop=row.get("stop"), target=row.get("target"))
    except Exception as e:                       # data layer optional / may be stale
        _log.warning("Research sheet unavailable (%s); continuing without levels.", e)
    return None, oa.Levels()


def _portfolio_position(symbol: str, spot):
    """Position from the game portfolio if held, else an illustrative 100-share lot."""
    today = datetime.date.today()
    try:
        for p in data_api.read_portfolio().get("positions", []):
            if str(p.get("symbol", "")).upper() == symbol.upper():
                acq = today - datetime.timedelta(days=int(p.get("days_held", 0) or 0))
                return oa.Position(symbol.upper(), p.get("qty", 0), p.get("cost", 0.0),
                                   p.get("current_price") or spot or 0.0, acq), False
    except Exception as e:
        _log.warning("Portfolio unavailable (%s); using an illustrative lot.", e)
    price = spot or 0.0
    return oa.Position(symbol.upper(), 100, price, price, None), True


def _offline_chain():
    with open(_FIXTURE, encoding="utf-8") as f:
        return oa.normalize_chain(json.load(f))


def _apply_overrides(position, levels, spot, args):
    """Let explicit CLI flags win over sourced values (for hypotheticals / no workbook)."""
    if args.qty is not None:
        position.qty = args.qty
    if args.cost is not None:
        position.cost_basis = args.cost
    if args.acquired:
        position.date_acquired = datetime.date.fromisoformat(args.acquired)
    if args.stop is not None:
        levels.stop = args.stop
    if args.target is not None:
        levels.target = args.target
    if args.spot is not None:
        spot = args.spot
    return position, levels, spot


def _build(symbol: str, live: bool, args=None):
    spot, levels = _research_levels(symbol)
    position, illustrative = _portfolio_position(symbol, spot)
    if live:
        client = ETradeClient("production", role="auth")
        # One live position read (authoritative qty/cost/acq-date) + one chain fetch.
        try:
            for p in client.accounts.positions():
                if str(p.get("symbol", "")).upper() == symbol.upper():
                    position = oa.Position(symbol.upper(), p["qty"], p["cost"], p["price"],
                                           p.get("date_acquired"))
                    illustrative = False
                    break
        except Exception as e:
            _log.warning("Live positions read failed (%s); keeping offline position.", e)
        pinned = None
        target_dte = oa.TARGET_DTE
        if args is not None:
            if getattr(args, "expiry", None):
                pinned = datetime.date.fromisoformat(args.expiry)
            if getattr(args, "dte", None) is not None:
                target_dte = args.dte
        quotes, live_spot, expiry = oa.fetch_chain(
            symbol, client, expiry=pinned, target_dte=target_dte,
            min_dte=min(oa.MIN_DTE, target_dte))
        spot = live_spot or spot or position.price
        source = "live"
    else:
        quotes = _offline_chain()
        spot = spot or position.price or 100.0
        expiry = None
        source = "offline"

    if args is not None:
        position, levels, spot = _apply_overrides(position, levels, spot, args)
        if any(v is not None for v in (args.qty, args.cost, args.stop, args.target,
                                       args.acquired, args.spot)):
            illustrative = False

    if not quotes:
        _log.warning(
            "No option quotes for %s (%s). If live, the payload shape may differ from "
            "normalize_chain's assumptions — inspect a raw options_chain response and "
            "adjust field paths.", symbol, source)
        return None, illustrative
    position.price = spot
    select = getattr(args, "select", "level") if args is not None else "level"
    report = oa.build_report(position, levels, quotes, spot=spot, expiry=expiry,
                             data_source=source, select=select)
    return report, illustrative


def main(argv=None):
    ap = argparse.ArgumentParser(description="Options + CPA/tax adviser")
    ap.add_argument("--symbol", default="INTC")
    ap.add_argument("--live", action="store_true", help="fetch a live E*TRADE chain (one call)")
    ap.add_argument("--print", dest="to_terminal", action="store_true", help="print to terminal")
    ap.add_argument("--no-email", action="store_true", help="skip the default e-mail delivery")
    ap.add_argument("--qty", type=float, help="override share quantity")
    ap.add_argument("--cost", type=float, help="override cost basis per share")
    ap.add_argument("--acquired", help="override acquisition date (YYYY-MM-DD)")
    ap.add_argument("--stop", type=float, help="override protection (put) level")
    ap.add_argument("--target", type=float, help="override upside (call) level")
    ap.add_argument("--spot", type=float, help="override current price")
    ap.add_argument("--expiry", help="pin the option expiry (YYYY-MM-DD); "
                                     "default auto-picks ~35 DTE (live only)")
    ap.add_argument("--dte", type=int, help="target days-to-expiry for the auto-selected "
                                            "expiry (default 35; live only)")
    ap.add_argument("--select", choices=("level", "delta"), default="level",
                    help="strike anchoring: 'level' (AETHER stop/target, default) or "
                         "'delta' (~0.30-delta OTM strikes)")
    args = ap.parse_args(argv)

    report, illustrative = _build(args.symbol, args.live, args)
    if report is None:
        return 2
    if illustrative:
        _log.console("No %s position found — showing an illustrative 100-share lot at "
                     "spot. Economics scale with real qty/cost/acquisition date.",
                     args.symbol)

    terminal = oa.render_terminal(report)
    if args.to_terminal or args.no_email:
        sys.stdout.write("\n" + terminal + "\n")

    if not args.no_email:
        try:
            subject = f"🛡️ Options Adviser — {args.symbol} ({len(report.strategies)} strategies)"
            notify.send_email(subject, oa.render_html(report), is_html=True)
            _log.console("E-mailed the %s adviser report.", args.symbol)
        except Exception as e:
            _log.error("E-mail delivery failed (%s). Report follows on stdout.", e)
            if not (args.to_terminal or args.no_email):
                sys.stdout.write("\n" + terminal + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
