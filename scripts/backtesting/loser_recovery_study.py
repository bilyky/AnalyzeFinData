"""O1 / R&D #81 test: do held losers come back? Buy back after the wash-sale window, or swap into the index?

Raised by the OceanView year-end harvest (plan 2026-11-20, stage O1; R&D #81 "Test first"). When a stock held
for a year or more is sold at a loss, the owner can either buy it back after the 31-day wash-sale window or put
the money into SPY/RSP and stay out. This asks which did better in the past.

RULES (fixed before the first run; any later change is disclosed with the result)
Data: our OHLCV cache (Symbol_full/<SYM>_daily.json under aether.paths.cache_dir()), split-adjusted with the
project's risk_utils.split_adjust_ohlcv. Universe: every cached symbol except SPY and RSP and the instruments
aether.instruments.is_excluded() flags (leveraged/inverse, crypto). Calendar: SPY's trading days.
Sampling: the last trading day of each month, from 2005-01 to the last month with 157 forward sessions.
Event (a "held loser"): on month-end t, close_t <= 0.80 x close_(t-252), i.e. a holder who bought a year earlier
is down 20% or more (a long-term loss). The symbol needs a bar on t, t-252, t+31 and t+157.
Outcomes, on split-adjusted closes:
  PRIMARY  buy-back excess = R_sym(t+31 -> t+157) - R_SPY(t+31 -> t+157): buy back after the 31-day window and
           hold 126 sessions, against holding SPY over the same window.
  Context  the same against RSP; the wash-window excess R_sym(t -> t+31) - R_SPY; losers down 40% or more; two
           eras (2005-2014, 2015-); the share of events that beat SPY. Per-event excess is winsorized at the
           1st/99th percentile of all events before averaging; raw means are reported too.
Statistic: the cross-sectional mean of the primary excess per month (months with at least 5 events), then the
mean of that monthly series with a Newey-West t, lag 5 (126-session windows overlap about six months).
Verdict:
  INSUFFICIENT      fewer than 36 months with events;
  BUY_BACK          monthly mean > 0 and HAC t >= 2.0 -> losers beat SPY after the window: buying back is
                    supported;
  LOSERS_LAG        monthly mean < 0 and HAC t <= -2.0 -> swap into SPY/RSP and stay out;
  INCONCLUSIVE      otherwise -> the plan's default: swap into SPY/RSP, no buy-back.
The harvest goes ahead whatever the verdict; this only decides what the money does after the sale.

Caveats written in advance:
- Survivorship: the universe is today's cache. Losers that were delisted or went bankrupt are missing, which
  makes recovery look better than it was. A BUY_BACK verdict is therefore weaker than it looks; a LOSERS_LAG or
  INCONCLUSIVE verdict is not hurt by this bias.
- The split adjuster treats a large one-day move whose open agrees with the close as a split; a real crash gap of
  45% or more could be mis-adjusted. The outcome distribution is checked before reading the result.
- No costs, no taxes, no dividends (closes are price-only, so SPY's ~1.5%/yr yield is left out on both sides
  only approximately).
- Not specific to the owner's names; the prior from 14 earlier studies is weak for return ideas.
"""
import datetime as dt
import json
import os
import statistics as st
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
from setup_s10_gate_study import _t_hac  # noqa: E402

from aether import instruments, paths  # noqa: E402
from aether.risk_utils import split_adjust_ohlcv  # noqa: E402

LOOKBACK = 252
WASH = 31
HOLD = 126
LOSS = -0.20
DEEP = -0.40
START = "2005-01-01"
MIN_EVENTS_PER_MONTH = 5
MIN_MONTHS = 36
HAC_LAG = 5
T_BAR = 2.0
ERA_SPLIT = "2015-01-01"


# ── pure parts (unit-tested) ─────────────────────────────────────────────────────────────────────
def month_ends(days):
    """Indexes of the last trading day of each month in a sorted list of ISO dates."""
    return [i for i in range(len(days)) if i == len(days) - 1 or days[i][:7] != days[i + 1][:7]]


def events_for(sym_closes, spy_days, spy_closes, ends, rsp=None):
    """Events for one symbol. sym_closes: {date: adjusted close}. Returns a list of dicts."""
    out = []
    for i in ends:
        if i - LOOKBACK < 0 or i + WASH + HOLD >= len(spy_days):
            continue
        d0, dp, dw, de = spy_days[i], spy_days[i - LOOKBACK], spy_days[i + WASH], spy_days[i + WASH + HOLD]
        if not all(d in sym_closes for d in (d0, dp, dw, de)):
            continue
        if sym_closes[d0] > (1 + LOSS) * sym_closes[dp]:        # the docstring's rule, exact: close_t <= 0.80 x
            continue
        loss = sym_closes[d0] / sym_closes[dp] - 1
        r_back = sym_closes[de] / sym_closes[dw] - 1
        spy_back = spy_closes[i + WASH + HOLD] / spy_closes[i + WASH] - 1
        ev = {"date": d0, "loss": loss, "excess": r_back - spy_back,
              "wash_excess": (sym_closes[dw] / sym_closes[d0] - 1) - (spy_closes[i + WASH] / spy_closes[i] - 1)}
        if rsp and all(d in rsp for d in (dw, de)):
            ev["excess_rsp"] = r_back - (rsp[de] / rsp[dw] - 1)
        out.append(ev)
    return out


def winsorize(xs, lo_q=0.01, hi_q=0.99):
    if len(xs) < 3:
        return list(xs)
    s = sorted(xs)
    lo, hi = s[int(lo_q * (len(s) - 1))], s[int(hi_q * (len(s) - 1))]
    return [min(max(x, lo), hi) for x in xs]


def monthly_series(events, key="excess", min_n=MIN_EVENTS_PER_MONTH):
    """[(month, mean of key)] over months with at least min_n events, in date order."""
    by = {}
    for e in events:
        if key in e:
            by.setdefault(e["date"][:7], []).append(e[key])
    return [(m, sum(v) / len(v)) for m, v in sorted(by.items()) if len(v) >= min_n]


def verdict(series):
    """The pre-registered decision on the monthly primary series."""
    xs = [v for _, v in series]
    if len(xs) < MIN_MONTHS:
        return "INSUFFICIENT", None, (sum(xs) / len(xs) if xs else None)
    m = sum(xs) / len(xs)
    t = _t_hac(xs, HAC_LAG)
    if t is not None and m > 0 and t >= T_BAR:
        return "BUY_BACK", t, m
    if t is not None and m < 0 and t <= -T_BAR:
        return "LOSERS_LAG", t, m
    return "INCONCLUSIVE", t, m


# ── data ─────────────────────────────────────────────────────────────────────────────────────────
def load_adjusted(sym):
    try:
        with open(os.path.join(paths.cache_dir(), "Symbol_full", f"{sym}_daily.json"), encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except (OSError, ValueError):
        return None
    days = sorted(ts)
    try:
        o = [float(ts[d]["1. open"]) for d in days]
        h = [float(ts[d]["2. high"]) for d in days]
        lo = [float(ts[d]["3. low"]) for d in days]
        c = [float(ts[d]["4. close"]) for d in days]
    except (KeyError, ValueError, TypeError):
        return None
    _, _, ca = split_adjust_ohlcv(o, h, lo, c)
    return {d: x for d, x in zip(days, ca) if x and x > 0}


def summarize(events, label):
    ws = winsorize([e["excess"] for e in events])
    ser = monthly_series([dict(e, excess=w) for e, w in zip(events, ws)])
    v, t, m = verdict(ser)
    raw = [e["excess"] for e in events]
    return {"label": label, "events": len(events), "months": len(ser), "verdict": v, "hac_t": t,
            "monthly_mean_excess": m, "event_mean_raw": (sum(raw) / len(raw)) if raw else None,
            "event_median": st.median(raw) if raw else None,
            "share_beat_spy": (sum(x > 0 for x in raw) / len(raw)) if raw else None}


def main():
    spy = load_adjusted("SPY")
    rsp = load_adjusted("RSP")
    if not spy:
        sys.exit("ABORT: no SPY in our cache")
    days = [d for d in sorted(spy)]
    closes = [spy[d] for d in days]
    ends = [i for i in month_ends(days) if days[i] >= START]
    syms = sorted(f[:-11] for f in os.listdir(os.path.join(paths.cache_dir(), "Symbol_full"))
                  if f.endswith("_daily.json"))
    events, used, skipped = [], 0, 0
    for s in syms:
        if s in ("SPY", "RSP") or instruments.is_excluded(s):
            skipped += 1
            continue
        sc = load_adjusted(s)
        if not sc:
            skipped += 1
            continue
        used += 1
        for e in events_for(sc, days, closes, ends, rsp):
            e["symbol"] = s
            events.append(e)
    raw = sorted(e["excess"] for e in events)
    dist = {"n": len(raw), "min": raw[0] if raw else None, "p01": raw[int(0.01 * (len(raw) - 1))] if raw else None,
            "median": st.median(raw) if raw else None, "p99": raw[int(0.99 * (len(raw) - 1))] if raw else None,
            "max": raw[-1] if raw else None}
    report = {"run_date": str(dt.date.today()), "rules": "see module docstring", "symbols_used": used,
              "symbols_skipped": skipped, "spy_range": [days[0], days[-1]], "outcome_distribution": dist,
              "primary": summarize(events, "all held losers, buy back after 31 days vs SPY"),
              "context": {
                  "deep_losers_40pct": summarize([e for e in events if e["loss"] <= DEEP], "down 40%+"),
                  "era_2005_2014": summarize([e for e in events if e["date"] < ERA_SPLIT], "2005-2014"),
                  "era_2015_on": summarize([e for e in events if e["date"] >= ERA_SPLIT], "2015-"),
                  "vs_rsp": summarize([dict(e, excess=e["excess_rsp"]) for e in events if "excess_rsp" in e], "vs RSP"),
                  "wash_window": summarize([dict(e, excess=e["wash_excess"]) for e in events], "t -> t+31 vs SPY")}}
    with open(os.path.join(paths.data_dir(), "loser_recovery_study.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    p = report["primary"]
    sys.stdout.write(f"symbols used {used}, skipped {skipped}; SPY {days[0]}..{days[-1]}\n"
                     f"outcome distribution: {json.dumps(dist)}\n")
    for r in [p] + list(report["context"].values()):
        sys.stdout.write(f"  {r['label']:<48} n={r['events']:<5} months={r['months']:<4} verdict={r['verdict']:<13}"
                         f" t={r['hac_t']} monthly mean={r['monthly_mean_excess']} median={r['event_median']}"
                         f" beat SPY={r['share_beat_spy']}\n")
    sys.stdout.write(f"\nPRIMARY VERDICT: {p['verdict']}\n")


if __name__ == "__main__":
    main()
