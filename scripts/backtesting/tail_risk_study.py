"""
Tail-Risk Study (R&D #115) - do stacked waivers, or a history of sudden drops, predict a big
10-session drop after a buy?

Why: on 2026-11-18 the paper game bought SA and the advisor recommended it; it fell ~30% on
11-19. SA passed the live buy gates only through two stacked waivers, both keyed on the same
elite gate (risk_utils.is_elite_breakout_candidate): the R&D #13 Bearish-PGR bypass
(ai_portfolio_game.check_failure_rules) and the R&D #32 R/R + 5%-target waiver. R&D #75 and
R&D #112 judged waivers on mean returns only. This study tests the LEFT TAIL, and asks the
owner's question whether a stock's own history of sudden drops predicts them.

Pre-registered rule (committed and pushed before the first full run; not changed after):

Population: observations of the scripts/backtesting/rule_walkforward.py panel (the replay R&D
#75 used) that the live entry gates admit: rule_walkforward.admit_mask(a, CURRENT), and not
rejected by the live R&D #13 PGR rule. That rule (check_failure_rules) rejects a PGR label that
startswith "Be" unless risk_utils.is_elite_breakout_candidate(S10+L60, S10); the bottom-confirmed
waiver is not modelled, so Bearish non-elite observations are dropped (counted in the report).
The PGR label is the live one: powergauge._pgr_str of the corrected PGR
(backtest_ratings.extract_pgr_corr).

Outcome: BIG_DROP = worst split-adjusted close of the 10 real sessions after the entry day
<= -15% vs the entry day's adjusted close (rule_walkforward.tail_fields: placeholder bars
excluded via bar_provenance, splits via risk_utils.split_adjust_ohlcv; observations whose
outcome window has a session gap or is incomplete are dropped).

Test 1, stacked waivers. Per observation: pgr_bypass = Bearish label AND elite; rr_waiver =
elite AND NOT (R/R >= 2 AND target gain >= 5%). W2 = both, W0 = neither, W1 = exactly one
(context only). Per date with both W2 and W0 present: BIG_DROP rate(W2) - rate(W0).

Test 2, drop-prone history. H = sessions with a close-to-close return <= -10% among the 756
real sessions before the entry day (split-adjusted; a -10% day followed by a >= +10% session is
a bad bar and not counted; a return across a session gap is not counted; H undefined with
fewer than 756 prior sessions). HIGH = H >= 4, LOW = H <= 1. Per date with both present:
BIG_DROP rate(HIGH) - rate(LOW).

Statistic (each test): mean of the per-date difference; Newey-West (HAC) t with lag 9 (a
10-session outcome sampled daily) over the dates in order; plain t as context. Halves: the
qualifying dates split at the calendar midpoint of their first..last date; the mean difference
in each half.

Verdict (each test separately):
    TAIL_WORSE    mean > 0 AND HAC t >= 1.96 AND the mean is > 0 in both halves
    INCONCLUSIVE  otherwise, or when fewer than 100 dates have both groups
Two tests are run, so a single |t| near 2 is weak evidence. TAIL_WORSE only justifies
PROPOSING a rule change in a later, separate PR (risk rules apply); INCONCLUSIVE -> keep the
live rules.

Reported, not decisive: group sizes, pooled BIG_DROP rates, mean and P5 of worst10, W1.

Not measured: stops (a live ATR stop would cut many of these drops short), sizing, slots,
costs. Biases: today's universe (survivorship); ~20% of Chaikin snapshots carry the wrong
date's data (R&D #74); the 2026-09-01..10-30 factor gap left PGR empty (label "" -> never
Bearish there); rows need 20 future bars (the panel's rule).

Usage (read-only on the real caches; keep the panel cache outside Data/):
    python scripts/backtesting/tail_risk_study.py --data-dir <main>/Data --panel <tmp>/tail_panel.pkl \\
        [--rebuild] [--workers N] [--out <main>/Data/tail_risk_study_<date>.json]
"""
import argparse
import datetime
import json
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import powergauge
import rule_walkforward as wf
import setup_s10_gate_study as sg
from aether import risk_utils

BIG_DROP_PCT = -15.0
HAC_LAG = 9
GATE_T = 1.96
MIN_DATES = 100
HIGH_H, LOW_H = 4, 1
MIN_RR, MIN_GAIN = wf.CURRENT[2], 5.0      # the R/R gate admit_mask applies to CURRENT


# ── Tags ──────────────────────────────────────────────────────────────────────

def tags(a) -> dict:
    """Live-rule tags per observation: elite, Bearish label, R/R gate pass."""
    s10, total = a["s10"], a["s10"] + a["l60"]
    elite = np.array([risk_utils.is_elite_breakout_candidate(float(t), float(s)) for t, s in zip(total, s10)],
                     dtype=bool)
    bearish = np.array([powergauge._pgr_str(int(p)).startswith("Be") for p in a["pgr"]], dtype=bool)
    rr_ok = (a["rr"] >= MIN_RR) & (a["gain"] >= MIN_GAIN)
    return {"elite": elite, "bearish": bearish, "rr_ok": rr_ok}


def population(a, t) -> np.ndarray:
    """Admitted by the live gates (admit_mask CURRENT) and not rejected by the R&D #13 PGR rule."""
    return wf.admit_mask(a, wf.CURRENT) & ~(t["bearish"] & ~t["elite"])


def waiver_count(t) -> np.ndarray:
    """0, 1 or 2 waivers needed: the Bearish-PGR bypass and the R/R + target waiver."""
    pgr_bypass = t["bearish"] & t["elite"]
    rr_waiver = t["elite"] & ~t["rr_ok"]
    return pgr_bypass.astype(int) + rr_waiver.astype(int)


def history_groups(h) -> tuple:
    """(HIGH, LOW) masks from the drop count; NaN (short history) is in neither."""
    h = np.asarray(h, dtype=float)
    ok = np.isfinite(h)
    return ok & (h >= HIGH_H), ok & (h <= LOW_H)


# ── Statistic ─────────────────────────────────────────────────────────────────

def per_date_diff(days, big, hi, lo) -> tuple:
    """(dates, rate_hi - rate_lo) for each date where both groups have observations."""
    days = np.asarray(days).astype(str)
    big = np.asarray(big, dtype=float)
    out_d, out_x = [], []
    for d in np.unique(days[hi | lo]):
        on = days == d
        h, lw = on & hi, on & lo
        if h.any() and lw.any():
            out_d.append(str(d))
            out_x.append(float(big[h].mean() - big[lw].mean()))
    return out_d, out_x


def _halves(dates, xs) -> tuple:
    """Mean of xs in the first and second half of the calendar span of `dates`."""
    if not dates:
        return None, None
    d0, d1 = datetime.date.fromisoformat(dates[0]), datetime.date.fromisoformat(dates[-1])
    mid = d0 + (d1 - d0) / 2
    first = [x for d, x in zip(dates, xs) if datetime.date.fromisoformat(d) <= mid]
    second = [x for d, x in zip(dates, xs) if datetime.date.fromisoformat(d) > mid]
    return (float(np.mean(first)) if first else None, float(np.mean(second)) if second else None)


def diff_stats(dates, xs) -> dict:
    t_hac = sg._t_hac(xs, HAC_LAG)
    t_plain = sg._t_one_sample(xs)
    h1, h2 = _halves(dates, xs)
    return {"n_dates": len(xs), "mean_diff": float(np.mean(xs)) if xs else None,
            "t_hac": t_hac, "t_plain": t_plain, "half1_mean": h1, "half2_mean": h2,
            "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None}


def verdict(s) -> str:
    """TAIL_WORSE / INCONCLUSIVE under the pre-registered rule (see the module docstring)."""
    if s["n_dates"] < MIN_DATES:
        return "INCONCLUSIVE"
    m, t, h1, h2 = s["mean_diff"], s["t_hac"], s["half1_mean"], s["half2_mean"]
    if None in (m, t, h1, h2):
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T and h1 > 0 and h2 > 0:
        return "TAIL_WORSE"
    return "INCONCLUSIVE"


def group_summary(a, mask, big) -> dict:
    w = a["worst10"][mask]
    return {"obs": int(mask.sum()), "dates": int(len(np.unique(a["day"][mask]))),
            "big_drop_rate": round(float(big[mask].mean()), 4) if mask.any() else None,
            "worst10_mean_pct": round(float(w.mean()), 2) if mask.any() else None,
            "worst10_p5_pct": round(float(np.percentile(w, 5)), 2) if mask.any() else None}


def _r(x, k=4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, k)


# ── Run ───────────────────────────────────────────────────────────────────────

def run(rows) -> dict:
    a = wf.to_arrays(rows)
    if "worst10" not in a:
        raise SystemExit("panel lacks the tail fields (built before them): pass --rebuild")
    a["worst10"] = a["worst10"].astype(float)
    a["drops756"] = a["drops756"].astype(float)
    t = tags(a)
    admitted = wf.admit_mask(a, wf.CURRENT)
    pop = population(a, t) & np.isfinite(a["worst10"])
    big = a["worst10"] <= BIG_DROP_PCT
    w = waiver_count(t)
    g = {"W2": pop & (w == 2), "W1": pop & (w == 1), "W0": pop & (w == 0)}
    hi, lo = history_groups(a["drops756"])
    g["HIGH"], g["LOW"] = pop & hi, pop & lo
    g["H_MID"] = pop & np.isfinite(a["drops756"]) & ~hi & ~lo

    wv = a["worst10"][pop]
    rep = {"counts": {"panel_obs": len(rows), "admitted_by_admit_mask": int(admitted.sum()),
                      "dropped_bearish_not_elite": int((admitted & t["bearish"] & ~t["elite"]).sum()),
                      "dropped_no_outcome": int((population(a, t) & ~np.isfinite(a["worst10"])).sum()),
                      "population": int(pop.sum()), "population_dates": int(len(np.unique(a["day"][pop]))),
                      "no_history": int((pop & ~np.isfinite(a["drops756"])).sum())},
           "outcome_check": {"worst10_min_pct": _r(float(wv.min()), 2), "worst10_median_pct": _r(float(np.median(wv)), 2),
                             "worst10_p1_pct": _r(float(np.percentile(wv, 1)), 2),
                             "below_minus50_obs": int((wv <= -50).sum()),
                             "big_drop_rate_all": _r(float(big[pop].mean()))},
           "groups": {k: group_summary(a, m, big) for k, m in g.items()}}
    worst = np.where(pop & (a["worst10"] <= -50))[0]
    rep["outcome_check"]["below_minus50_examples"] = [
        [str(a["sym"][k]), str(a["day"][k]), round(float(a["worst10"][k]), 1)] for k in worst[:15]]
    for name, (hi_m, lo_m) in (("test1_w2_minus_w0", (g["W2"], g["W0"])),
                               ("test2_high_minus_low", (g["HIGH"], g["LOW"]))):
        dates, xs = per_date_diff(a["day"], big, hi_m, lo_m)
        s = diff_stats(dates, xs)
        rep[name] = {k: _r(v) if isinstance(v, float) or v is None else v for k, v in s.items()}
        rep[name]["verdict"] = verdict(s)
    # context: W1 vs W0 (not decisive)
    dates, xs = per_date_diff(a["day"], big, g["W1"], g["W0"])
    rep["context_w1_minus_w0"] = {k: _r(v) if isinstance(v, float) or v is None else v
                                  for k, v in diff_stats(dates, xs).items()}
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, help="the Data dir holding Symbol/ and Symbol_full/ (read-only)")
    ap.add_argument("--panel", required=True, help="pickle cache of the panel (keep it outside Data/)")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    rows = wf.build_panel(args.data_dir, args.panel, args.rebuild, args.workers)
    rep = run(rows)
    rep["meta"] = {"run_date": str(datetime.date.today()), "first_obs": min(r[0] for r in rows),
                   "last_obs": max(r[0] for r in rows), "symbols": len({r[1] for r in rows}),
                   "big_drop_pct": BIG_DROP_PCT, "hac_lag": HAC_LAG, "high_h": HIGH_H, "low_h": LOW_H,
                   "min_dates": MIN_DATES, "script": "scripts/backtesting/tail_risk_study.py"}
    sg._out(json.dumps(rep, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)


if __name__ == "__main__":
    main()
