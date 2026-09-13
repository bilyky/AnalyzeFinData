"""Ban-safe daily snapshot of the LIVE E*TRADE option chain → Data/option_chains/<SYM>.jsonl.

The honest answer to "start collecting / get historical option data somewhere": no free historical
option vendor is reachable here, and the options-replay backtest therefore prices a **modeled**
chain (aether/option_pricing.py). This script starts accruing the *real* thing going forward — one
append-only JSON line per run — so that future replays can be re-scored against traded premiums
instead of modeled ones, and the modeling bias in `intc_options_replay_study.py` can be measured.

Ban-safety (CLAUDE.md E*TRADE rule — automated jobs NEVER open a browser):
    * Uses ``ETradeClient(role="data")`` — the read-only data plane, which by construction cannot
      renew tokens or open a browser (the ban-safety boundary in aether/etrade/client.py). If tokens
      are absent/expired it fails LOUDLY and exits non-zero; it never launches an interactive login.
    * Exactly one chain fetch per invocation (expiry auto-select + chain + spot), via the same
      ``oa.fetch_chain`` the adviser uses.

This is a SCAFFOLD to schedule (e.g. a daily task after the close); it recommends/collects only and
places no orders. Run ``--dry-run`` to exercise the serialization path against the offline fixture
with no network call.

Usage:
    python scripts/monitoring/option_chain_snapshot.py                     # live INTC snapshot
    python scripts/monitoring/option_chain_snapshot.py --symbol AMD
    python scripts/monitoring/option_chain_snapshot.py --dry-run           # offline, no live call
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from aether import options_adviser as oa
from aether.etrade.client import ETradeClient
from aether.logger import get_logger as _get_logger


_log = _get_logger("option_chain_snapshot")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUT_DIR = BASE_DIR / "Data" / "option_chains"
_FIXTURE = BASE_DIR / "tests" / "fixtures" / "intc_option_chain.json"


def _serialize_quote(q: oa.OptionQuote) -> dict:
    """OptionQuote -> JSON-safe dict (dates as ISO strings)."""
    d = dataclasses.asdict(q)
    if isinstance(d.get("expiry"), datetime.date):
        d["expiry"] = d["expiry"].isoformat()
    return d


def _write_snapshot(symbol: str, spot, expiry, quotes: list, source: str) -> Path:
    """Append one JSONL record for this snapshot; returns the file path."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{symbol.upper()}.jsonl"
    record = {
        "captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol.upper(),
        "source": source,
        "spot": spot,
        "expiry": expiry.isoformat() if isinstance(expiry, datetime.date) else expiry,
        "n_quotes": len(quotes),
        "quotes": [_serialize_quote(q) for q in quotes],
    }
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return out


def _dry_run(symbol: str) -> int:
    """Exercise the serialization + append path against the offline fixture — no network."""
    try:
        with open(_FIXTURE, encoding="utf-8") as f:
            quotes = oa.normalize_chain(json.load(f))
    except Exception as e:
        _log.console("Dry-run fixture unavailable (%s).", e)
        return 1
    if not quotes:
        _log.console("Dry-run: fixture produced no quotes.")
        return 1
    expiry = next((q.expiry for q in quotes if q.expiry), None)
    out = _write_snapshot(symbol, spot=None, expiry=expiry, quotes=quotes, source="dry-run-fixture")
    _log.console("Dry-run OK: appended %d fixture quotes (expiry %s) to %s (source=dry-run-fixture).",
                 len(quotes), expiry, out)
    return 0


def snapshot(symbol: str = "INTC") -> int:
    """Take one live, ban-safe chain snapshot and append it to the JSONL log."""
    # role="data" is the read-only plane: it cannot renew tokens or open a browser (ban-safe).
    # Constructing it here (not at import) means --dry-run never touches broker state.
    client = ETradeClient("production", role="data")
    try:
        quotes, spot, expiry = oa.fetch_chain(symbol, client,
                                              target_dte=oa.TARGET_DTE, min_dte=oa.MIN_DTE)
    except Exception as e:
        _log.error("Live chain fetch failed for %s (%s). Tokens may be expired — this job will "
                   "NOT open a browser; renew tokens out-of-band and retry.", symbol, e)
        return 2
    if not quotes:
        _log.error("No quotes returned for %s — the live payload shape may differ from "
                   "normalize_chain's assumptions; inspect a raw options_chain response.", symbol)
        return 2
    out = _write_snapshot(symbol, spot=spot, expiry=expiry, quotes=quotes, source="etrade-live")
    _log.console("Snapshot OK: %d live quotes for %s (spot %s, expiry %s) appended to %s.",
                 len(quotes), symbol.upper(), spot, expiry, out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ban-safe daily E*TRADE option-chain snapshot collector")
    ap.add_argument("--symbol", default="INTC")
    ap.add_argument("--dry-run", action="store_true",
                    help="exercise serialization against the offline fixture (no live call)")
    args = ap.parse_args(argv)
    return _dry_run(args.symbol) if args.dry_run else snapshot(args.symbol)


if __name__ == "__main__":
    sys.exit(main())
