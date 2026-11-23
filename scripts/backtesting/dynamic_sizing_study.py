"""R&D #124: dynamic step sizing for new money. Do bigger steps when SPY is far under its 50-day average (and half
steps on a jump day) buy at a lower average price than equal steps?

Raised 2026-11-23 (owner: "it should be dynamic based on market status"). New money (held into 2027) goes into
SPY/RSP in steps. The base rates in the plan check (invest_plan.odds) were better 8%+ under the 50-day average
(median +6.8% after 60 sessions) than 4-8% under (+4.0%), and after a +3% day in a deep drawdown a lower close came
within 5 days in 20 of 25 past cases. This tests a sizing rule built on that, against plain equal steps.

RULES (fixed before the first run; any later change is disclosed with the result)
Data: SPY closes from our OHLCV cache (Symbol_full/SPY_daily.json, field "4. close", under aether.paths.cache_dir()),
2000 to the last cached bar. RSP the same, as context. Buys are at the session's close (no intraday limit fills).
The 50-day average at session j uses closes j-49..j (the plan check's definition).
Step rule, per session j, as a share of the starting budget:
    SPY close vs its 50-day average:  <= -8% -> 25%;  -8% to -4% -> 15%;  -4% to 0 -> 10%;  above 0 -> 5%
    jump day (close_j / close_(j-1) - 1 >= +3%): half the step
  buying until the budget is used; whatever is left is bought at session 40 (so both plans are fully in by then).
Equal plan: 10% at each of the first 10 sessions.
Start dates: the last trading day of each month with 50 prior sessions and 39 later ones.
Outcome per start: advantage = ln(average cost of the equal plan / average cost of the dynamic plan); above 0 means
the dynamic plan bought cheaper. Both plans hold the same budget from session 40 on, so the difference in later
returns is exactly this advantage.
PRIMARY sample: starts when SPY closes 4% or more under its 50-day average (a correction: when the rule is meant
to be used). Context: all starts; RSP; how much of the budget each plan had in after 10 sessions.
Statistic: mean advantage over the monthly series, Newey-West t with lag 1 (40-session windows overlap about two
months).
Verdict (primary):
  INSUFFICIENT      fewer than 24 starts;
  DYNAMIC_BETTER    mean > 0 and HAC t >= 2.0 -> propose the rule for the plan check as the daily step;
  DYNAMIC_WORSE     mean < 0 and HAC t <= -2.0 -> keep equal steps;
  INCONCLUSIVE      otherwise -> the plan check may show the rule's step as information only, labelled untested.
Caveats written in advance: one index and overlapping starts; close-only fills (a limit near the day's low would
buy cheaper on both plans); the band edges were picked from the same history the odds came from (in-sample), so a
pass is weaker than it looks; no costs or dividends.
"""
import datetime as dt
import json
import math
import os
import statistics as st
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
from sell_everything_study import our_closes  # noqa: E402
from setup_s10_gate_study import _t_hac  # noqa: E402

from aether import paths  # noqa: E402

BANDS = ((-0.08, 0.25), (-0.04, 0.15), (0.0, 0.10))     # (upper edge, step); above 0 -> ABOVE_STEP
ABOVE_STEP = 0.05
JUMP = 0.03
WINDOW = 40
EQUAL_STEPS = 10
CORRECTION = -0.04
MIN_STARTS = 24
HAC_LAG = 1
T_BAR = 2.0


def sma50(c, j):
    return sum(c[j - 49:j + 1]) / 50 if j >= 49 else None


def step_for(c, j):
    """The rule's step (share of the starting budget) for session j."""
    dist = c[j] / sma50(c, j) - 1
    step = next((s for edge, s in BANDS if dist <= edge), ABOVE_STEP)
    if j >= 1 and c[j] / c[j - 1] - 1 >= JUMP:
        step /= 2
    return step


def avg_cost(buys):
    """buys: [(amount, price)]; the average price paid for the shares bought."""
    spent = sum(a for a, _ in buys)
    shares = sum(a / p for a, p in buys)
    return spent / shares


def dynamic_buys(c, s):
    left, out = 1.0, []
    for j in range(s, s + WINDOW):
        amt = left if j == s + WINDOW - 1 else min(step_for(c, j), left)
        if amt > 1e-12:
            out.append((amt, c[j]))
            left -= amt
        if left <= 1e-12:
            break
    return out


def equal_buys(c, s):
    return [(1.0 / EQUAL_STEPS, c[j]) for j in range(s, s + EQUAL_STEPS)]


def advantage(c, s):
    return math.log(avg_cost(equal_buys(c, s)) / avg_cost(dynamic_buys(c, s)))


def month_ends(days):
    return [i for i in range(len(days)) if i == len(days) - 1 or days[i][:7] != days[i + 1][:7]]


def verdict(xs):
    if len(xs) < MIN_STARTS:
        return "INSUFFICIENT", None, (sum(xs) / len(xs) if xs else None)
    m = sum(xs) / len(xs)
    t = _t_hac(xs, HAC_LAG)
    if t is not None and m > 0 and t >= T_BAR:
        return "DYNAMIC_BETTER", t, m
    if t is not None and m < 0 and t <= -T_BAR:
        return "DYNAMIC_WORSE", t, m
    return "INCONCLUSIVE", t, m


def run(symbol):
    closes = our_closes(symbol)
    days = sorted(closes)
    c = [closes[d] for d in days]
    starts = [i for i in month_ends(days) if i >= 50 and i + WINDOW - 1 < len(c)]
    rows = []
    for s in starts:
        dyn = dynamic_buys(c, s)
        rows.append({"date": days[s], "dist": c[s] / sma50(c, s) - 1, "adv": advantage(c, s),
                     "dyn_in_10": sum(a for a, _ in dyn[:10]), "dyn_sessions": len(dyn)})
    out = {"symbol": symbol, "range": [days[0], days[-1]]}
    for name, sel in (("correction_starts", [r for r in rows if r["dist"] <= CORRECTION]), ("all_starts", rows)):
        xs = [r["adv"] for r in sel]
        v, t, m = verdict(xs)
        out[name] = {"n": len(xs), "verdict": v, "hac_t": t, "mean_adv": m,
                     "median_adv": st.median(xs) if xs else None,
                     "share_dynamic_cheaper": (sum(x > 0 for x in xs) / len(xs)) if xs else None,
                     "dyn_invested_after_10_sessions": (sum(r["dyn_in_10"] for r in sel) / len(sel)) if sel else None}
    out["recent"] = [r for r in rows if r["date"] >= "2025-01-01"]
    return out


def main():
    report = {"run_date": str(dt.date.today()), "rules": "see module docstring", "SPY": run("SPY"), "RSP": run("RSP")}
    with open(os.path.join(paths.data_dir(), "dynamic_sizing_study.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    for sym in ("SPY", "RSP"):
        r = report[sym]
        for k in ("correction_starts", "all_starts"):
            x = r[k]
            sys.stdout.write(f"{sym} {k:18} n={x['n']:<4} verdict={x['verdict']:<15} t={x['hac_t']} mean adv={x['mean_adv']}"
                             f" median={x['median_adv']} dynamic cheaper={x['share_dynamic_cheaper']}"
                             f" dynamic in after 10 sessions={x['dyn_invested_after_10_sessions']}\n")
    sys.stdout.write(f"\nPRIMARY VERDICT (SPY, correction starts): {report['SPY']['correction_starts']['verdict']}\n")


if __name__ == "__main__":
    main()
