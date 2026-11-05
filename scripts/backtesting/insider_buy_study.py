"""
insider_buy_study.py — after an insider's open-market purchase, does the stock beat the market?

Question (R&D, 2026-11-05): position-watch fired its first TRIM on INTC on 2026-11-04, two
days after the CEO bought 150,000 shares on the open market at $64.73 (Form 4 filed 11-04).
Should an open-market purchase by an officer or director pause trims? Only if such purchases
are followed by the stock beating the market.

Events: SEC Form 4 (DOCUMENT_TYPE "4"), non-derivative transaction code P (open-market or
private purchase), acquired, by a reporting owner whose relationship includes Director or
Officer (pure 10% owners excluded). Per symbol and filing date the purchases are summed; an
event needs at least MIN_VALUE dollars. One event per symbol per SPACING sessions (the first),
so overlapping windows of a buying spree count once. Source: the SEC's quarterly insider
transaction data sets (structured Form 3/4/5 tables), cached in Data/insider_cache/.

Timing (no look-ahead): the decision day is the filing date (the buy is public that day,
possibly after the close). Returns start at the close of the first session AFTER the filing
date and run h sessions. Outcome: the stock's return minus the equal-weight return of the
universe over the same sessions (market and universe drift removed).

TRIM setup, from position-watch's own functions (scripts/monitoring/position_watch.py, rules
v2): on the last session up to the filing date the stock is below its 50-day average
(symbol_metrics vs_sma50 < 0), the day is not a crash day (ret_1 >= CRASH_DAY) and it is weak
on its own (beta_residual z_20 < WEAK_Z). Deviation: position-watch uses a configured peer
basket (INTC's chip peers); here the basket is the equal-weight universe, because no peer
list exists for other symbols.

Statistics: events on the same days share one market move, so the event excess returns are
averaged per calendar month of the filing, and the t-stat is Newey-West over those monthly
means: lag 0 for 20 sessions (about a month), lag 2 for 60 sessions (about three months).

DECISION RULE (fixed 2026-11-05, before any result):
  primary  = all events, 60-session excess.
  subset   = events in the TRIM setup, 60-session excess.
  - PAUSE_SUPPORTED: primary mean > 0 with NW t >= 1.96, AND subset n >= MIN_SUBSET with a
    mean > 0. -> propose (not wire) an "insider buy pauses trims" rule for position-watch.
  - EDGE_NOT_IN_TRIM_SETUP: primary passes, but the subset is too small or its mean <= 0.
    -> no rule.
  - NO_EDGE: primary NW t <= -1.96. -> no rule; the CEO-buy argument is not supported here.
  - INCONCLUSIVE: anything else. -> no rule.
  The 20-session figures, CEO-only and $1M+ events, and the by-period split are context
  only, not part of the verdict.

SECOND TEST (added 2026-11-05 AFTER the first result was INCONCLUSIVE, on the user's
question; disclosed as such). Research on insider trading (Cohen, Malloy & Pomorski 2012)
finds routine purchases carry no information and opportunistic ones do. Definitions, fixed
before this run:
  - routine buyer: the insider bought this stock in the same calendar month in at least 2
    of the 3 previous calendar years; opportunistic = anything else (first-time buyers too).
    An event is opportunistic if at least one buyer that day is. Only filings from
    SPLIT_FROM on (three years of history needed; data starts 2012).
  - after a big drop: the stock's own return over the 20 sessions up to the decision day
    is DROP_20 or worse (INTC on 2026-11-02: about -48%).
  DECISION RULE: opportunistic buys after a big drop, 60-session excess:
  - DROP_PAUSE_SUPPORTED: n >= MIN_SUBSET, mean > 0 and NW t >= 1.96.
  - otherwise INCONCLUSIVE (NO_EDGE if t <= -1.96). No rule unless supported.
  Routine buys, opportunistic buys without a drop, and the TRIM-setup slice are context.
  Also context (added on the user's "black swan" question, not a test): opportunistic buys
  within SHOCK_WINDOW sessions after a single-day drop of SHOCK_DAY or worse (INTC: -29% on
  2026-10-30, the CEO bought one session later).
  This is the second test on the same data, so a lone pass deserves caution.

Known biases: today's universe (the cache holds symbols that exist today, plus a few
delisted ones), matched by the ticker on the filing (renamed companies are missed), no
trading costs, no stop-outs.

Usage:
    python scripts/backtesting/insider_buy_study.py --fetch          # download/cache quarters
    python scripts/backtesting/insider_buy_study.py                  # run on the cache
"""
import argparse
import csv
import datetime
import io
import json
import math
import os
import re
import statistics
import sys
import time
import zipfile

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "monitoring"))

import position_watch as pw  # noqa: E402
from aether import instruments, paths  # noqa: E402

FIRST_Q, LAST_Q = "2012q1", "2026q3"
INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
# The SEC asks for a contact address in the User-Agent and serves a block page without one.
UA = "AnalyzeFinData research admin@example.com"
MIN_VALUE = 100_000
BIG_VALUE = 1_000_000
SPACING = 20
HORIZONS = (20, 60)
NW_LAG = {20: 0, 60: 2}
MIN_SUBSET = 30
SPLIT_FROM = "2015-01-01"
DROP_20 = -0.20
SHOCK_DAY = -0.15
SHOCK_WINDOW = 10
DAILY_CAP = 0.5            # winsorize daily returns in the equal-weight index at +/-50%
csv.field_size_limit(10_000_000)


def _out(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


def cache_dir() -> str:
    return os.path.join(paths.data_dir(), "insider_cache")


# ── Data: SEC quarterly insider transaction data sets ─────────────────────────

def quarter_names(first=FIRST_Q, last=LAST_Q) -> list:
    y, q = int(first[:4]), int(first[5])
    ly, lq = int(last[:4]), int(last[5])
    out = []
    while (y, q) <= (ly, lq):
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def fetch(session, proxies=None) -> dict:
    """Download the missing quarter files listed on the SEC index page (paced, stops after 3
    failures in a row). Returns {"fetched": [...], "cached": [...], "failed": [...]}."""
    os.makedirs(cache_dir(), exist_ok=True)
    resp = session.get(INDEX_URL, proxies=proxies, timeout=60)
    links = {m.split("/")[-1][:6]: m for m in re.findall(r'href="([^"]*form345[^"]*\.zip)"', resp.text)}
    if not links:
        raise RuntimeError(f"no data set links on the SEC index page (HTTP {resp.status_code}); "
                           f"check the User-Agent and proxy")
    res = {"fetched": [], "cached": [], "failed": []}
    fails = 0
    for q in quarter_names():
        path = os.path.join(cache_dir(), f"{q}_form345.zip")
        if os.path.exists(path):
            res["cached"].append(q)
            continue
        if q not in links:
            res["failed"].append(q)
            continue
        try:
            r = session.get("https://www.sec.gov" + links[q], proxies=proxies, timeout=300)
            r.raise_for_status()
            with open(path + ".tmp", "wb") as f:
                f.write(r.content)
            os.replace(path + ".tmp", path)
            res["fetched"].append(q)
            fails = 0
        except Exception as e:  # network or HTTP error: count it, keep going, stop after three in a row
            res["failed"].append(q)
            fails += 1
            _out(f"  {q}: {e}")
            if fails >= 3:
                break
        time.sleep(1.0)
    return res


def _rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_purchases(z: zipfile.ZipFile) -> list:
    """Officer/director open-market purchases in one quarter file:
    [{"symbol", "filed" (ISO), "value", "ceo"}] (one row per purchase line)."""
    subs = {}
    for r in _rows(z, "SUBMISSION.tsv"):
        if r.get("DOCUMENT_TYPE") == "4" and r.get("ISSUERTRADINGSYMBOL"):
            subs[r["ACCESSION_NUMBER"]] = r
    owners = {}
    for r in _rows(z, "REPORTINGOWNER.tsv"):
        rel = (r.get("RPTOWNER_RELATIONSHIP") or "").lower()
        title = (r.get("RPTOWNER_TITLE") or "").lower()
        o = owners.setdefault(r["ACCESSION_NUMBER"], {"insider": False, "ceo": False, "ciks": []})
        if "director" in rel or "officer" in rel:
            o["insider"] = True
            o["ciks"].append((r.get("RPTOWNERCIK") or "").strip())
        o["ceo"] |= "ceo" in title or "chief executive" in title
    out = []
    for r in _rows(z, "NONDERIV_TRANS.tsv"):
        acc = r["ACCESSION_NUMBER"]
        if (r.get("TRANS_CODE") != "P" or r.get("TRANS_ACQUIRED_DISP_CD") != "A" or acc not in subs
                or not owners.get(acc, {}).get("insider")):
            continue
        shares, price = _num(r.get("TRANS_SHARES")), _num(r.get("TRANS_PRICEPERSHARE"))
        if not shares or not price:
            continue
        filed = _iso(subs[acc]["FILING_DATE"])
        if filed:
            out.append({"symbol": subs[acc]["ISSUERTRADINGSYMBOL"].strip().upper(), "filed": filed,
                        "value": shares * price, "ceo": owners[acc]["ceo"],
                        "owners": tuple(owners[acc]["ciks"]), "traded": _iso(r.get("TRANS_DATE") or "") or filed})
    return out


def _iso(s):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            continue
    return None


def events_from(purchases: list, min_value=MIN_VALUE) -> list:
    """Sum purchases per (symbol, filing date); keep those worth at least min_value."""
    agg = {}
    for p in purchases:
        e = agg.setdefault((p["symbol"], p["filed"]), {"symbol": p["symbol"], "filed": p["filed"], "value": 0.0,
                                                        "ceo": False, "buys": []})
        e["value"] += p["value"]
        e["ceo"] |= p["ceo"]
        e["buys"] += [(o, p.get("traded", p["filed"])) for o in p.get("owners", ())]
    return sorted((e for e in agg.values() if e["value"] >= min_value), key=lambda e: (e["symbol"], e["filed"]))


def buy_history(purchases: list) -> dict:
    """{(owner, symbol): {(year, month), ...}} of every purchase's trade month."""
    h: dict = {}
    for p in purchases:
        t = p.get("traded", p["filed"])
        for o in p.get("owners", ()):
            h.setdefault((o, p["symbol"]), set()).add((int(t[:4]), int(t[5:7])))
    return h


def is_routine(history: dict, owner: str, symbol: str, traded: str) -> bool:
    """Bought this stock in the same calendar month in at least 2 of the 3 previous years."""
    months = history.get((owner, symbol), set())
    y, m = int(traded[:4]), int(traded[5:7])
    return sum((y - k, m) in months for k in (1, 2, 3)) >= 2


def opportunistic(event: dict, history: dict) -> bool:
    """At least one buyer that day isn't routine (an event with no owner data counts as opportunistic)."""
    buys = event.get("buys") or []
    return not buys or any(not is_routine(history, o, event["symbol"], t) for o, t in buys)


# ── Returns ──────────────────────────────────────────────────────────────────

def worst_day(b: dict, i: int, window: int) -> float:
    """The worst single-day return over the `window` sessions up to and including bar i."""
    c = b["c"]
    return min((c[k] / c[k - 1] - 1 for k in range(max(1, i - window + 1), i + 1) if c[k - 1]), default=0.0)


def ew_index(bars: dict) -> dict:
    """{date: equal-weight daily return across the universe} (daily returns capped)."""
    sums: dict = {}
    for b in bars.values():
        c, days = b["c"], b["days"]
        for k in range(1, len(days)):
            if c[k - 1]:
                r = max(-DAILY_CAP, min(DAILY_CAP, c[k] / c[k - 1] - 1))
                s = sums.setdefault(days[k], [0.0, 0])
                s[0] += r
                s[1] += 1
    return {d: s / n for d, (s, n) in sums.items() if n}


def _compound(idx: dict, days: list) -> float:
    g = 1.0
    for d in days:
        g *= 1 + idx.get(d, 0.0)
    return g - 1


def decision_index(b: dict, filed: str):
    """Index of the last session on or before the filing date (None if none)."""
    lo, hi, ans = 0, len(b["days"]) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if b["days"][mid] <= filed:
            ans, lo = mid, mid + 1
        else:
            hi = mid - 1
    return ans


def outcome(b: dict, idx: dict, i: int, h: int):
    """Excess return from the close of session i+1 (first after the filing) to i+1+h."""
    s, e = i + 1, i + 1 + h
    if e >= len(b["days"]) or not b["c"][s]:
        return None
    return (b["c"][e] / b["c"][s] - 1) - _compound(idx, b["days"][s + 1:e + 1])


def trim_setup(b: dict, idx: dict, i: int) -> bool:
    """position-watch's TRIM conditions on bar i, with the equal-weight universe as the basket."""
    if i < 260:
        return False
    m = pw.symbol_metrics(b, i)
    if m["vs_sma50"] is None or m["vs_sma50"] >= 0 or m["ret_1"] is None or m["ret_1"] < pw.CRASH_DAY:
        return False
    w = b["days"][i - pw.BETA_WINDOW - 25:i + 1]
    sym = [math.log(b["c"][b["idx"][y]] / b["c"][b["idx"][x]]) for x, y in zip(w, w[1:])]
    basket = [math.log(1 + idx.get(d, 0.0)) for d in w[1:]]
    rb = pw.beta_residual(sym, basket)
    return bool(rb) and rb["z_20"] < pw.WEAK_Z


# ── Statistics and verdict ────────────────────────────────────────────────────

def nw_t(xs: list, lag: int):
    """Newey-West t of the mean of a series (Bartlett weights)."""
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    d = [x - m for x in xs]
    var = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * sum(d[t] * d[t - k] for t in range(k, n)) / n
    return m / math.sqrt(var / n) if var > 0 else None


def summarize(rows: list, key: str, lag: int) -> dict:
    vals = [r for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0}
    by_m: dict = {}
    for r in vals:
        by_m.setdefault(r["filed"][:7], []).append(r[key])
    monthly = [sum(v) / len(v) for _m, v in sorted(by_m.items())]
    xs = [r[key] for r in vals]
    return {"n": len(xs), "months": len(monthly), "mean": sum(xs) / len(xs), "median": statistics.median(xs),
            "hit": sum(x > 0 for x in xs) / len(xs), "t_nw": nw_t(monthly, lag)}


def verdict(primary: dict, subset: dict) -> str:
    t = primary.get("t_nw")
    if t is not None and primary.get("mean", 0) > 0 and t >= 1.96:
        if subset.get("n", 0) >= MIN_SUBSET and subset.get("mean", 0) > 0:
            return "PAUSE_SUPPORTED"
        return "EDGE_NOT_IN_TRIM_SETUP"
    if t is not None and t <= -1.96:
        return "NO_EDGE"
    return "INCONCLUSIVE"


def verdict_drop(sub: dict) -> str:
    t = sub.get("t_nw")
    if sub.get("n", 0) >= MIN_SUBSET and sub.get("mean", 0) > 0 and t is not None and t >= 1.96:
        return "DROP_PAUSE_SUPPORTED"
    if t is not None and t <= -1.96:
        return "NO_EDGE"
    return "INCONCLUSIVE"


# ── Driver ───────────────────────────────────────────────────────────────────

def build_rows(events: list, bars: dict, idx: dict, history: dict | None = None) -> list:
    """One row per kept event: the first per symbol within SPACING sessions, with its decision
    day, TRIM-setup flag and excess returns. Events whose symbol isn't in the universe drop."""
    rows, last = [], {}
    for e in events:
        b = bars.get(e["symbol"])
        if b is None:
            continue
        i = decision_index(b, e["filed"])
        if i is None or i < 1 or (e["symbol"] in last and i - last[e["symbol"]] < SPACING):
            continue
        last[e["symbol"]] = i
        row = {k: v for k, v in e.items() if k != "buys"}
        row.update(decision_day=b["days"][i], trim_setup=trim_setup(b, idx, i),
                   ret20=b["c"][i] / b["c"][i - 20] - 1 if i >= 20 and b["c"][i - 20] else None,
                   shock=worst_day(b, i, SHOCK_WINDOW) <= SHOCK_DAY,
                   opportunistic=opportunistic(e, history or {}))
        for h in HORIZONS:
            row[f"x{h}"] = outcome(b, idx, i, h)
        rows.append(row)
    return rows


def load_universe() -> dict:
    bars = {}
    for fn in sorted(os.listdir(paths.ohlcv_dir())):
        if fn.endswith("_daily.json"):
            sym = fn[:-len("_daily.json")]
            if instruments.classify(sym) == "normal":
                b = pw.load_bars(sym)
                if b and len(b["days"]) >= 200:
                    bars[sym] = b
    return bars


def run() -> dict:
    purchases = []
    for q in quarter_names():
        p = os.path.join(cache_dir(), f"{q}_form345.zip")
        if os.path.exists(p):
            purchases += parse_purchases(zipfile.ZipFile(p))
    bars = load_universe()
    idx = ew_index(bars)
    rows = build_rows(events_from(purchases), bars, idx, buy_history(purchases))
    trim = [r for r in rows if r["trim_setup"]]
    res = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "quarters": [FIRST_Q, LAST_Q],
           "purchases": len(purchases), "events": len(rows), "universe": len(bars),
           "params": {"min_value": MIN_VALUE, "spacing": SPACING, "daily_cap": DAILY_CAP, "nw_lag": NW_LAG}}
    for name, sub in (("all", rows), ("trim_setup", trim), ("ceo", [r for r in rows if r["ceo"]]),
                      ("big", [r for r in rows if r["value"] >= BIG_VALUE]),
                      ("pre2020", [r for r in rows if r["filed"] < "2020"]),
                      ("since2020", [r for r in rows if r["filed"] >= "2020"])):
        res[name] = {f"x{h}": summarize(sub, f"x{h}", NW_LAG[h]) for h in HORIZONS}
    res["verdict"] = verdict(res["all"]["x60"], res["trim_setup"]["x60"])
    split = [r for r in rows if r["filed"] >= SPLIT_FROM]
    drop = [r for r in split if r["ret20"] is not None and r["ret20"] <= DROP_20]
    groups2 = {"opp_after_drop": [r for r in drop if r["opportunistic"]],
               "routine_after_drop": [r for r in drop if not r["opportunistic"]],
               "opp_no_drop": [r for r in split if r["opportunistic"] and r not in drop],
               "routine_all": [r for r in split if not r["opportunistic"]],
               "opp_after_drop_trim": [r for r in drop if r["opportunistic"] and r["trim_setup"]],
               "opp_after_shock": [r for r in split if r["opportunistic"] and r["shock"]],
               "routine_after_shock": [r for r in split if not r["opportunistic"] and r["shock"]]}
    for name, sub in groups2.items():
        res[name] = {f"x{h}": summarize(sub, f"x{h}", NW_LAG[h]) for h in HORIZONS}
    res["verdict_drop"] = verdict_drop(res["opp_after_drop"]["x60"])
    res["rows"] = rows
    return res


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "n=0"
    t = "-" if s["t_nw"] is None else f"{s['t_nw']:+.2f}"
    return f"n={s['n']:5d} months={s['months']:3d} mean {s['mean']:+6.2%} med {s['median']:+6.2%} hit {s['hit']:.0%} t(NW) {t}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--fetch", action="store_true", help="download missing quarter files first")
    ap.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY", ""))
    a = ap.parse_args(argv)
    if a.fetch:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        r = fetch(s, {"https": a.proxy} if a.proxy else None)
        _out(f"fetched {len(r['fetched'])}, cached {len(r['cached'])}, failed {r['failed']}")
        return 0
    res = run()
    out = os.path.join(paths.data_dir(), f"insider_buy_study_{datetime.date.today().isoformat()}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    _out(f"Insider-buy study {res['quarters'][0]}..{res['quarters'][1]}: {res['purchases']} purchase lines, "
         f"{res['events']} events in a {res['universe']}-symbol universe")
    for name in ("all", "trim_setup", "ceo", "big", "pre2020", "since2020"):
        for h in HORIZONS:
            _out(f"  {name:10s} +{h:<3d} {_fmt(res[name][f'x{h}'])}")
    _out(f"\nVERDICT (primary all x60, subset trim_setup x60): {res['verdict']}")
    _out(f"\nSecond test (added after the first result; filings from {SPLIT_FROM}, drop = 20 sessions <= {DROP_20:.0%}):")
    for name in ("opp_after_drop", "routine_after_drop", "opp_no_drop", "routine_all", "opp_after_drop_trim",
                 "opp_after_shock", "routine_after_shock"):
        for h in HORIZONS:
            _out(f"  {name:20s} +{h:<3d} {_fmt(res[name][f'x{h}'])}")
    _out(f"VERDICT (opportunistic after a big drop, x60): {res['verdict_drop']}")
    _out(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
