"""
Rubber-Band Reversal (RBR) live watchlist monitor.

Scans the OHLCV universe daily and splits it into two lists off each symbol's
LATEST bar, reusing the backtested detector (aether/patterns.rubber_band_reversal_score):

  CONFIRMED  — Leg-2 fired today with buy weight: a confirmed higher-low green reversal
               bar after a big/fast NO-gap overreaction drawdown (the tradeable
               INTC-Jul-30-style trigger, but only the gate-cleared no-gap pocket).
  WATCHING   — Leg-1 in progress (still falling / no higher-low green bar yet) PLUS the
               gap-cohort watch-only confirmations: a Leg-2 that fired on an earnings-GAP
               drawdown. The gap cohort scores 0.0 (win ~0.52, hasn't cleared its own
               gate), so it is surfaced to watch but never auto-bought. The 'Signal'
               column tags these ('RBR↑(gap,watch)') vs a pure pullback ('-').

This is the DAILY/WEEKLY-cadence piece of the RBR feature; the parameter sweep
(scripts/backtesting/pullback_recovery_study.py) is recalibrated only MONTHLY.

Dry-run by default: prints the tables and writes Data/rbr_watch.html. Pass --send
to email the report via notify.send_email (mirrors real_copilot.py's channel).

    python scripts/monitoring/rbr_watch.py            # dry run (no email)
    python scripts/monitoring/rbr_watch.py --send      # also email the alert

No look-ahead and no writes to protected state files — read-only over Symbol_full.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import instruments
import notify
import patterns
from aether_logger import get_logger as _get_logger
from patterns import ohlcv_to_array, rbr_leg1, RBR_LOOKBACK, RBR_VOL_WIN, RBR_WARMUP

_log = _get_logger("rbr_watch")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV    = BASE_DIR / "Data" / "Symbol_full"
OUT_HTML = BASE_DIR / "Data" / "rbr_watch.html"


def _load_ts(path):
    try:
        with open(path) as f:
            return json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None


def _leg1_state(ohlcv_ts, date_str):
    """Return the open-drawdown state at the latest bar, or None if no qualifying
    overreaction drawdown is currently in force. Delegates the drawdown geometry to
    patterns.rbr_leg1 — the SAME helper the scoring detector uses — so WATCHING and
    the detector can never disagree on what a setup is."""
    arr = ohlcv_to_array(ohlcv_ts, date_str, lookback=RBR_LOOKBACK + RBR_VOL_WIN + 10)
    if arr is None or len(arr) < RBR_WARMUP:
        return None
    o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    i = len(arr) - 1
    leg1 = rbr_leg1(o, h, l, c, i)   # None = no qualifying drawdown; trough may be today
    if leg1 is None:
        return None
    return {
        "drop_pct": round(leg1["drop_pct"] * 100, 1),
        "speed": leg1["speed"],
        "days_since_trough": i - leg1["t_idx"],   # 0 = trough is today (knife), still falling
        "trough_low": round(float(leg1["trough_low"]), 2),
        "last_close": round(float(c[i]), 2),
        "had_gap": leg1["had_gap"],
    }


def scan(as_of=None):
    """Return (confirmed, watching) lists of dict rows, scanning every non-excluded
    symbol at its latest bar at or before as_of (None = each symbol's latest bar)."""
    confirmed, watching = [], []
    for path in sorted(OHLCV.glob("*_daily.json")):
        sym = path.stem.replace("_daily", "")
        try:
            if instruments.is_excluded(sym):
                continue
        except Exception:
            pass
        ts = _load_ts(path)
        if not ts:
            continue
        date_str = as_of or max(ts.keys())
        state = _leg1_state(ts, date_str)
        if not state:
            continue
        score, names = patterns.rubber_band_reversal_score(ts, date_str)
        row = {"symbol": sym, "date": date_str, "score": score,
               "label": " ".join(names), **state}
        if score > 0:
            confirmed.append(row)
        else:
            watching.append(row)
    # strongest/deepest first
    confirmed.sort(key=lambda r: (-r["score"], r["drop_pct"]))
    watching.sort(key=lambda r: r["drop_pct"])
    return confirmed, watching


def _table(title, rows, cols):
    if not rows:
        return f"<h3>{title}</h3><p style='color:#888'>none</p>"
    head = "".join(f"<th style='text-align:left;padding:4px 10px'>{c}</th>" for c, _ in cols)
    body = ""
    for r in rows:
        tds = "".join(f"<td style='padding:4px 10px'>{fmt(r)}</td>" for _, fmt in cols)
        body += f"<tr>{tds}</tr>"
    return (f"<h3>{title}</h3><table style='border-collapse:collapse;font-family:monospace'>"
            f"<tr style='border-bottom:1px solid #ccc'>{head}</tr>{body}</table>")


def build_html(confirmed, watching, as_of):
    conf_cols = [
        ("Symbol", lambda r: r["symbol"]),
        ("Signal", lambda r: r["label"] or "RBR"),
        ("Score", lambda r: f"{r['score']:+.2f}"),
        ("Drop%", lambda r: f"{r['drop_pct']:+.1f}"),
        ("Speed", lambda r: r["speed"]),
        ("Gap", lambda r: "yes" if r["had_gap"] else "no"),
        ("Close", lambda r: r["last_close"]),
    ]
    watch_cols = [
        ("Symbol", lambda r: r["symbol"]),
        ("Signal", lambda r: r["label"] or "-"),   # '-' = pure pullback; gap-watch confirms show their tag
        ("Drop%", lambda r: f"{r['drop_pct']:+.1f}"),
        ("Speed", lambda r: r["speed"]),
        ("Days since low", lambda r: r["days_since_trough"]),
        ("Gap", lambda r: "yes" if r["had_gap"] else "no"),
        ("Trough", lambda r: r["trough_low"]),
        ("Close", lambda r: r["last_close"]),
    ]
    return (
        f"<html><body style='font-family:sans-serif'>"
        f"<h2>Rubber-Band Reversal watch &mdash; as of {as_of}</h2>"
        f"<p>{len(confirmed)} confirmed reversal trigger(s), "
        f"{len(watching)} pullback(s) in progress.</p>"
        f"{_table('CONFIRMED &mdash; Leg-2 fired (BUY triggers)', confirmed, conf_cols)}"
        f"<br>{_table('WATCHING &mdash; Leg-1 pullback in progress (+ gap-cohort watch-only confirms)', watching, watch_cols)}"
        f"<p style='color:#888;font-size:12px'>RBR detector "
        f"(aether/patterns.rubber_band_reversal_score); pocket recalibrated monthly via "
        f"scripts/backtesting/pullback_recovery_study.py.</p></body></html>"
    )


def main():
    ap = argparse.ArgumentParser(description="Rubber-Band Reversal live watch")
    ap.add_argument("--send", action="store_true", help="email the report via notify")
    ap.add_argument("--as-of", default=None, help="evaluate as of YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    # Temporal Zero-Trust: stamp the run with the empirical system date and evaluate
    # strictly at-or-before it, so a stray future-dated bar can never leak in.
    run_day = datetime.date.today().isoformat()
    as_of = args.as_of or run_day
    _log.console(f"[RBR watch] system date {run_day}; scanning as of {as_of}...")

    confirmed, watching = scan(as_of=as_of)
    _log.console(f"[RBR watch] {len(confirmed)} confirmed, {len(watching)} watching.")
    for r in confirmed:
        _log.console(f"  CONFIRMED {r['symbol']:<6} score={r['score']:+.2f} "
              f"drop={r['drop_pct']:+.1f}% speed={r['speed']} gap={r['had_gap']} "
              f"close={r['last_close']}")
    for r in watching[:20]:
        _log.console(f"  watching  {r['symbol']:<6} drop={r['drop_pct']:+.1f}% "
              f"speed={r['speed']} days_since_low={r['days_since_trough']} gap={r['had_gap']}")

    html = build_html(confirmed, watching, as_of)
    OUT_HTML.write_text(html, encoding="utf-8")
    _log.console(f"[RBR watch] wrote {OUT_HTML}")

    if args.send:
        subject = f"AETHER RBR watch — {len(confirmed)} confirmed, {len(watching)} watching ({as_of})"
        notify.send_email(subject, html, is_html=True)
        _log.console("[RBR watch] emailed report.")
    else:
        _log.console("[RBR watch] dry run — not sending (pass --send to email).")


if __name__ == "__main__":
    main()
