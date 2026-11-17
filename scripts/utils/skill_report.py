"""Run a skill's read-only checks in Python and have our AI layer write its report (no Gemini CLI).

The Gemini CLI agent tasks failed with HTTP 403 on PROD every run. For a report-style skill
(``.claude/commands/<skill>.md``) this script does what the agent did, without the CLI:

1. runs every ```bash block of the skill that is exactly ``python -c "..."`` (read-only checks;
   a block that runs a script or anything else is never run), each under its ``## `` heading.
   A block that mentions a broker call, email, a subprocess, a network call or a file write is
   refused and reported instead (``not_read_only``);
2. sends the outputs and the skill's "## Report format" section to ``aether.ai_step`` (config
   ``ai``, default tier ``cheap``) and emails the report;
3. if the AI step is skipped, emails the raw outputs with the reason instead.

    python scripts/utils/skill_report.py --skill status --title "Portfolio status" --sessions-only
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from aether import ai_step, market_calendar, notify
from aether_logger import get_logger as _get_logger

_log = _get_logger("skill_report")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SKILLS = BASE_DIR / ".claude" / "commands"
CHECK_TIMEOUT_S = 180
_BLOCK = re.compile(r"```bash\n(.*?)\n```", re.S)
_PY_C = re.compile(r'\Apython -c "(.*)"\Z', re.S)


# A check runs unattended on PROD, so a block that names a broker call, email, a subprocess, a
# network call or a file write is refused, not run. It's a text check: it can't see what a
# called function does inside (tests/test_skill_report.py pins the status skill's blocks).
_NOT_READ_ONLY = re.compile(
    r"\betrade\b|get_live_prices|send_email|\bnotify\b|subprocess|os\.system|\brequests\b|urllib|"
    r"open\([^)]*,\s*(?:mode\s*=\s*)?['\"][rbt]*[wax+][rbt+]*['\"]|\.write\(|json\.dump\(|to_excel|\.save\(|os\.(remove|unlink|rename|replace)|"
    r"shutil|rmtree|unlink\(", re.I)


@dataclass
class Check:
    label: str
    code: str


def _today() -> datetime.date:
    return datetime.date.today()


def _unescape_bash_double_quoted(s: str) -> str:
    """Undo bash's escaping inside "...": \\" \\$ \\` and \\\\ (other backslashes stay)."""
    return re.sub(r'\\([\\"$`])', r"\1", s)


def extract_checks(skill_text: str) -> list:
    """Every ```bash block that is exactly ``python -c "..."``, labelled with its ## heading."""
    checks = []
    for m in _BLOCK.finditer(skill_text):
        py = _PY_C.match(m.group(1).strip())
        if not py:
            continue
        heads = re.findall(r"^## (.+)$", skill_text[:m.start()], re.M)
        checks.append(Check(heads[-1].strip() if heads else f"check {len(checks) + 1}",
                            _unescape_bash_double_quoted(py.group(1)).strip()))
    return checks


def report_instructions(skill_text: str) -> str:
    """The skill's "## Report format" section (or the whole skill when it has none)."""
    i = skill_text.find("## Report format")
    head = skill_text.split("\n## ", 1)[0]
    return (head + "\n\n" + skill_text[i:]) if i >= 0 else skill_text


def not_read_only(code: str) -> str:
    """The first text in a check that marks it as more than a read, or "" when there is none."""
    m = _NOT_READ_ONLY.search(code)
    return m.group(0) if m else ""


def _run_check(check: Check) -> str:
    bad = not_read_only(check.code)
    if bad:
        _log.warning(f"skill check '{check.label}' refused: it mentions {bad!r}; scheduled checks must only read.")
        return f"REFUSED, not run: the check mentions {bad!r}; a scheduled skill check must only read."
    try:
        r = subprocess.run([sys.executable, "-c", check.code], cwd=str(BASE_DIR), capture_output=True,
                           text=True, errors="replace", timeout=CHECK_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"FAILED (timed out after {CHECK_TIMEOUT_S}s)"
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return f"FAILED (exit {r.returncode})\n{out}\n{(r.stderr or '').strip()[-1500:]}".strip()
    return out or "(no output)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skill", required=True, help="skill file name in .claude/commands, without .md")
    ap.add_argument("--title", required=True, help="report name used in the email subject")
    ap.add_argument("--tier", default="cheap")
    ap.add_argument("--sessions-only", action="store_true", help="do nothing on days NYSE is closed")
    args = ap.parse_args()

    today = _today()
    if args.sessions_only and not market_calendar.is_session(today):
        _log.info(f"{args.title}: {today} is not an NYSE session; nothing to report.")
        return 0
    text = (SKILLS / f"{args.skill}.md").read_text(encoding="utf-8")
    checks = extract_checks(text)
    outputs = "\n\n".join(f"## {c.label}\n{_run_check(c)}" for c in checks)
    result = ai_step.run(f"{args.title} report", report_instructions(text) +
                         "\n\nUse only the check outputs given; never invent a value. Say plainly when a check failed.",
                         f"Date: {today}\n\n{outputs}", tier=args.tier, max_tokens=1200, notify_on_skip=False)
    if result.text:
        notify.send_email(f"AETHER {args.title}: {today}", f"{result.text}\n\n(written by {result.provider})")
    else:
        notify.send_email(f"AETHER {args.title}: {today} (AI report skipped)",
                          f"The checks ran; the AI report was skipped.\n\nReason: {result.skipped}\n\n{outputs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
