"""
data_completeness.py — did today's download come in as complete as the days before?

Every field of every cached Chaikin snapshot (Data/Symbol/<SYM>/<SYM>_<date>.json) is
counted: how many stocks have it filled today vs the median of the last sessions. A field
that is usually filled and suddenly goes empty is an alarm, without anyone listing which
fields matter. That is how the 20 PGR factors went missing for two months from 2026-09-01:
an API change left them empty, and nothing compared one day with the next.

Also checked:
  - the number of snapshots vs the usual count;
  - values: the 12-char signal string is 0/1 only, the PGR rating is 1-5;
  - each stock's OHLCV (Data/Symbol_full) has a bar for the session;
  - each snapshot has its raw copy (Data/Symbol_raw), once that folder exists;
  - screen symbols (``expected``, the Research sheet) that were there in 8 of the last 10
    sessions and are missing today are named;
  - non-trading days (weekends and weekday holidays): snapshots are only a note, because the
    evening task runs daily and writes them routinely; there is no count or OHLCV bar check,
    and on a day without snapshots no field comparison. Junk still alarms through the value
    check (2026-05-25, Memorial Day: 56 snapshots with "-1-1-..." signals).

Read-only. ``check(day)`` returns a report; daily_task puts ``alarms`` at the top of the
evening email. CLI:

    python -m aether.data_completeness                 # latest session with snapshots
    python -m aether.data_completeness --date 2026-09-01
"""
import argparse
import datetime
import html
import json
import os
import re
import shutil
import statistics
import sys

from aether import market_calendar, paths

BASELINE_DAYS = 10          # prior sessions the fill rates are compared with
USUAL_FILL = 0.5            # a field filled for at least half the stocks counts as "usual"
DROP = 0.5                  # alarm when today's fill rate is under half the usual one
COUNT_DROP = 0.9            # alarm when there are under 90% of the usual number of snapshots
BAR_MISS = 0.1              # alarm when more than 10% of stocks have no OHLCV bar for the session
KEEPS_UP = 0.8              # a screen symbol present in 80% of the prior sessions is expected today
SIGNALS_RE = re.compile(r"^[01]{12}$")


DATA_GATE_KEY = "_data_gate_fields"   # game state: game-input fields that went empty (buy freeze memory)
# Snapshot fields the game's buy decisions read (field paths as built by fields()). Readers:
# powergauge.PowerGauge (PGR Value / Corrected PGR Value, the four factor groups that give money
# flow, OB/OS, LT trend and industry strength, metaInfo Last / signals / industry_name) and the
# R&D #32 guard (checklist strengthCount / timingCount). Only these can hold the buy freeze: on
# 2026-09-01 nine other fields (pgr.power_bar, pgr.beta, ...) went empty for good, and nothing
# reads them (PR #288 review).
GAME_INPUT_FIELDS = ("pgr.PGR Value", "pgr.Corrected PGR Value", "pgr.Financials", "pgr.Earnings",
                     "pgr.Technicals", "pgr.Experts", "meta.Last", "meta.signals", "meta.industry_name",
                     "checklist.strengthCount", "checklist.timingCount")


def is_game_input(field: str) -> bool:
    return any(field == p or field.startswith(p + ".") for p in GAME_INPUT_FIELDS)


def _filled(v) -> bool:
    return v not in (None, "", [], {})


def fields(bundle: dict) -> dict:
    """{field path: filled?} for one snapshot."""
    out = {}
    for e in bundle.get("pgr") or []:
        for k, v in e.items():
            out[f"pgr.{k}"] = _filled(v)
            if isinstance(v, list):
                for d in v:
                    for kk, vv in (d.items() if isinstance(d, dict) else ()):
                        out[f"pgr.{k}.{kk}"] = _filled(vv)
    meta = bundle.get("metaInfo") or [{}]
    for k, v in (meta[0] if meta and isinstance(meta[0], dict) else {}).items():
        out[f"meta.{k}"] = _filled(v)
    for k, v in (bundle.get("checklist_stocks") or {}).items():
        out[f"checklist.{k}"] = _filled(v)
    return out


def value_problems(bundle: dict) -> list:
    probs = []
    meta = (bundle.get("metaInfo") or [{}])[0] or {}
    sig = meta.get("signals")
    if sig is not None and not SIGNALS_RE.match(str(sig)):
        probs.append(f"signals '{str(sig)[:24]}'")
    pgr = {k: v for e in bundle.get("pgr") or [] for k, v in e.items()}
    for key in ("PGR Value", "Corrected PGR Value"):
        v = pgr.get(key)
        if v is not None and not (isinstance(v, int) and -1 <= v <= 5):    # 0 / -1 = unrated (-1: old API)
            probs.append(f"{key} {v!r}")
    return probs


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def day_stats(base: str, symbols: list, day: str) -> dict:
    """Fill rates for one session: {"n": snapshots, "rate": {field: share filled}, ...}."""
    counts, n, bad, loaded = {}, 0, {}, []
    for s in symbols:
        b = _read(os.path.join(base, s, f"{s}_{day}.json"))
        if not isinstance(b, dict) or b.get("status") not in ("ok", "success"):
            continue
        n += 1
        loaded.append(s)
        for f, ok in fields(b).items():
            counts[f] = counts.get(f, 0) + ok
        p = value_problems(b)
        if p:
            bad[s] = p
    return {"n": n, "rate": {f: c / n for f, c in counts.items()} if n else {}, "bad_values": bad,
            "symbols": loaded}


def _sessions_before(day: datetime.date, k: int) -> list:
    out, d = [], day
    while len(out) < k:
        d -= datetime.timedelta(days=1)
        if market_calendar.is_session(d):
            out.append(d.isoformat())
    return out


def _latest_day(base: str, symbols: list, today: datetime.date | None = None) -> str | None:
    d = today or datetime.date.today()
    for _ in range(10):
        if any(os.path.exists(os.path.join(base, s, f"{s}_{d.isoformat()}.json")) for s in symbols[:50]):
            return d.isoformat()
        d -= datetime.timedelta(days=1)
    return None


def check(day: str | None = None, base: str | None = None, expected: list | None = None,
          as_of: datetime.date | None = None) -> dict:
    """as_of: the calendar date the check runs on (default: today), for the last-session rule."""
    base = base or paths.symbol_dir()
    symbols = sorted(x for x in os.listdir(base) if os.path.isdir(os.path.join(base, x)))
    auto = day is None
    day = day or _latest_day(base, symbols, as_of)
    if day is None:
        msg = "no Chaikin snapshots in the last 10 days"
        return {"day": None, "alarms": [msg], "notes": [], "gate": [msg], "dropped_fields": [], "fill_rates": {}}
    today = day_stats(base, symbols, day)
    prior = [st for st in (day_stats(base, symbols, d) for d in _sessions_before(datetime.date.fromisoformat(day),
                                                                                BASELINE_DAYS)) if st["n"]]
    alarms, notes = [], []
    gate, dropped_fields = [], []       # alarms that mean the inputs are broken (the buy freeze reads these)
    d = datetime.date.fromisoformat(day)
    if auto:
        # The newest day with snapshots must be the last finished session: if the evening run
        # wrote nothing, a morning check would otherwise judge an older, healthy day (#288 review).
        last_session = _sessions_before(as_of or datetime.date.today(), 1)[0]
        if day < last_session:
            alarms.append(f"no snapshots for the last session {last_session} (newest: {day})")
            gate.append(alarms[-1])
    session = market_calendar.is_session(d)
    if today["n"] and not session:
        kind = "a weekend day" if d.weekday() >= 5 else "a weekday market holiday"
        notes.append(f"{today['n']} snapshots on {day}, {kind}")
    if prior:
        usual_n = statistics.median(st["n"] for st in prior)
        if session and today["n"] < COUNT_DROP * usual_n:
            alarms.append(f"{today['n']} snapshots, usually {usual_n:.0f}")
            gate.append(alarms[-1])
        dropped = []
        for f in sorted({f for st in prior for f in st["rate"]}) if today["n"] else []:
            usual = statistics.median(st["rate"].get(f, 0.0) for st in prior)
            now = today["rate"].get(f, 0.0)
            if usual >= USUAL_FILL and now < DROP * usual:
                dropped.append(f"{f} {now:.0%} (usually {usual:.0%})")
                if is_game_input(f):
                    dropped_fields.append(f)
        if dropped:
            alarms.append(f"{len(dropped)} fields went empty: " + "; ".join(dropped[:12])
                          + (" ..." if len(dropped) > 12 else ""))
        if dropped_fields:                      # only fields the game reads hold back buys
            gate.append(f"{len(dropped_fields)} game-input fields went empty: " + ", ".join(dropped_fields[:8])
                        + (" ..." if len(dropped_fields) > 8 else ""))
    else:
        notes.append("no earlier sessions to compare with")
    if today["bad_values"]:
        sample = "; ".join(f"{s}: {', '.join(p)}" for s, p in list(today["bad_values"].items())[:5])
        alarms.append(f"{len(today['bad_values'])} snapshots with bad values ({sample})")
        gate.append(alarms[-1])
    if expected and prior:
        seen = {}
        for st in prior:
            for s in st["symbols"]:
                seen[s] = seen.get(s, 0) + 1
        have = set(today["symbols"])
        gone = sorted(s for s in {x.upper() for x in expected}
                      if seen.get(s, 0) >= KEEPS_UP * len(prior) and s not in have)
        if gone:
            alarms.append(f"{len(gone)} screen symbols missing today: {', '.join(gone[:15])}"
                          + (" ..." if len(gone) > 15 else ""))
    no_bar = [s for s in today["symbols"] if not _has_bar(s, day)] if session else []
    if today["n"] and len(no_bar) > BAR_MISS * today["n"]:
        alarms.append(f"{len(no_bar)} of {today['n']} stocks have no OHLCV bar for {day}")
        gate.append(alarms[-1])
    elif no_bar:
        notes.append(f"{len(no_bar)} stocks without an OHLCV bar for {day}: {', '.join(no_bar[:10])}")
    raw_dir = os.path.join(paths.cache_dir(), "Symbol_raw")
    if os.path.isdir(raw_dir):
        no_raw = [s for s in today["symbols"]
                  if not os.path.exists(os.path.join(raw_dir, s, f"{s}_{day}.json.gz"))]
        if no_raw:
            alarms.append(f"{len(no_raw)} snapshots without a raw copy (Data/Symbol_raw)")
    # "gate": the alarms that mean today's inputs are broken (the game's data-health buy freeze);
    # the raw-copy and missing-screen-symbol alarms are left out on purpose. "fill_rates" lets the
    # freeze tell when fields that went empty are filled again.
    return {"day": day, "snapshots": today["n"], "baseline_sessions": len(prior), "alarms": alarms,
            "notes": notes, "gate": gate, "dropped_fields": dropped_fields, "fill_rates": today["rate"]}


_bars_cache: dict = {}


def _has_bar(symbol: str, day: str) -> bool:
    if symbol not in _bars_cache:
        b = _read(os.path.join(paths.ohlcv_dir(), f"{symbol}_daily.json")) or {}
        _bars_cache[symbol] = set((b.get("Time Series (Daily)") or {}).keys())
    return day in _bars_cache[symbol]


def html_banner(report: dict) -> str:
    """A block for the top of the evening email; empty when there is nothing to say."""
    if not report.get("alarms"):
        return ""
    items = "".join(f"<li>{html.escape(a)}</li>" for a in report["alarms"])
    return (f'<div style="border:2px solid #c0392b;padding:8px 12px;margin-bottom:16px;">'
            f'<b style="color:#c0392b;">DATA COMPLETENESS: {len(report["alarms"])} problem(s) in the '
            f'{report.get("day")} download</b><ul>{items}</ul>'
            f'<i>Check with: python -m aether.data_completeness --date {report.get("day")}</i></div>')


def game_file() -> str:
    return os.path.join(paths.data_dir(), "ai_portfolio_game.json")


def remembered_fields(path: str | None = None) -> list:
    """The game-input fields the buy freeze still remembers as empty ([] when none or unreadable)."""
    state = _read(path or game_file())
    return list(state.get(DATA_GATE_KEY) or []) if isinstance(state, dict) else []


def frozen_alarm(path: str | None = None) -> str | None:
    """A line for the evening email while the remembered fields keep new buys frozen."""
    f = remembered_fields(path)
    if not f:
        return None
    return (f"New buys frozen: {len(f)} game-input fields still empty since their outage ({', '.join(f[:6])}"
            + (" ..." if len(f) > 6 else "") + "). Release by hand only if the field is no longer used: "
            "python -m aether.data_completeness --accept-fields <field,...>")


def accept_fields(names: list, path: str | None = None, now: datetime.datetime | None = None) -> dict:
    """Drop fields from the buy-freeze memory (the owner accepts they stay empty). Backs up the game
    file to Data/Backup/<time>_pre_accept_fields first and replaces it atomically."""
    path = path or game_file()
    state = _read(path)
    if not isinstance(state, dict):
        return {"status": "error", "reason": f"cannot read {path}"}
    before = list(state.get(DATA_GATE_KEY) or [])
    unknown = [n for n in names if n not in before]
    after = [f for f in before if f not in names]
    if after == before:
        return {"status": "unchanged", "remembered": before, "unknown": unknown}
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(os.path.dirname(path), "Backup", f"{stamp}_pre_accept_fields")
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(path, os.path.join(bdir, os.path.basename(path)))
    if after:
        state[DATA_GATE_KEY] = after
    else:
        state.pop(DATA_GATE_KEY, None)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)
    return {"status": "updated", "remembered": after, "unknown": unknown, "backup": bdir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date", help="session to check (default: the latest with snapshots)")
    ap.add_argument("--accept-fields", help="comma-separated fields to drop from the buy-freeze memory "
                    "(backs up the game file first)")
    a = ap.parse_args(argv)
    if a.accept_fields:
        r = accept_fields([x.strip() for x in a.accept_fields.split(",") if x.strip()])
        sys.stdout.write(json.dumps(r, indent=1) + "\n")
        return 0 if r["status"] in ("updated", "unchanged") else 1
    r = check(a.date)
    sys.stdout.write(f"Data completeness for {r['day']}: {r.get('snapshots', 0)} snapshots, "
                     f"compared with {r.get('baseline_sessions', 0)} earlier sessions\n")
    for x in r["alarms"]:
        sys.stdout.write(f"  ALARM: {x}\n")
    for x in r["notes"]:
        sys.stdout.write(f"  note: {x}\n")
    if not r["alarms"]:
        sys.stdout.write("  OK\n")
    return 1 if r["alarms"] else 0


if __name__ == "__main__":
    sys.exit(main())
