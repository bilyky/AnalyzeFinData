"""
Multi-stock structured comparison — the pure, deterministic source of truth.

``compare_data(symbols)`` assembles a side-by-side factor view plus a deterministic
ranking straight from the already-computed Research rows (``data_api.read_research``),
with **no LLM and no network**. Summarization — the ranked WHY narrative, including the
qualitative news/events layer — is the *caller's* job: the console ``--summarize`` flag,
any ``/api/compare`` consumer, and the ``.claude/commands/compare-stocks.md`` agent skill
all layer that on top via the shared ``prompts/compare_stocks.md`` rubric. Keeping this
engine LLM-free means every surface ranks the same numbers the same way (single source of
truth) and the logic is trivially unit-testable.

Zero-Trust: each row carries a ``stale`` flag derived from the resolved stop/target source
(``"stale"`` when the OHLCV cache is old and the level fell back to a fixed % off price), and
``meta.stale_warning`` fires when any compared symbol is stale — so consumers caveat levels.
"""
import datetime
from pathlib import Path

import data_api
from aether import ai_client
from aether.logger import get_logger


_log = get_logger("stock_compare")

# Shared rubric for the optional AI "WHY" narrative (single source of truth for the
# prompt — console, /api/compare, and chat all summarize through summarize_comparison()).
RUBRIC_PATH = Path(__file__).resolve().parent.parent / "prompts" / "compare_stocks.md"

# Fields carried verbatim from each Research row — no recompute, no extra file reads.
# read_research() already resolved stops/targets/scores once; we only select + rank.
_CARRY = (
    "symbol", "industry", "price", "pgr", "s10", "l60", "combined", "setup",
    "money_flow", "lt_trend", "industry_strength", "obos",
    "stop", "stop_source", "target", "target_source", "risk_ratio",
    "instrument", "patterns", "status", "buying_ratio", "seasonality", "win_pct",
)


def _is_stale(row: dict) -> bool:
    """A row is stale when its stop or target fell back to the % source because the
    OHLCV cache was older than STALE_STOP_DAYS (risk_utils.resolve_*_detailed → 'stale')."""
    return row.get("stop_source") == "stale" or row.get("target_source") == "stale"


def _rank_key(row: dict):
    """Deterministic ordering: highest combined score first; ties broken by a real
    setup, then by better reward:risk, then alphabetically for full stability."""
    return (
        -(row.get("combined") or 0.0),
        0 if row.get("setup") else 1,
        -(row.get("risk_ratio") or 0.0),
        row.get("symbol") or "",
    )


def compare_data(symbols: list[str], as_of: str | None = None) -> dict:
    """Return a structured side-by-side comparison + deterministic ranking for ``symbols``.

    Shape::

        {as_of, symbols, rows:[{...,found,stale} | {symbol,found:false,reason}],
         ranking:[{symbol,rank,combined}],
         meta:{requested,found,missing,market_regime,stale_warning,generated_by}}

    Unknown tickers (not on the Research sheet) are returned as ``found: false`` rows and
    listed in ``meta.missing`` — never raised — so a caller comparing a typo still gets a
    usable result for the valid symbols.
    """
    as_of = as_of or datetime.date.today().isoformat()

    # Normalize + de-dupe while preserving the caller's requested order.
    ordered: list[str] = []
    seen: set[str] = set()
    for s in (symbols or []):
        sym = (s or "").upper().strip()
        if sym and sym not in seen:
            seen.add(sym)
            ordered.append(sym)

    research = data_api.read_research()          # single workbook read for all symbols
    index = {r["symbol"].upper(): r for r in research.get("rows", [])}
    regime = research.get("summary", {}).get("market_regime", "Unknown")

    rows: list[dict] = []
    missing: list[str] = []
    for sym in ordered:
        src = index.get(sym)
        if not src:
            rows.append({"symbol": sym, "found": False, "reason": "not on Research sheet"})
            missing.append(sym)
            continue
        row = {k: src.get(k) for k in _CARRY}
        row["found"] = True
        row["stale"] = _is_stale(src)
        rows.append(row)

    found_rows = [r for r in rows if r.get("found")]
    ranked = sorted(found_rows, key=_rank_key)
    ranking = [
        {"symbol": r["symbol"], "rank": i, "combined": r.get("combined")}
        for i, r in enumerate(ranked, 1)
    ]

    stale_warning = None
    if any(r.get("stale") for r in found_rows):
        stale_warning = (
            "One or more symbols have a stale OHLCV cache — their stop/target levels "
            "fell back to a fixed % off price. Treat the levels (and risk:reward) as "
            "approximate and refresh Data/Symbol_full before acting."
        )

    meta = {
        "requested": len(ordered),
        "found": len(found_rows),
        "missing": missing,
        "market_regime": regime,
        "stale_warning": stale_warning,
        "generated_by": "stock_compare",
    }
    _log.info("compare_data: %d requested, %d found, %d missing (regime=%s)",
              len(ordered), len(found_rows), len(missing), regime)
    return {
        "as_of": as_of,
        "symbols": ordered,
        "rows": rows,
        "ranking": ranking,
        "meta": meta,
    }


# ── Optional AI summary ────────────────────────────────────────────────────────
# The engine above is pure; the two helpers below are the ONE optional LLM summarizer
# shared by every surface (console --summarize, /api/compare?summarize, chat compare
# intent). Importing this module never triggers an LLM call — summarize_comparison() must
# be invoked explicitly and degrades to None when no provider/rubric is available.

def render_for_summary(data: dict) -> str:
    """Label:value block per symbol + ranking + meta, for the summary rubric."""
    lines = [f"as_of: {data['as_of']}",
             f"market_regime: {data['meta'].get('market_regime')}",
             f"stale_warning: {data['meta'].get('stale_warning') or 'none'}",
             "deterministic_ranking: " + ", ".join(
                 f"{x['rank']}. {x['symbol']} ({x['combined']})" for x in data["ranking"]),
             ""]
    for r in data["rows"]:
        if not r.get("found"):
            lines.append(f"[{r['symbol']}] NOT FOUND ({r.get('reason')})")
            continue
        lines.append(
            f"[{r['symbol']}] combined={r.get('combined')} s10={r.get('s10')} "
            f"l60={r.get('l60')} pgr={r.get('pgr')} money_flow={r.get('money_flow')} "
            f"lt_trend={r.get('lt_trend')} industry_strength={r.get('industry_strength')} "
            f"obos={r.get('obos')} setup={r.get('setup')} price={r.get('price')} "
            f"stop={r.get('stop')} ({r.get('stop_source')}) target={r.get('target')} "
            f"({r.get('target_source')}) risk_ratio={r.get('risk_ratio')} "
            f"instrument={r.get('instrument')} status={r.get('status')} "
            f"win_pct={r.get('win_pct')} patterns={r.get('patterns') or 'none'} "
            f"stale={r.get('stale')}")
    return "\n".join(lines)


def summarize_comparison(data: dict) -> tuple[str | None, str | None]:
    """Ask the configured AI for the ranked WHY narrative (prompts/compare_stocks.md).

    Returns ``(summary, None)`` on success and ``(None, reason)`` when the rubric is
    missing, no provider is configured, or the call fails — so every caller can tell the
    user *what happened* instead of a bare "unavailable" (ai_client.evaluate RAISES on
    failure). The reason is safe to show (no exception text / secrets — only the failure
    kind and provider name); the full error goes to the log. Synthesizes on the
    QUANTITATIVE factors only (no web access); the agent skill is the surface with news.
    """
    try:
        rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        _log.warning("compare rubric missing at %s — skipping AI summary.", RUBRIC_PATH)
        return None, "the comparison rubric is missing on the server"
    provider = ai_client.primary()
    if not provider:
        _log.warning("No AI provider configured — skipping AI summary.")
        return None, "no AI provider is configured"
    try:
        return ai_client.evaluate(rubric, render_for_summary(data),
                                  provider=provider, max_tokens=1200), None
    except Exception as e:
        _log.warning("AI summary failed (%s): %s", provider, e)
        return None, (f"the AI provider '{provider}' didn't respond "
                      f"({type(e).__name__}) — usually a transient rate limit, try again")
