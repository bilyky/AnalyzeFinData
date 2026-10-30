"""
Rank IC Study — among the candidates that pass today's gates, does Short10 order the next
20-day returns better than Short10 + Long60?

R&D #59 picked ranking by S10 alone on 2023-2024 and it beat today's S10 + L60 ranking on
2025-2026 (test CAGR 12.1% vs 7.6%), but one 3-6 name portfolio is too noisy to separate them
(HAC t 0.56). This asks the same question with far more evidence: on each date, the Spearman
rank correlation (IC) between a score and the next 20-day return across every candidate that
passes today's live gates (S10 >= 2, Setup, R/R >= 2 with the elite waiver).

Decision rule (fixed before running):
    Primary   per date with >= MIN_NAMES candidates: IC(S10) - IC(S10 + L60), 20-day returns;
              Newey-West t across dates, lag 19 (daily samples of a 20-day return). The lag
              counts dated samples; dates with < MIN_NAMES are skipped, so 19 samples can span
              more than 19 trading days (conservative).
    S10_RANKS_BETTER       mean > 0 AND HAC t >= 1.96 -> propose ranking by S10 (own PR).
    COMBINED_RANKS_BETTER  mean < 0 AND HAC t <= -1.96 -> keep today's ranking.
    INCONCLUSIVE           otherwise -> keep today's ranking.
Reported, not decisive: mean IC of each score vs 0 (S10, L60, S10 + L60); the same difference at
60 days (lag 59); over all stocks without the gates; per year.

Inputs: the rule_walkforward panel (scores from the replay fixed in PR #212). Today's universe;
unadjusted closes (ranks are robust to the split outliers the other studies winsorize).

Usage:
    python scripts/backtesting/rank_ic_study.py --panel <rule_wf_panel.pkl> [--out FILE]
"""
import argparse
import datetime
import json
import os
import pickle
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import rule_walkforward as wf
import setup_s10_gate_study as sg

MIN_NAMES = 10
GATE_T = 1.96


def ranks(x: np.ndarray) -> np.ndarray:
    """Average ranks (ties share the mean rank), 0-based."""
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x), dtype=float)
    for v in np.unique(x):
        tied = x == v
        if tied.sum() > 1:
            r[tied] = r[tied].mean()
    return r


def spearman(x, y):
    """Spearman correlation, or None when either side is constant."""
    rx, ry = ranks(np.asarray(x, dtype=float)), ranks(np.asarray(y, dtype=float))
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def daily_ic(days, score, ret, mask, min_names=MIN_NAMES) -> dict:
    """{date: IC} over the masked observations of each date with >= min_names of them."""
    out = {}
    keep = mask & ~np.isnan(ret)
    for d in np.unique(days[keep]):
        sel = keep & (days == d)
        if sel.sum() >= min_names:
            ic = spearman(score[sel], ret[sel])
            if ic is not None:
                out[d] = ic
    return out


def summarize_ic(ic: dict, lag: int) -> dict:
    v = [ic[d] for d in sorted(ic)]
    t = sg._t_hac(v, lag)
    return {"days": len(v), "mean_ic": round(float(np.mean(v)), 4) if v else None,
            "t_hac": round(t, 2) if t is not None else None}


def diff(ic_a: dict, ic_b: dict, lag: int) -> dict:
    days = sorted(set(ic_a) & set(ic_b))
    return summarize_ic({d: ic_a[d] - ic_b[d] for d in days}, lag)


def verdict(d: dict) -> str:
    m, t = d.get("mean_ic"), d.get("t_hac")
    if m is None or t is None:
        return "INCONCLUSIVE"
    if m > 0 and t >= GATE_T:
        return "S10_RANKS_BETTER"
    if m < 0 and t <= -GATE_T:
        return "COMBINED_RANKS_BETTER"
    return "INCONCLUSIVE"


def run(rows) -> dict:
    a = wf.to_arrays(rows)
    gated = wf.admit_mask(a, wf.CURRENT)
    everyone = np.ones(len(rows), dtype=bool)
    scores = {"s10": a["s10"], "l60": a["l60"], "combined": a["s10"] + a["l60"]}
    rep = {"counts": {"obs": len(rows), "gated_obs": int(gated.sum())}}
    for label, mask in (("gated", gated), ("all_stocks", everyone)):
        for horizon, ret, lag in (("20d", a["f20"], 19), ("60d", a["f60"], 59)):
            ics = {k: daily_ic(a["day"], s, ret, mask) for k, s in scores.items()}
            key = f"{label}_{horizon}"
            rep[key] = {k: summarize_ic(v, lag) for k, v in ics.items()}
            rep[key]["s10_minus_combined"] = diff(ics["s10"], ics["combined"], lag)
            if key == "gated_20d":
                rep["by_year_gated_20d"] = {
                    y: diff({d: v for d, v in ics["s10"].items() if d.startswith(y)},
                            {d: v for d, v in ics["combined"].items() if d.startswith(y)}, lag)
                    for y in sorted({d[:4] for d in ics["s10"]})}
    rep["verdict"] = verdict(rep["gated_20d"]["s10_minus_combined"])
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    with open(args.panel, "rb") as f:
        rows = pickle.load(f)
    rep = run(rows)
    rep["meta"] = {"run_date": str(datetime.date.today()), "min_names": MIN_NAMES,
                   "first_obs": min(r[0] for r in rows), "last_obs": max(r[0] for r in rows)}
    sg._out(json.dumps(rep, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)


if __name__ == "__main__":
    main()
