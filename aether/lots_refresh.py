"""Keep position-watch's lots file current from E*TRADE, during the workbook update.

Data/position_watch/<SYMBOL>_lots.json lists the watched holding's tax lots per account label
({"account", "acquired", "price", "qty"}); position-watch picks lots from it on a TRIM and
estimates the tax. It was maintained by hand, so a sale, a buy or a vest showed only after an
edit. check_from_xls() already holds a renewed token and reads every account's positions; this
refreshes the lots in the same session. Read-only against the broker; it never logs in.

Only accounts mapped in the private config are refreshed:
    "lot_sources": {"<lots-file label>": "<account last 4 digits>"}
Labels without a mapping (for example a stock-plan account the API doesn't list) are kept exactly
as they are. Nothing is written unless, for every mapped account, the broker's lots add up to the
broker's position quantity; the old file is backed up first (Data/Backup/<time>_pre_lots_refresh)
and replaced atomically. Lot dates are the broker's acquired dates in US/Eastern.
"""
import argparse
import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

from aether import position_watch_store as store
from aether.etrade.client import ETradeClient

ET = ZoneInfo("America/New_York")
QTY_TOL = 1e-3


def et_date(ms) -> str | None:
    """The broker's epoch-milliseconds date as a US/Eastern calendar date."""
    try:
        ms = int(ms or 0)
    except (TypeError, ValueError):
        return None
    return datetime.datetime.fromtimestamp(ms / 1000, tz=ET).date().isoformat() if ms > 0 else None


def fetch_position_lots(accounts, symbol: str, last4s: set, positions: list, tokens: dict | None = None) -> dict:
    """{last4: {"position_qty": float, "lots": [{acquired, price, qty}]}} for the mapped accounts that
    hold `symbol`. accounts: the E*TRADE client's read-only accounts feed (``ETradeClient.accounts``:
    ``account_keys`` and ``position_lots``). positions: etrade.fetch_positions() rows, the one
    reader of portfolios (the workbook update has just made them); quantities come from there and
    only the lots are fetched here."""
    out = {}
    for last4, key in accounts.account_keys(tokens).items():
        if last4 not in last4s:
            continue
        qty = sum(float(p.get("qty") or 0) for p in positions
                  if str(p.get("account_last4")) == last4 and str(p.get("symbol", "")).upper() == symbol)
        lots = []
        if qty > 0:
            for lot in accounts.position_lots(symbol, key, tokens):
                q = float(lot.get("remainingQty", lot.get("originalQty", 0)) or 0)
                if q > 0:
                    lots.append({"acquired": et_date(lot.get("acquiredDate")), "price": float(lot.get("price") or 0),
                                 "qty": q})
        out[last4] = {"position_qty": qty, "lots": lots}
    return out


def merge(existing: list, fresh_by_label: dict) -> list:
    """Existing lots with every refreshed label's lots replaced; other labels untouched, order kept."""
    kept = [x for x in existing if str(x.get("account")) not in fresh_by_label]
    added = [{"account": label, **lot} for label, lots in fresh_by_label.items() for lot in lots]
    return kept + sorted(added, key=lambda x: (x["account"], x["acquired"] or "", x["price"]))


def refresh(accounts, *, tokens: dict | None = None, symbol: str | None = None, cfg: dict | None = None,
            data_dir: str | None = None, now: datetime.datetime | None = None, dry_run: bool = False,
            positions: list | None = None) -> dict:
    """Refresh the lots file for the mapped accounts. Returns {"status": ..., ...}; never raises on a
    broker or data problem (the caller is the workbook update, which must not fail on this)."""
    folder = os.path.join(data_dir, store.FOLDER_NAME) if data_dir else store.watch_dir()
    if cfg is None:
        try:
            cfg = store.read_config(folder)
        except (OSError, ValueError):
            cfg = None
        if not cfg:
            return {"status": "skipped", "reason": "no position-watch config"}
    sources = {str(k): str(v)[-4:] for k, v in (cfg.get("lot_sources") or {}).items()}
    symbol = (symbol or cfg.get("symbol") or "").upper()
    if not sources or not symbol:
        return {"status": "skipped", "reason": "no lot_sources in the config"}
    try:
        existing = store.read_lots(symbol, folder) or []
    except (OSError, ValueError) as e:
        return {"status": "error", "reason": f"can't read {os.path.basename(store.lots_path(symbol, folder))}: {e}"}
    if positions is None:                      # quantities come only from etrade.fetch_positions()
        return {"status": "error", "reason": "no positions given (pass etrade.fetch_positions() rows)"}
    try:
        got = fetch_position_lots(accounts, symbol, set(sources.values()), positions, tokens)
    except Exception as e:                      # broker read failed: keep the file
        return {"status": "error", "reason": f"broker read failed: {e}"}
    fresh, problems = {}, []
    for label, last4 in sources.items():
        g = got.get(last4)
        if g is None:
            problems.append(f"{label}: account ...{last4} not in the broker's list")
            continue
        total = sum(x["qty"] for x in g["lots"])
        if abs(total - g["position_qty"]) > QTY_TOL:
            problems.append(f"{label}: lots add up to {total:g}, position is {g['position_qty']:g}")
            continue
        fresh[label] = g["lots"]
    if problems:
        return {"status": "blocked", "reason": "; ".join(problems)}
    new = merge(existing, fresh)
    if store.same_lots(new, existing):            # same lots in another order: no write, no backup
        return {"status": "unchanged", "lots": len(new)}
    if dry_run:                                   # first run on a machine: show, don't write
        added, removed = store.lots_diff(existing, new)
        return {"status": "would_update", "lots": len(new), "labels": sorted(fresh),
                "added": added, "removed": removed}
    store.write_lots(symbol, new, folder, now)    # backup first, then an atomic replace
    return {"status": "updated", "lots": len(new), "labels": sorted(fresh)}


def main(argv=None) -> int:
    """Run once by hand: python -m aether.lots_refresh [--write]. Dry run by default; uses today's
    E*TRADE token only (no login)."""
    ap = argparse.ArgumentParser(description="Refresh position-watch's lots from E*TRADE (dry run by default)")
    ap.add_argument("--write", action="store_true", help="write the file (after a backup)")
    a = ap.parse_args(argv)
    client = ETradeClient("production", role="data")      # the read plane: never renews, never logs in
    tok = client.auth.current_token()
    if not tok:
        sys.stdout.write("No E*TRADE token for today on this machine; nothing done (no login is attempted).\n")
        return 1
    r = refresh(client.accounts, tokens=tok, dry_run=not a.write, positions=client.accounts.positions(tok))
    sys.stdout.write(json.dumps(r, indent=1) + "\n")
    return 0 if r["status"] in ("updated", "unchanged", "would_update", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
