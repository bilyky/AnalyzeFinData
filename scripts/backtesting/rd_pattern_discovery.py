"""Weekly R&D pattern discovery without the Gemini CLI (scheduled as AETHER_PatternDiscovery).

The AETHER_RD_Scientist agent task ran this through the Gemini CLI and failed with HTTP 403 every
Saturday from at least 2026-09-19 to 2026-11-14, so no report was written after the 08-18 replay.

1. Computation (Python): ``pattern_discovery.py --date <today - 25 days> --top 10`` writes
   ``Data/pattern_discovery_<date>.json`` (the 25-day lag lets the 10-day window settle, as the
   pattern-discover skill says).
2. Analysis (our AI layer, config ``ai``): the skill's own analysis steps (Step 3 to the end of
   ``.claude/commands/pattern-discover.md``) plus the report go to ``aether.ai_step``. The answer
   is saved as ``Data/pattern_discovery_<date>_ai.md`` and emailed.
3. If the AI step can't run, it is skipped: the email says why and carries the computed counts,
   and the report stays on disk. Only a failed computation makes the task exit 1.

    python scripts/backtesting/rd_pattern_discovery.py              # replay today - 25 days
    python scripts/backtesting/rd_pattern_discovery.py --date 2026-10-22
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from aether import ai_step, notify
from aether_logger import get_logger as _get_logger

_log = _get_logger("rd_pattern_discovery")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUT_DIR = BASE_DIR / "Data"                       # where pattern_discovery.py writes its report
SKILL = BASE_DIR / ".claude" / "commands" / "pattern-discover.md"
SCRIPT = BASE_DIR / "scripts" / "backtesting" / "pattern_discovery.py"
REPLAY_LAG_DAYS = 25
COMPUTE_TIMEOUT_S = 900
MAX_REPORT_CHARS = 40000                          # keep the prompt inside the providers' context


def replay_date(today: datetime.date) -> str:
    return (today - datetime.timedelta(days=REPLAY_LAG_DAYS)).isoformat()


def analysis_instructions() -> str:
    """The skill's analysis steps (Step 3 to the end): one source for what the analysis should do."""
    text = SKILL.read_text(encoding="utf-8")
    start = text.find("## Step 3")
    return text[start:] if start >= 0 else text


def _counts(report: dict) -> str:
    def part(key):
        a = report.get(key) or {}
        return (f"{len(a.get('missed') or [])} missed, {len(a.get('false_positives') or [])} false positives, "
                f"universe avg {a.get('universe_avg')}")
    return f"S10: {part('s10_analysis')}\nL60: {part('l60_analysis')}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", help="Replay date YYYY-MM-DD (default: today - 25 days)")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    day = args.date or replay_date(datetime.date.today())

    cmd = [sys.executable, str(SCRIPT), "--date", day, "--top", str(args.top)]
    _log.info(f"Pattern discovery for {day}: {' '.join(cmd[1:])}")
    try:
        res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, errors="replace",
                             timeout=COMPUTE_TIMEOUT_S, stdin=subprocess.DEVNULL)
        failed = res.returncode != 0
        detail = (res.stderr or res.stdout or "")[-2000:]
    except subprocess.TimeoutExpired:
        failed, detail = True, f"timed out after {COMPUTE_TIMEOUT_S}s"
    report_path = OUT_DIR / f"pattern_discovery_{day}.json"
    if failed or not report_path.exists():
        _log.error(f"Pattern discovery computation failed for {day}: {detail[-300:]}")
        notify.send_email(f"AETHER R&D: pattern discovery {day} failed",
                          f"pattern_discovery.py did not produce {report_path.name}.\n\n{detail}")
        return 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    payload = json.dumps(report, default=str)[:MAX_REPORT_CHARS]
    result = ai_step.run(f"pattern discovery {day}", analysis_instructions(),
                         f"DATE = {day}\nReport JSON (Data/{report_path.name}):\n{payload}",
                         tier="strong", max_tokens=3000, notify_on_skip=False)
    if result.text:
        ai_path = OUT_DIR / f"pattern_discovery_{day}_ai.md"
        ai_path.write_text(result.text + "\n", encoding="utf-8")
        notify.send_email(f"AETHER R&D: pattern discovery {day}",
                          f"{_counts(report)}\n\nAI analysis ({result.provider}), also saved as {ai_path.name}:\n\n{result.text}")
    else:
        notify.send_email(f"AETHER R&D: pattern discovery {day} (AI analysis skipped)",
                          f"The computation finished; the AI analysis was skipped.\n\nReason: {result.skipped}\n\n"
                          f"{_counts(report)}\n\nThe report is in Data/{report_path.name}. Run the "
                          "pattern-discover skill by hand on it, or fix the 'ai' providers in config.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
