"""Paths of the learning ledgers: the one home for every reader, writer and the test harness.

Read these as module attributes at call time (``ledgers.TRADE_DNA_FILE``), never
``from aether.ledgers import ...``, so a redirect (tests/__init__.py) reaches every user.
"""
import json
import os
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "Data"

TRADE_DNA_FILE = _DATA / "trade_history_dna.json"        # closed trades + circuit-breaker triggers
FAILURE_RULES_FILE = _DATA / "failure_dna_rules.json"    # retrospective_analyzer -> buy-time veto
FAILURE_RULES_PROPOSED_FILE = _DATA / "failure_dna_rules_proposed.json"  # a --report-only run: not read by the game
RETRO_REPORT_FILE = _DATA / "retrospective_report.txt"   # retrospective_analyzer human report
PICK_LOG_FILE = _DATA / "pick_lists_log.jsonl"          # daily email pick lists -> aether/pick_eval scorecard
RULE_SHADOW_FILE = _DATA / "rule_shadow.jsonl"
BREAKER_LAST_RUN_FILE = _DATA / "breaker_last_run.json"   # the breaker's verdict of its last run -> reports         # buy rules in shadow (plan T0.4) -> aether/rule_shadow scorer


def trim_jsonl(path, max_lines: int) -> None:
    """Keep the last ``max_lines`` lines. The rewrite goes to a temp file and replaces the log in one step, so a
    crash mid-trim leaves the old file, not a truncated one (#317 review)."""
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) <= max_lines:
        return
    tmp = p.with_name(p.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-max_lines:])
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_jsonl(path, entries, max_lines: int | None = None) -> None:
    """Append ``entries`` as JSON lines (creating the folder), then trim to ``max_lines``. Raises on failure: the
    caller decides whether a failed write is only a warning (the decision and shadow logs say so)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    if max_lines:
        trim_jsonl(p, max_lines)
