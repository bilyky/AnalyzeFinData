"""Log the daily email pick lists, and score how each list did later.

Every run that shows a list (aether/pick_lists.py) appends its picks to ledgers.PICK_LOG_FILE.
A later run scores each logged pick from the split-adjusted OHLCV cache: the close h trading days after the
entry minus the entry close, against SPY over the same days, for h in HORIZONS. The entry close
is the last close before the pick date for a list decided before the open ("prior_close"), and
the pick date's own close for one decided after it ("same_close"). The legacy morning log
(performance_tracker.LOG_FILE) is scored too, as "morning_top5_legacy".

Per list and horizon the scorecard gives: picks and dates scored, the mean return, the mean
excess over SPY (each date's picks averaged first, then the dates, since picks on one date
share one market move), and the share of dates that beat SPY. It is a running record of what
each list did, not a significance test: lists, rules and the market regime all change.
"""
import datetime
import json
import os
import statistics
import sys
from collections import defaultdict

import performance_tracker
from aether import ledgers, paths
from aether.risk_utils import split_adjust_ohlcv
from aether.logger import get_logger
from bar_provenance import real_dates

_log = get_logger("pick_eval")

HORIZONS = (5, 10, 20)
BENCHMARK = "SPY"


# ── Logging ────────────────────────────────────────────────────────────────────

def log_picks(plist, picks, day=None, path=None) -> None:
    """Append one list's picks for `day` (default today). Best effort: never raises."""
    day = str(day or datetime.date.today())
    try:
        path = path or ledgers.PICK_LOG_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for rank, p in enumerate(picks, 1):
                row = {str(k).lower(): v for k, v in p.items()}   # report dicts use "Symbol"/"S10"
                f.write(json.dumps({"date": day, "list": plist.key, "rank": rank,
                                    "symbol": str(row.get("symbol")).upper(),
                                    "score": round(plist.score(row), 3), "entry": plist.entry}) + "\n")
    except Exception as e:
        _log.warning(f"[pick_eval] could not log {plist.key}: {e}")


def read_logged(path=None, legacy_path=None) -> list:
    """Logged picks, one run per (date, list): the last run of the day wins."""
    rows, last = [], {}
    try:
        with open(path or ledgers.PICK_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                key = (r.get("date"), r.get("list"))
                if r.get("rank") == 1:              # a new run of that list starts at rank 1
                    last[key] = []
                last.setdefault(key, []).append(r)
    except OSError:
        pass
    for picks in last.values():
        rows.extend(picks)
    new_morning = {d for d, lst in last if lst == "morning_top5"}
    try:
        with open(legacy_path or performance_tracker.LOG_FILE, encoding="utf-8") as f:
            legacy = json.load(f)
        for day, picks in (legacy.items() if isinstance(legacy, dict) else []):
            # performance_tracker still writes the same morning picks; count each morning once.
            if len(str(day)) == 10 and isinstance(picks, list) and day not in new_morning:
                for rank, p in enumerate(picks, 1):
                    if isinstance(p, dict) and p.get("symbol"):
                        rows.append({"date": day, "list": "morning_top5_legacy", "rank": rank,
                                     "symbol": str(p["symbol"]).upper(), "entry": "prior_close"})
    except (OSError, ValueError):
        pass
    return rows


# ── Scoring ────────────────────────────────────────────────────────────────────

def _closes(symbol, cache):
    if symbol not in cache:
        try:
            with open(os.path.join(paths.ohlcv_dir(), f"{symbol}_daily.json"), encoding="utf-8") as f:
                ts = json.load(f).get("Time Series (Daily)", {})
            days = real_dates(ts)                  # placeholder bars never price an entry or exit
            col = {k: [float(ts[d][k]) for d in days] for k in ("1. open", "2. high", "3. low", "4. close")}
            # The cache is not split-adjusted (CRWD's 4:1 on 2026-07-01 read as -71%).
            _h, _l, closes = split_adjust_ohlcv(col["1. open"], col["2. high"], col["3. low"], col["4. close"])
            cache[symbol] = (days, list(closes))
        except (OSError, ValueError, KeyError, TypeError):
            cache[symbol] = ([], [])
    return cache[symbol]


def forward_return(symbol, day, entry, h, cache):
    """(return %, entry date) h trading days after the entry close, or None until it matures."""
    days, closes = _closes(symbol, cache)
    i = next((k for k, d in enumerate(days) if d >= day), None)
    if i is None:
        return None
    if entry == "prior_close" or days[i] > day:
        i -= 1                                      # decided before that day's open
    if i < 0 or i + h >= len(days) or closes[i] <= 0:
        return None
    return (closes[i + h] / closes[i] - 1) * 100, days[i]


def scorecard(rows, horizons=HORIZONS, cache=None) -> dict:
    """{list: {h: {"picks", "dates", "mean_ret", "mean_excess", "beat_spy_dates"}}}."""
    cache = {} if cache is None else cache
    by = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))   # list -> h -> date -> [excess, ret]
    for r in rows:
        for h in horizons:
            fr = forward_return(r["symbol"], r["date"], r.get("entry", "prior_close"), h, cache)
            spy = forward_return(BENCHMARK, r["date"], r.get("entry", "prior_close"), h, cache)
            if fr is None or spy is None:
                continue
            by[r["list"]][h][r["date"]].append((fr[0] - spy[0], fr[0]))
    out = {}
    for lst, hs in by.items():
        out[lst] = {}
        for h, dates in hs.items():
            per_date = [statistics.mean(x for x, _ in v) for v in dates.values()]
            out[lst][h] = {
                "picks": sum(len(v) for v in dates.values()),
                "dates": len(dates),
                "mean_ret": round(statistics.mean(r for v in dates.values() for _, r in v), 2),
                "mean_excess": round(statistics.mean(per_date), 2),
                "beat_spy_dates": round(sum(x > 0 for x in per_date) / len(per_date), 3),
            }
    return out


def render_html(card, titles=None) -> str:
    """The scorecard as an email section."""
    titles = titles or {}
    if not card:
        return ("<h3>How past picks did</h3><p><i>No logged pick has a full horizon of later "
                "prices yet (needs 5+ trading days).</i></p>")
    head = "".join(f"<th>{h}d excess vs SPY</th><th>{h}d beat SPY</th>" for h in HORIZONS)
    body = ""
    for lst in sorted(card):
        cells = ""
        for h in HORIZONS:
            m = card[lst].get(h)
            cells += (f"<td>{m['mean_excess']:+.2f}% <small>({m['dates']}d/{m['picks']}p)</small></td>"
                      f"<td>{m['beat_spy_dates']:.0%}</td>") if m else "<td>—</td><td>—</td>"
        body += f"<tr><td><b>{titles.get(lst, lst)}</b></td>{cells}</tr>"
    return (
        "<h3>How past picks did</h3>"
        "<p><i>Each list's logged picks, scored from the price cache: the close h trading days after "
        "the entry close, minus SPY over the same days. Picks on one date are averaged first "
        "(they share one market move); (Nd/Np) = dates/picks scored. A running record, not a "
        "significance test.</i></p>"
        f"<table><thead><tr><th>List</th>{head}</tr></thead><tbody>{body}</tbody></table>")


if __name__ == "__main__":
    sys.stdout.write(json.dumps(scorecard(read_logged()), indent=1) + "\n")
