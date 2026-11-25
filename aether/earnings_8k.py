"""AI labels for earnings press releases (R&D #98 step 3, Part B; owner 2026-11-25: "8-K should be scheduled").

For each earnings report in Data/earnings_history.json (SEC 8-K item 2.02), fetch the press release attached to the
filing (exhibit 99.1, chosen by name, else the largest document that isn't the 8-K itself), cut its text to the first
TEXT_CHARS characters, and ask the configured AI tier for three labels with one fixed prompt (PROMPT_VERSION):
results (beat / miss / inline / unclear), guidance (raised / lowered / maintained / none / unclear), tone (-2..2).

Records go to Data/earnings_8k_labels.jsonl (ledgers.EARNINGS_8K_LABELS_FILE); this module is its only writer. Labels
are written before any outcome is looked at, and the study (earnings_drift_study.py, Part B) never re-labels: a
prompt change is a new PROMPT_VERSION, recorded on every row.

Each run labels at most --max reports (the free AI tiers allow a few hundred calls a day): new reports first (filed in
the last RECENT_DAYS days, newest first), then the backfill sample (filed from BACKFILL_FROM, oldest first). A report
already labelled "ok" or found to have no press release is done; fetch / AI / parse failures are retried on later runs
up to MAX_TRIES times. The first AI skip ends the run (no provider: no point fetching more documents).

Only public SEC documents are sent to the model: no account or position data.

    python -m aether.earnings_8k [--max 150] [--refresh-history]
"""
from __future__ import annotations

import argparse
import datetime
import html as _html
import json
import re
import sys

from aether import ai_buildout, ai_step, earnings_history, ledgers
from aether.logger import get_logger as _get_logger

_log = _get_logger("earnings_8k")

PROMPT_VERSION = "v1"
TEXT_CHARS = 6000
RECENT_DAYS = 10
BACKFILL_FROM = "2021-01-01"
MAX_TRIES = 3
DEFAULT_MAX = 150
FINAL = {"ok", "no_exhibit"}
RESULTS = {"beat", "miss", "inline", "unclear"}
GUIDANCE = {"raised", "lowered", "maintained", "none", "unclear"}

SYSTEM = (
    "You read a company's quarterly earnings press release and label it. Answer with one JSON object and nothing "
    'else: {"results": "beat"|"miss"|"inline"|"unclear", "guidance": "raised"|"lowered"|"maintained"|"none"|'
    '"unclear", "tone": -2|-1|0|1|2}. results: whether reported results beat, missed or met expectations or the '
    "company's prior outlook, as stated or clearly implied in the text; \"unclear\" if the text doesn't say. guidance: "
    "how the company changed its forward outlook versus its previous one; \"none\" if it gives no outlook. tone: the "
    "overall tone of management's statements, -2 very negative to +2 very positive. Use only the text provided."
)

_EX99 = re.compile(r"ex[-_]?99", re.I)
_HINT = re.compile(r"press|release|earn|result", re.I)


def pick_exhibit(items: list, primary: str | None) -> str | None:
    """The press-release document among a filing's files (EDGAR index.json 'item' list), or None."""
    cands = []
    for it in items or []:
        name = it.get("name") or ""
        low = name.lower()
        if not low.endswith((".htm", ".html", ".txt")) or name == primary or "-index" in low:
            continue
        if re.fullmatch(r"\d{10}-\d{2}-\d{6}\.txt", name):       # the whole submission as one file
            continue
        try:
            size = int(it.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        rank = 2 if _EX99.search(name) else 1 if _HINT.search(name) else 0
        cands.append((rank, size, name))
    if not cands:
        return None
    rank, size, name = max(cands)
    return name if rank > 0 or size > 0 else None


def html_to_text(raw: str) -> str:
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6])>", "\n", t)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return "\n".join(x.strip() for x in t.splitlines() if x.strip())


def parse_label(text: str | None) -> dict | None:
    """The three labels from a model answer, or None when it isn't one valid JSON object."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    results, guidance, tone = str(d.get("results", "")).lower(), str(d.get("guidance", "")).lower(), d.get("tone")
    try:
        tone = int(tone)
    except (TypeError, ValueError):
        return None
    if results not in RESULTS or guidance not in GUIDANCE or not -2 <= tone <= 2:
        return None
    return {"results": results, "guidance": guidance, "tone": tone}


def read_records(path=None) -> list:
    try:
        with open(path or ledgers.EARNINGS_8K_LABELS_FILE, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []


def progress(records: list) -> tuple[set, dict]:
    """(accessions that are done, {accession: failed tries}) for the current PROMPT_VERSION."""
    done, tries = set(), {}
    for r in records:
        if r.get("prompt_version") != PROMPT_VERSION:
            continue
        if r.get("status") in FINAL:
            done.add(r.get("accession"))
        else:
            tries[r.get("accession")] = tries.get(r.get("accession"), 0) + 1
    return done, tries


def due(history: dict, records: list, today: str) -> list:
    """(symbol, cik, event) to label this run, in order: recent reports newest first, then the backfill oldest first."""
    done, tries = progress(records)
    recent_from = (datetime.date.fromisoformat(today) - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    recent, backfill = [], []
    for sym, v in (history.get("symbols") or {}).items():
        for ev in v.get("events", []):
            acc = ev.get("accession")
            if not acc or acc in done or tries.get(acc, 0) >= MAX_TRIES:
                continue
            row = (sym, v["cik"], ev)
            if ev["filed"] >= recent_from:
                recent.append(row)
            elif ev["filed"] >= BACKFILL_FROM:
                backfill.append(row)
    recent.sort(key=lambda r: r[2]["filed"], reverse=True)
    backfill.sort(key=lambda r: (r[2]["filed"], r[0]))
    return recent + backfill


def label_one(sym: str, cik: int, ev: dict, ask=None) -> dict:
    """One record for one report (never raises)."""
    rec = {"sym": sym, "cik": cik, "accession": ev.get("accession"), "filed": ev.get("filed"),
           "first_session": ev.get("first_session"), "prompt_version": PROMPT_VERSION,
           "labeled_at": datetime.datetime.now().isoformat(timespec="seconds")}
    try:
        idx = ai_buildout.filing_index(cik, ev["accession"])
        if idx is None:
            return {**rec, "status": "fetch_failed", "detail": "index"}
        doc = pick_exhibit(((idx.get("directory") or {}).get("item")) or [], ev.get("primary_doc"))
        if not doc:
            return {**rec, "status": "no_exhibit"}
        raw = ai_buildout.filing_document(cik, ev["accession"], doc)
        if raw is None:
            return {**rec, "status": "fetch_failed", "detail": doc}
        text = html_to_text(raw)[:TEXT_CHARS]
        rec.update(doc=doc, chars=len(text))
        if len(text) < 200:
            return {**rec, "status": "no_exhibit", "detail": "too little text"}
        ask = ask or (lambda s, u: ai_step.run("earnings_8k_label", s, u, tier="cheap", max_tokens=200,
                                               temperature=0.0, notify_on_skip=False))
        res = ask(SYSTEM, f"Company ticker: {sym}. Filed {ev.get('filed')}.\n\n{text}")
        if res.text is None:
            return {**rec, "status": "ai_skipped", "detail": (res.skipped or "")[:200]}
        lab = parse_label(res.text)
        if lab is None:
            return {**rec, "status": "unparsed", "provider": res.provider, "detail": res.text[:200]}
        return {**rec, **lab, "status": "ok", "provider": res.provider}
    except Exception as e:   # one bad filing must not stop the run
        return {**rec, "status": "fetch_failed", "detail": f"{type(e).__name__}: {e}"[:200]}


def run(max_n: int = DEFAULT_MAX, refresh_history: bool = False, today: str | None = None, ask=None) -> dict:
    if refresh_history:
        earnings_history.build(recent_ttl_hours=20)
    history = earnings_history.load()
    st = earnings_history.status(history)
    if not st.get("symbols_with_reports"):
        return {"labelled": 0, "note": "no earnings history (run python -m aether.earnings_history)"}
    todo = due(history, read_records(), today or datetime.date.today().isoformat())
    counts, written = {}, 0
    for sym, cik, ev in todo[:max_n]:
        rec = label_one(sym, cik, ev, ask)
        ledgers.append_jsonl(ledgers.EARNINGS_8K_LABELS_FILE, [rec])
        written += 1
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        if rec["status"] == "ai_skipped":
            _log.warning(f"earnings 8-K labels: AI unavailable ({rec.get('detail')}); stopping this run.")
            break
    out = {"due": len(todo), "written": written, "by_status": counts, "history_complete": st.get("complete")}
    _log.info(f"earnings 8-K labels: {out}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max", type=int, default=DEFAULT_MAX)
    ap.add_argument("--refresh-history", action="store_true", help="re-read new filings from SEC first")
    a = ap.parse_args(argv)
    out = run(a.max, a.refresh_history)
    sys.stdout.write(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
