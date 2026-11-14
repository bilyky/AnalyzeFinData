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
import shutil
import sys
from zoneinfo import ZoneInfo

from aether import etrade, paths

ET = ZoneInfo("America/New_York")
QTY_TOL = 1e-3


def _walk(o, key):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                yield from (v if isinstance(v, list) else [v])
            else:
                yield from _walk(v, key)
    elif isinstance(o, list):
        for x in o:
            yield from _walk(x, key)


def et_date(ms) -> str | None:
    """The broker's epoch-milliseconds date as a US/Eastern calendar date."""
    try:
        ms = int(ms or 0)
    except (TypeError, ValueError):
        return None
    return datetime.datetime.fromtimestamp(ms / 1000, tz=ET).date().isoformat() if ms > 0 else None


def fetch_position_lots(accounts_api, symbol: str, last4s: set) -> dict:
    """{last4: {"position_qty": float, "lots": [{acquired, price, qty}]}} for the mapped accounts that
    hold `symbol`. accounts_api: a pyetrade ETradeAccounts (aether.etrade.get_accounts)."""
    raw = accounts_api.list_accounts(resp_format="json")
    out = {}
    for acct in _walk(raw, "Account"):
        last4, key = str(acct.get("accountId", ""))[-4:], acct.get("accountIdKey")
        if last4 not in last4s or not key:
            continue
        port = accounts_api.get_account_portfolio(key, resp_format="json")
        qty = sum(float(p.get("quantity") or 0) for p in _walk(port, "Position")
                  if str(p.get("symbolDescription", "")).strip().upper() == symbol)
        lots = []
        if qty > 0:
            resp = accounts_api.get_portfolio_position_lot(symbol, key, resp_format="json")
            for lot in _walk(resp, "PositionLot"):
                q = float(lot.get("remainingQty", lot.get("originalQty", 0)) or 0)
                if q > 0:
                    lots.append({"acquired": et_date(lot.get("acquiredDate")), "price": float(lot.get("price") or 0),
                                 "qty": q})
        out[last4] = {"position_qty": qty, "lots": lots}
    return out


def _canon(lots: list) -> list:
    """Order-free form of a lots list, for the "unchanged" check (qty/price compared as floats)."""
    return sorted(json.dumps({**x, "qty": float(x.get("qty") or 0), "price": float(x.get("price") or 0)},
                             sort_keys=True) for x in lots)


def merge(existing: list, fresh_by_label: dict) -> list:
    """Existing lots with every refreshed label's lots replaced; other labels untouched, order kept."""
    kept = [x for x in existing if str(x.get("account")) not in fresh_by_label]
    added = [{"account": label, **lot} for label, lots in fresh_by_label.items() for lot in lots]
    return kept + sorted(added, key=lambda x: (x["account"], x["acquired"] or "", x["price"]))


def refresh(accounts_api, *, symbol: str | None = None, cfg: dict | None = None, data_dir: str | None = None,
            now: datetime.datetime | None = None, dry_run: bool = False) -> dict:
    """Refresh the lots file for the mapped accounts. Returns {"status": ..., ...}; never raises on a
    broker or data problem (the caller is the workbook update, which must not fail on this)."""
    data_dir = data_dir or paths.data_dir()
    watch = os.path.join(data_dir, "position_watch")
    if cfg is None:
        try:
            with open(os.path.join(watch, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return {"status": "skipped", "reason": "no position-watch config"}
    sources = {str(k): str(v)[-4:] for k, v in (cfg.get("lot_sources") or {}).items()}
    symbol = (symbol or cfg.get("symbol") or "").upper()
    if not sources or not symbol:
        return {"status": "skipped", "reason": "no lot_sources in the config"}
    path = os.path.join(watch, f"{symbol}_lots.json")
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = []
    except (OSError, ValueError) as e:
        return {"status": "error", "reason": f"can't read {os.path.basename(path)}: {e}"}
    try:
        got = fetch_position_lots(accounts_api, symbol, set(sources.values()))
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
    if _canon(new) == _canon(existing):           # same lots in another order: no write, no backup
        return {"status": "unchanged", "lots": len(new)}
    if dry_run:                                   # first run on a machine: show, don't write
        before = {x for x in _canon(existing)}
        after = {x for x in _canon(new)}
        return {"status": "would_update", "lots": len(new), "labels": sorted(fresh),
                "added": len(after - before), "removed": len(before - after)}
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    if os.path.exists(path):
        bdir = os.path.join(data_dir, "Backup", f"{stamp}_pre_lots_refresh")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=1)
    os.replace(tmp, path)
    return {"status": "updated", "lots": len(new), "labels": sorted(fresh)}


def main(argv=None) -> int:
    """Run once by hand: python -m aether.lots_refresh [--write]. Dry run by default; uses today's
    E*TRADE token only (no login)."""
    ap = argparse.ArgumentParser(description="Refresh position-watch's lots from E*TRADE (dry run by default)")
    ap.add_argument("--write", action="store_true", help="write the file (after a backup)")
    a = ap.parse_args(argv)
    tok = etrade._load_tokens("production")
    if not tok:
        sys.stdout.write("No E*TRADE token for today on this machine; nothing done (no login is attempted).\n")
        return 1
    r = refresh(etrade.get_accounts(tok, "production"), dry_run=not a.write)
    sys.stdout.write(json.dumps(r, indent=1) + "\n")
    return 0 if r["status"] in ("updated", "unchanged", "would_update", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
