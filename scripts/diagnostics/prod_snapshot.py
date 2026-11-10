"""Read a copied PROD snapshot (logs.zip + state files) for a review. Read-only; never writes outside DEST.

The `prod-review` skill (.claude/commands/prod-review.md) runs these in order. Each prints a compact
summary, so a review reads tens of lines instead of megabytes of log.

    extract    <logs.zip> <dest>                  safe unzip into a NEW empty folder; members newest first
    schedule   <agent_runs_dir> [--days N]        first log time per job per day (catches DST / missed runs)
    errors     <aether.jsonl> --since TS [--top N] WARNING/ERROR grouped by normalized message, first/last
    signatures <aether.jsonl> --since TS --sig LABEL=TEXT ...   which code ran: counts of distinctive lines
    config     <config.json> --key a.b.c ...      value or presence of keys; text and numbers shown only for known-safe keys

TS is "YYYY-MM-DD HH:MM" in the log's local time. Run with `python -I` from outside the extracted folder.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys
import zipfile

_NUM = re.compile(r"\d+(\.\d+)?")
# Long words anywhere in a key name (api_key_source, smtp_password, username; "tokens", a count, is not one); short
# ones at the end (ip, id), so "provider" or "ideal" are not caught.
_SECRET_KEY = re.compile(r"key|token(?!s)|secret|pass|pwd|auth|cookie|session|account|acct|email|url|host|share|"
                         r"user|(ip|id|dir|path)$", re.I)
# Deny by default: text prints only under _SAFE_TEXT_KEY names and numbers only under _SAFE_NUM_KEY
# names. A login, a note or a money value such as oracle.start_equity shows <set>. Booleans and null print.
_SAFE_TEXT_KEY = re.compile(r"(type|model|primary|provider|mode|env|environment|level|profile|tier|strategy|"
                            r"reasoning_effort|timezone)$", re.I)
_SAFE_NUM_KEY = re.compile(r"^(max|min)_|_(max|min|pct|ratio|limit|floor|sec|seconds|minutes|hours|days|per_run)$|"
                           r"^port$|timeout|retries", re.I)
_TS_LINE = re.compile(r"^\[?(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})")


def _out(line=""):
    sys.stdout.write(f"{line}\n")


def extract(zip_path, dest):
    """Unzip into dest, which must not exist or be empty; refuse members that escape it."""
    if os.path.isdir(dest) and os.listdir(dest):
        raise SystemExit(f"{dest} is not empty; extract into a new folder")
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        for i in infos:
            target = os.path.realpath(os.path.join(dest, i.filename))
            if os.path.commonpath([root, target]) != root:
                raise SystemExit(f"refusing member outside {dest}: {i.filename}")
        z.extractall(dest)
    rows = sorted(((datetime.datetime(*i.date_time), i.file_size, i.filename) for i in infos if not i.is_dir()),
                  reverse=True)
    return [(t.strftime("%m-%d %H:%M"), size, name) for t, size, name in rows]


def schedule(runs_dir, days=3):
    """{log file: [(day, first time), ...]} for the last `days` days seen in each log."""
    out = {}
    for name in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, name)
        if not os.path.isfile(path):
            continue
        first = {}
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = _TS_LINE.match(line)
                if m:
                    first.setdefault(m.group(1), m.group(2))
        if first:
            out[name] = sorted(first.items())[-days:]
    return out


def _events(jsonl, since):
    with open(jsonl, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if str(e.get("ts", "")) >= since:
                yield e


def errors(jsonl, since, levels=("WARNING", "ERROR", "CRITICAL")):
    """(level counts, [(count, level, module, first, last, normalized message)]) sorted by count."""
    levels_seen, groups = collections.Counter(), {}
    for e in _events(jsonl, since):
        level = str(e.get("level", "")).upper()
        levels_seen[level] += 1
        if level not in levels:
            continue
        msg = _NUM.sub("N", str(e.get("msg", "")).splitlines()[0] if e.get("msg") else "")[:160]
        key = (level, e.get("module", ""), msg)
        g = groups.setdefault(key, [0, e["ts"], e["ts"]])
        g[0] += 1
        g[2] = e["ts"]
    rows = [(n, lv, mod, first, last, msg) for (lv, mod, msg), (n, first, last) in groups.items()]
    return levels_seen, sorted(rows, key=lambda r: (-r[0], r[1], r[5]))


def signatures(jsonl, since, sigs):
    """{label: (count, first ts, last ts)} for each LABEL=TEXT substring of a message (or its extras)."""
    found = {label: [0, None, None] for label in sigs}
    for e in _events(jsonl, since):
        text = str(e.get("msg", "")) + " " + json.dumps({k: v for k, v in e.items() if k not in ("ts", "msg")},
                                                         default=str)
        for label, needle in sigs.items():
            if needle in text:
                f = found[label]
                f[0] += 1
                f[1] = f[1] or e["ts"]
                f[2] = e["ts"]
    return {k: tuple(v) for k, v in found.items()}


def _looks_secret(value):
    s = str(value)
    return bool(re.search(r"[A-Za-z0-9_\-]{20,}", s) or "@" in s or re.search(r"\d+\.\d+\.\d+\.\d+", s)
                or "\\" in s or "://" in s or re.search(r"\d{4,}", s))


def config_keys(config_path, keys):
    """{dotted key: shown value}; a missing key shows <missing>. Text and numbers are shown only under a
    known-safe key name (_SAFE_TEXT_KEY, _SAFE_NUM_KEY) that doesn't look secret; anything else shows <set>."""
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    out = {}
    for dotted in keys:
        node, ok = cfg, True
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if not ok:
            out[dotted] = "<missing>"
        elif isinstance(node, (dict, list)):
            out[dotted] = f"<{type(node).__name__} with {len(node)} entries>"
        elif node is None or isinstance(node, bool):
            out[dotted] = node
        elif isinstance(node, (int, float)):
            # Only limits, ports and timings print; a balance, an equity baseline or an id doesn't.
            last = dotted.split(".")[-1]
            out[dotted] = node if _SAFE_NUM_KEY.search(last) and not _SECRET_KEY.search(last) else "<set>"
        elif node == "":
            out[dotted] = "<empty>"
        else:
            last = dotted.split(".")[-1]
            safe = _SAFE_TEXT_KEY.search(last) and not _SECRET_KEY.search(last) and not _looks_secret(node)
            out[dotted] = node if safe else "<set>"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract")
    p.add_argument("zip")
    p.add_argument("dest")
    p = sub.add_parser("schedule")
    p.add_argument("runs_dir")
    p.add_argument("--days", type=int, default=3)
    p = sub.add_parser("errors")
    p.add_argument("jsonl")
    p.add_argument("--since", required=True)
    p.add_argument("--top", type=int, default=40)
    p = sub.add_parser("signatures")
    p.add_argument("jsonl")
    p.add_argument("--since", required=True)
    p.add_argument("--sig", action="append", required=True, help="LABEL=TEXT, repeatable")
    p = sub.add_parser("config")
    p.add_argument("config")
    p.add_argument("--key", action="append", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "extract":
        for when, size, name in extract(a.zip, a.dest):
            _out(f"{when}  {size:>10}  {name}")
    elif a.cmd == "schedule":
        for name, days in schedule(a.runs_dir, a.days).items():
            _out(f"{name:<36} " + "  ".join(f"{d} {t}" for d, t in days))
    elif a.cmd == "errors":
        levels, rows = errors(a.jsonl, a.since)
        _out(f"levels since {a.since}: {dict(levels)}")
        for n, lv, mod, first, last, msg in rows[:a.top]:
            _out(f"{n:>5} {lv[:4]} {mod} {first[5:16]}..{last[5:16]} | {msg}")
    elif a.cmd == "signatures":
        sigs = dict(s.split("=", 1) for s in a.sig)
        for label, (n, first, last) in signatures(a.jsonl, a.since, sigs).items():
            _out(f"{n:>5}  {label:<40} {first or '-'} .. {last or '-'}")
    elif a.cmd == "config":
        for k, v in config_keys(a.config, a.key).items():
            _out(f"{k} = {v}")


if __name__ == "__main__":
    main()
