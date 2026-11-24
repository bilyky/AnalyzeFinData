"""Plan stage G1: does the circuit breaker's buy freeze help or cost? When it turns on, what does SPY do next?

Raised 2026-11-24 (owner: the game stayed deep down through Monday's +4.7% rally). The breaker freezes new buys
(and tightens stops) while SPY fell 2% or more on the day, or sits 5% or more under its 10-session peak. On
2026-11-23 it most likely stayed on through the rally (SPY 5.07% under its peak at the 07:00 run). If SPY tends to
rise after the breaker turns on, the freeze costs the game exactly the rebounds it should buy.

RULES (fixed before the first run; any later change is disclosed with the result)
Data: SPY closes from our OHLCV cache (Symbol_full/SPY_daily.json under aether.paths.cache_dir(), "4. close"),
2000 to the last cached bar.
Breaker state on session i: aether.circuit_breaker.breaker_reason(*breaker_measures(closes[: i + 1])) is not
None, the live code's own thresholds (one-day drop <= -2%, 10-session drawdown <= -5%), replayed at the close.
Not replayed: the VXX surge trigger (no VXX history in our cache) and the 30-minute opening window.
Episode: the first breaker-on session after at least 20 breaker-off sessions (so 20-session outcomes of different
episodes don't overlap).
PRIMARY outcome: SPY's return over the 20 sessions after the episode's first close, minus SPY's mean 20-session
return over all sessions (excess). Statistic: mean excess over episodes, one-sample t.
Verdict:
  INSUFFICIENT    fewer than 15 episodes;
  FREEZE_COSTS    mean excess > 0 and t >= 2 -> the freeze misses above-normal returns: propose (own PR, owner
                  sign-off) a shorter freeze or an earlier release, studied the same way first;
  FREEZE_HELPS    mean excess < 0 and t <= -2 -> keep the freeze;
  INCONCLUSIVE    otherwise -> no change.
Context (not decisive): how long the breaker stays on per episode and SPY's return while it is on; the excess
after 5 and 60 sessions; the worst close in the next 20 sessions; episodes started by the one-day drop vs the
drawdown; the 2025, 2026 spring and 2026 November episodes by name.
Caveats written in advance: index-level, not the game's own picks; the stop tightening is not measured (the
portfolio backtest has no breaker); close-to-close, no costs; one market's history (survivorship in time).
"""
import datetime as dt
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from aether import circuit_breaker as cb  # noqa: E402
from aether import paths  # noqa: E402

GAP = 20
HORIZON = 20
MIN_EPISODES = 15
T_BAR = 2.0


def on(closes, i) -> str | None:
    """The breaker's reason at the close of session i (None when off). Uses the live code's functions."""
    return cb.breaker_reason(*cb.breaker_measures(closes[max(0, i - cb.DRAWDOWN_WINDOW):i + 1]))


def episodes(closes) -> list:
    """Index of each episode's first session: on, after at least GAP off sessions."""
    out, off_run = [], GAP
    for i in range(1, len(closes)):
        if on(closes, i):
            if off_run >= GAP:
                out.append(i)
            off_run = 0
        else:
            off_run += 1
    return out


def spell_length(closes, i) -> int:
    n = 0
    while i + n < len(closes) and on(closes, i + n):
        n += 1
    return n


def t_stat(xs):
    if len(xs) < 2:
        return None
    sd = st.stdev(xs)
    return (sum(xs) / len(xs)) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def verdict(excess):
    if len(excess) < MIN_EPISODES:
        return "INSUFFICIENT", None
    m, t = sum(excess) / len(excess), t_stat(excess)
    if t is not None and m > 0 and t >= T_BAR:
        return "FREEZE_COSTS", t
    if t is not None and m < 0 and t <= -T_BAR:
        return "FREEZE_HELPS", t
    return "INCONCLUSIVE", t


def mean_fwd(closes, h):
    xs = [closes[i + h] / closes[i] - 1 for i in range(len(closes) - h)]
    return sum(xs) / len(xs)


def main():
    with open(os.path.join(paths.cache_dir(), "Symbol_full", "SPY_daily.json"), encoding="utf-8") as f:
        ts = json.load(f)["Time Series (Daily)"]
    days = sorted(d for d in ts if d >= "2000-01-01")
    c = [float(ts[d]["4. close"]) for d in days]
    base = {h: mean_fwd(c, h) for h in (5, 20, 60)}
    rows = []
    for i in episodes(c):
        if i + HORIZON >= len(c):
            continue
        n = spell_length(c, i)
        j = min(i + n, len(c) - 1)
        rows.append({"date": days[i], "trigger": "day" if "Single-Day" in on(c, i) else "drawdown",
                     "spell_sessions": n, "spell_return": c[j] / c[i] - 1,
                     **{f"excess{h}": (c[i + h] / c[i] - 1 - base[h]) if i + h < len(c) else None for h in (5, 20, 60)},
                     "worst20": min(c[i + 1:i + HORIZON + 1]) / c[i] - 1})
    ex = [r["excess20"] for r in rows]
    v, t = verdict(ex)
    report = {"run_date": str(dt.date.today()), "rules": "see module docstring", "range": [days[0], days[-1]],
              "baseline_mean_fwd": base, "episodes": rows, "verdict": v, "t": t, "n": len(ex),
              "mean_excess20": (sum(ex) / len(ex)) if ex else None, "median_excess20": st.median(ex) if ex else None,
              "share_positive": (sum(x > 0 for x in ex) / len(ex)) if ex else None,
              "median_spell_sessions": st.median(r["spell_sessions"] for r in rows) if rows else None}
    with open(os.path.join(paths.data_dir(), "breaker_freeze_study.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    sys.stdout.write(f"SPY {days[0]}..{days[-1]}; episodes {len(ex)}; baseline 20-session mean {base[20]:+.2%}\n")
    for r in rows:
        sys.stdout.write(f"  {r['date']} {r['trigger']:8} on {r['spell_sessions']:>3} sessions (spy {r['spell_return']:+.1%} while on)"
                         f"  excess5 {r['excess5']:+.1%}  excess20 {r['excess20']:+.1%}  worst20 {r['worst20']:+.1%}\n")
    sys.stdout.write(f"\nmean excess20 {report['mean_excess20']:+.2%} (median {report['median_excess20']:+.2%}, "
                     f"positive {report['share_positive']:.0%}), t {t}\nVERDICT: {v}\n")


if __name__ == "__main__":
    main()
