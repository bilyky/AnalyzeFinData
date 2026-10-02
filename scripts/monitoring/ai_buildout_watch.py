"""
AI-buildout supply-chain watchlist (WATCH-ONLY).

Ranks power / cooling / electrical / construction names that sit downstream of AI
data-center spending by early public signals: revenue and backlog (RPO) growth,
recent 8-K material agreements, 60-day strength vs SPY, and money flow. See
aether/ai_buildout.py for the signal definitions. The ranking is unvalidated and
adds no buy weight.

Dry-run by default: prints the table and writes Data/ai_buildout_watch.json + .html.
Pass --send to email the report via notify.send_email (same channel as rbr_watch.py).

    python scripts/monitoring/ai_buildout_watch.py            # dry run (no email)
    python scripts/monitoring/ai_buildout_watch.py --send     # also email the report

Reads SEC EDGAR (cached in Data/edgar_cache/) and local OHLCV. No writes to
protected state files.
"""
import argparse
import datetime
import html
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import notify
from aether import ai_buildout, paths
from aether_logger import get_logger as _get_logger

_log = _get_logger("ai_buildout_watch")


def _fmt(v, spec="+.1f"):
    return "-" if v is None else format(v, spec)


_COLS = [
    ("Symbol", lambda r: r["symbol"]),
    ("Bucket", lambda r: r["bucket"]),
    ("Score", lambda r: f"{r['watch_score']:+d}"),
    ("Rev YoY%", lambda r: _fmt(r["revenue_yoy"])),
    ("RPO YoY%", lambda r: _fmt(r["rpo_yoy"]) + (
        "?" if r["rpo_yoy"] is not None and abs(r["rpo_yoy"]) > ai_buildout.RPO_SUSPECT_PCT else "")),
    ("8-K 1.01 (90d)", lambda r: r["agreements_90d"]),
    ("Last agreement", lambda r: (r["agreement_dates"] or ["-"])[0]),
    ("RS vs SPY 60d", lambda r: _fmt(r["rs_60d"])),
    ("CMF20", lambda r: _fmt(r["cmf_20"], "+.2f")),
    ("Close", lambda r: "-" if r["close"] is None else r["close"]),
    ("Name", lambda r: r["name"] or ""),
]


def build_html(rows, as_of, failed=()):
    head = "".join(f"<th style='text-align:left;padding:4px 10px'>{c}</th>" for c, _ in _COLS)
    body = "".join(
        "<tr>" + "".join(f"<td style='padding:4px 10px'>{html.escape(str(fmt(r)))}</td>"
                         for _, fmt in _COLS) + "</tr>"
        for r in rows)
    return (
        f"<html><body style='font-family:sans-serif'>"
        f"<h2>AI-buildout supply chain watch &mdash; as of {as_of}</h2>"
        f"<p>{len(rows)} names in the power / cooling / electrical / construction theme. "
        f"Watch-only: the score is unvalidated and adds no buy weight. "
        f"RPO YoY marked '?' (beyond &plusmn;{ai_buildout.RPO_SUSPECT_PCT}%) is likely a reporting "
        f"change or acquisition and is not scored.</p>"
        + (f"<p style='color:#b45309'>SEC fetch failed for {len(failed)} symbol(s), not "
           f"scanned: {html.escape(', '.join(failed))}</p>" if failed else "") +
        f"<table style='border-collapse:collapse;font-family:monospace'>"
        f"<tr style='border-bottom:1px solid #ccc'>{head}</tr>{body}</table>"
        f"<p style='color:#888;font-size:12px'>Sources: SEC EDGAR XBRL + 8-K filings, local "
        f"OHLCV (placeholder bars skipped). aether/ai_buildout.py</p></body></html>"
    )


def main():
    ap = argparse.ArgumentParser(description="AI-buildout supply-chain watch")
    ap.add_argument("--send", action="store_true", help="email the report via notify")
    ap.add_argument("--as-of", default=None, help="evaluate as of YYYY-MM-DD (default: today)")
    ap.add_argument("--top", type=int, default=25, help="rows to print (default 25)")
    args = ap.parse_args()

    # Temporal Zero-Trust: take the date from the system clock, never assume it.
    run_day = datetime.date.today().isoformat()
    as_of = args.as_of or run_day
    _log.console(f"[AI buildout] system date {run_day}; scanning as of {as_of}...")

    failed = []
    rows = ai_buildout.scan(as_of, failed=failed)
    report = build_html(rows, as_of, failed)
    out_json = ai_buildout.save(rows, as_of, failed=failed)
    out_html = Path(paths.data_dir()) / "ai_buildout_watch.html"
    out_html.write_text(report, encoding="utf-8")

    _log.console(f"[AI buildout] {len(rows)} themed names.")
    if failed:
        _log.warning(f"[AI buildout] SEC fetch failed for {len(failed)} symbol(s), not "
                     f"scanned (re-run to retry): {', '.join(failed)}")
    for r in rows[:args.top]:
        _log.console(
            f"  {r['symbol']:<6} {r['bucket']:<12} score={r['watch_score']:+d} "
            f"rev={_fmt(r['revenue_yoy'])} rpo={_fmt(r['rpo_yoy'])} "
            f"8k101={r['agreements_90d']} rs60={_fmt(r['rs_60d'])} cmf={_fmt(r['cmf_20'], '+.2f')}")
    _log.console(f"[AI buildout] wrote {out_json} and {out_html}")

    if args.send:
        subject = f"AETHER AI-buildout watch — {len(rows)} names ({as_of})"
        notify.send_email(subject, report, is_html=True)
        _log.console("[AI buildout] emailed report.")
    else:
        _log.console("[AI buildout] dry run — not sending (pass --send to email).")


if __name__ == "__main__":
    main()
