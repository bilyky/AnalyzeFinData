"""
Multi-stock comparison — console adapter over the shared stock_compare engine.

Prints a side-by-side factor table + the deterministic ranking, always writes
Data/stock_comparison.html, and optionally asks the configured AI to write the
ranked "WHY" summary (prompts/compare_stocks.md). Same engine the web /api/compare
endpoint and the .claude/commands/compare-stocks.md agent skill use — one source of
truth, so every surface ranks the same numbers the same way.

    python scripts/analysis/compare_stocks.py TG CC DAVE IBM            # table + HTML
    python scripts/analysis/compare_stocks.py TG CC DAVE IBM --json     # structured JSON to stdout
    python scripts/analysis/compare_stocks.py TG CC DAVE IBM --summarize  # + AI WHY narrative
    python scripts/analysis/compare_stocks.py TG CC DAVE IBM --summarize --send  # + email it

Note: the console --summarize path scores on the QUANTITATIVE factors only. The richest
qualitative synthesis (recent news / earnings / events per the rubric) comes from the agent
skill, which has web access — this adapter has no news source of its own.

Read-only: never writes protected state files.
"""
import argparse
import datetime
import html
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import notify
from aether import stock_compare
from aether.logger import get_logger as _get_logger

_log = _get_logger("compare_stocks")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUT_HTML = BASE_DIR / "Data" / "stock_comparison.html"

# (header, key, formatter) for the shared table — console and HTML render the same columns.
_COLS = [
    ("Symbol",     "symbol",     lambda v: str(v)),
    ("Combined",   "combined",   lambda v: _num(v, 1, signed=True)),
    ("S10",        "s10",        lambda v: _num(v, 1, signed=True)),
    ("L60",        "l60",        lambda v: _num(v, 1, signed=True)),
    ("PGR",        "pgr",        lambda v: _str(v)),
    ("MoneyFlow",  "money_flow", lambda v: _str(v)),
    ("LT Trend",   "lt_trend",   lambda v: _str(v)),
    ("Setup",      "setup",      lambda v: "OK" if v else "-"),
    ("Stop",       "stop",       lambda v: _num(v, 2)),
    ("StopSrc",    "stop_source", lambda v: _str(v)),
    ("Target",     "target",     lambda v: _num(v, 2)),
    ("R:R",        "risk_ratio", lambda v: _num(v, 2)),
    ("Patterns",   "patterns",   lambda v: _str(v)),
]


def _num(v, nd=2, signed=False):
    try:
        return f"{float(v):{'+' if signed else ''}.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def _str(v):
    return "-" if v in (None, "") else str(v)


# ── Rendering ──────────────────────────────────────────────────────────────────

def render_console(data: dict) -> str:
    """A plain aligned table + ranking for stdout."""
    rows = [r for r in data["rows"] if r.get("found")]
    lines = []
    widths = [max(len(h), *(len(f(r.get(k))) for r in rows)) if rows else len(h)
              for (h, k, f) in _COLS]
    header = "  ".join(h.ljust(w) for (h, _, _), w in zip(_COLS, widths))
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        lines.append("  ".join(f(r.get(k)).ljust(w)
                               for (_, k, f), w in zip(_COLS, widths)))
    missing = data["meta"].get("missing") or []
    if missing:
        lines.append(f"\nnot found: {', '.join(missing)}")
    order = " > ".join(f"{x['symbol']}({_num(x['combined'], 1, signed=True)})"
                       for x in data["ranking"])
    lines.append(f"\nRanking: {order}")
    if data["meta"].get("stale_warning"):
        lines.append(f"⚠ {data['meta']['stale_warning']}")
    return "\n".join(lines)


def render_html(data: dict, summary: str | None = None) -> str:
    """Inline-styled HTML report (email-safe). ``summary`` is the optional AI narrative."""
    def _e(v):
        return html.escape(str(v)) if v not in (None, "") else "-"

    rows = [r for r in data["rows"] if r.get("found")]
    head = "".join(f"<th style='text-align:left;padding:4px 10px'>{_e(h)}</th>"
                   for (h, _, _) in _COLS)
    body = ""
    for r in rows:
        tds = "".join(f"<td style='padding:4px 10px'>{_e(f(r.get(k)))}</td>"
                      for (_, k, f) in _COLS)
        body += f"<tr>{tds}</tr>"
    table = (f"<table style='border-collapse:collapse;font-family:monospace;font-size:13px'>"
             f"<tr style='border-bottom:1px solid #ccc'>{head}</tr>{body}</table>")

    order = " &gt; ".join(f"{_e(x['symbol'])} ({_num(x['combined'], 1, signed=True)})"
                          for x in data["ranking"])
    missing = data["meta"].get("missing") or []
    parts = [
        "<html><body style='font-family:sans-serif'>",
        f"<h2>Stock comparison &mdash; as of {_e(data['as_of'])}</h2>",
        f"<p><b>Market regime:</b> {_e(data['meta'].get('market_regime'))}</p>",
    ]
    if data["meta"].get("stale_warning"):
        parts.append(f"<p style='color:#b00'>&#9888; {_e(data['meta']['stale_warning'])}</p>")
    parts.append(f"<p><b>Ranking:</b> {order}</p>")
    parts.append(table)
    if missing:
        parts.append(f"<p style='color:#888'>Not found: {_e(', '.join(missing))}</p>")
    if summary:
        parts.append("<h3>AI summary</h3>"
                     f"<div style='white-space:pre-wrap;font-family:sans-serif'>{_e(summary)}</div>")
    parts.append("<p style='color:#888;font-size:12px'>Generated by stock_compare "
                 "(deterministic engine); summary is advisory.</p></body></html>")
    return "".join(parts)


# ── AI summary (optional) ────────────────────────────────────────────────────────
# The summarizer lives in aether.stock_compare (shared by console, /api/compare, and
# chat — one implementation, one rubric). This adapter just calls it.


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Compare several stocks side-by-side + ranked.")
    ap.add_argument("symbols", nargs="+", help="ticker symbols, e.g. TG CC DAVE IBM")
    ap.add_argument("--as-of", default=None, help="evaluate as of YYYY-MM-DD (default: today)")
    ap.add_argument("--json", action="store_true", help="print structured JSON to stdout")
    ap.add_argument("--summarize", action="store_true", help="add the AI WHY narrative")
    ap.add_argument("--send", action="store_true", help="email the HTML report via notify")
    args = ap.parse_args()

    # Temporal Zero-Trust: stamp with the empirical system date unless overridden.
    as_of = args.as_of or datetime.date.today().isoformat()
    data = stock_compare.compare_data(args.symbols, as_of=as_of)

    # Machine-readable + human output is the program's RESULT — it belongs on
    # stdout (the /compare-stocks skill pipes `--json`), so write it directly
    # rather than via _log.console() which is diagnostics/stderr.
    if args.json:
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
        return

    sys.stdout.write(render_console(data) + "\n")

    summary, summary_error = (stock_compare.summarize_comparison(data)
                              if args.summarize else (None, None))
    if args.summarize:
        body = summary or f"(AI summary unavailable — {summary_error})"
        sys.stdout.write("\n=== AI SUMMARY ===\n" + body + "\n")

    html_doc = render_html(data, summary=summary)
    OUT_HTML.write_text(html_doc, encoding="utf-8")
    _log.console("[compare] wrote %s", OUT_HTML)

    if args.send:
        subject = (f"AETHER stock comparison — top: "
                   f"{data['ranking'][0]['symbol'] if data['ranking'] else 'n/a'} ({as_of})")
        notify.send_email(subject, html_doc, is_html=True)
        _log.console("[compare] emailed report.")


if __name__ == "__main__":
    main()
