"""
R&D #113 — Waiver far from the highs, and buys below a falling 200-day: do they lose more?

Owner's question (2026-11-19), from SA: the Oracle recommended SA on 2026-11-18 and it fell 29% on
11-19 after a court ruling on its only project. SA got in only through the elite-breakout waiver
(reward/risk 0.15 against the Oracle's 1.5), while it sat 38% below its 52-week high and below a
falling 200-day average. The waiver's own stated premise is "a leader at new highs reads ~0
reward/risk". Two candidate risk filters, judged on LOSSES AVOIDED, not on beating SPY:

  (A) The elite-breakout waiver only near the 52-week high.
      Among Oracle-style admits that got in ONLY through the waiver (reward/risk under the minimum,
      elite: risk_utils' S10+L60 >= CFG.system_bypass_score_floor and S10 >= CFG.system_bypass_s10_floor),
      FAR = close more than FAR_PCT (10%) below its highest close of the last 252 sessions,
      NEAR = within FAR_PCT.
  (B) No real-money buy below a falling 200-day average.
      Among ALL Oracle-style admits: BELOW_FALLING = close < SMA200 and SMA200 < SMA200 twenty
      sessions earlier; OTHER = the rest.

Relation to R&D #75 (INCONCLUSIVE, keep the waiver): #75 switched the waiver off or floored its R/R
over a whole portfolio. This tests a different design (the waiver kept, but only where its premise
holds) and a different outcome (big losses per pick). It does not re-run #75.

Population (the Oracle's buy filter, aether_oracle.get_oracle_buy_candidates, on the rule_walkforward
panel's fields): Setup full (risk_utils.setup_ok via compute_setup_ok), S10 >= CFG.oracle_s10_floor,
and reward/risk >= CFG.oracle_min_rr_ratio OR elite. NOT applied: the Oracle's bubble z guard (the
panel has no bubble z) and "not already held". The game's own gate (rule_walkforward.admit_mask with
CURRENT) is reported as context. One event per symbol per HORIZON sessions (the first admit; the
next one only after the previous window ended), so one symbol's run is not counted 20 times.

Outcome (from the NEXT close, the #85 lesson; split-adjusted bars): entry = close of the session
after the snapshot day. BIG_LOSS = the lowest close of the next HORIZON (20) sessions is at or below
entry x (1 - BIG_LOSS_PCT) (15%): a loss a stop would have taken. Secondary: the 20-session return
minus SPY's over the same sessions, and the 10-session one.

Decision rule (fixed before the first run). For each test, per calendar month with at least one
event in each group: d_loss = BIG_LOSS rate(flagged) - rate(other) and d_ret = mean excess(flagged)
- mean excess(other). Newey-West t of the monthly series, lag HAC_LAG (1).
  FILTER_HELPS  mean d_loss > 0 AND HAC t(d_loss) >= 1.96 AND mean d_ret <= 0 AND d_loss > 0 in both
                halves (months before / from SPLIT_MONTH) -> propose the filter as its own PR (risk
                rules apply; a pass is a reason to propose, not to wire).
  FILTER_HURTS  mean d_loss < 0 AND HAC t(d_loss) <= -1.96 -> the flagged picks lose LESS: keep.
  INSUFFICIENT  fewer than MIN_MONTHS months with both groups, or fewer than MIN_FLAGGED flagged events.
  INCONCLUSIVE  otherwise -> keep today's rule.
Two tests (A, B), so a single |t| near 2 is weak evidence.

Not measured: stops (the outcome is a path low, not a stopped exit), costs, sizing, taxes, the bubble
guard, the live overrides. Biases: today's universe (survivors flatter weak names: delisted losers
are missing, which works AGAINST finding that FAR / BELOW_FALLING lose more); the panel's Chaikin
snapshots carry the #74 caveat (~20% were wrong-date backfills before the quarantine). The panel
ends 2026-10-14, so SA (2026-11-18) is not in it.

Usage (read-only; the panel from rule_walkforward.py, e.g. R&D #72's):
    python scripts/backtesting/waiver_trend_loss_study.py --panel <rule_wf_panel.pkl> [--out FILE]
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
from aether import paths, risk_utils
from aether.config import CFG

HORIZON = 20
SHORT_H = 10
BIG_LOSS_PCT = 0.15
FAR_PCT = 0.10
HIGH_WINDOW = 252
SMA_N = 200
SLOPE_LAG = 20
HAC_LAG = 1
GATE_T = 1.96
MIN_MONTHS = 12
MIN_FLAGGED = 100
SPLIT_MONTH = "2025-01"


# ── Pure parts (unit-tested) ─────────────────────────────────────────────────

def oracle_mask(a) -> np.ndarray:
    """The Oracle's buy filter on panel arrays, and the waiver-only subset (see the docstring)."""
    s10, l60 = a["s10"], a["l60"]
    elite = (s10 + l60 >= CFG.system_bypass_score_floor) & (s10 >= CFG.system_bypass_s10_floor)
    rr_ok = a["rr"] >= CFG.oracle_min_rr_ratio
    base = a["setup_full"] & (s10 >= CFG.oracle_s10_floor)
    return base & (rr_ok | elite), base & ~rr_ok & elite


def bar_features(closes: list, i: int) -> dict | None:
    """Distance below the 252-session high close and the falling-200-day flag at bar i (bars up to i)."""
    if i < max(HIGH_WINDOW, SMA_N + SLOPE_LAG):
        return None
    hi = max(closes[i - HIGH_WINDOW + 1: i + 1])
    sma = sum(closes[i - SMA_N + 1: i + 1]) / SMA_N
    sma_then = sum(closes[i - SLOPE_LAG - SMA_N + 1: i - SLOPE_LAG + 1]) / SMA_N
    c = closes[i]
    return {"below_high": 1 - c / hi, "below_falling_200": c < sma and sma < sma_then}


def outcome(closes: list, i: int, spy_at) -> dict | None:
    """From the NEXT close (i+1): big loss on the path, 20- and 10-session returns minus SPY's."""
    e = i + 1
    if e + HORIZON >= len(closes):
        return None
    entry = closes[e]
    path = closes[e + 1: e + HORIZON + 1]
    s0, s20, s10 = spy_at(e), spy_at(e + HORIZON), spy_at(e + SHORT_H)
    if not (entry > 0 and s0 and s20 and s10):
        return None
    return {"big_loss": min(path) <= entry * (1 - BIG_LOSS_PCT),
            "ex20": (closes[e + HORIZON] / entry - 1) - (s20 / s0 - 1),
            "ex10": (closes[e + SHORT_H] / entry - 1) - (s10 / s0 - 1)}


def thin(events: list) -> list:
    """One event per symbol per HORIZON sessions: keep the first, skip until its window ended."""
    out, last = [], {}
    for ev in sorted(events, key=lambda x: (x["sym"], x["i"])):
        if ev["i"] > last.get(ev["sym"], -10 ** 9) + HORIZON:
            out.append(ev)
            last[ev["sym"]] = ev["i"]
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def t_hac(xs, lag=HAC_LAG):
    """Newey-West t of mean(xs) vs 0 (setup_s10_gate_study._t_hac)."""
    return sg._t_hac(xs, lag)


def monthly_diffs(events: list, flag: str) -> list:
    """[(month, d_loss, d_ret)] for months with at least one flagged and one other event."""
    by = {}
    for ev in events:
        by.setdefault(ev["day"][:7], ([], []))[0 if ev[flag] else 1].append(ev)
    out = []
    for m in sorted(by):
        f, o = by[m]
        if f and o:
            out.append((m, _mean([x["big_loss"] for x in f]) - _mean([x["big_loss"] for x in o]),
                        _mean([x["ex20"] for x in f]) - _mean([x["ex20"] for x in o])))
    return out


def verdict(diffs: list, n_flagged: int) -> dict:
    """The fixed decision rule over the monthly diffs (see the docstring)."""
    dl = [d for _, d, _ in diffs]
    dr = [r for _, _, r in diffs]
    res = {"months": len(diffs), "n_flagged": n_flagged, "d_loss": _mean(dl), "d_ret": _mean(dr),
           "t_loss": t_hac(dl) if len(dl) > 2 else None, "t_ret": t_hac(dr) if len(dr) > 2 else None}
    h1 = [d for m, d, _ in diffs if m < SPLIT_MONTH]
    h2 = [d for m, d, _ in diffs if m >= SPLIT_MONTH]
    res["d_loss_halves"] = (_mean(h1), _mean(h2))
    if len(diffs) < MIN_MONTHS or n_flagged < MIN_FLAGGED or res["t_loss"] is None:
        res["verdict"] = "INSUFFICIENT"
    elif (res["d_loss"] > 0 and res["t_loss"] >= GATE_T and res["d_ret"] <= 0
          and all(h is not None and h > 0 for h in res["d_loss_halves"])):
        res["verdict"] = "FILTER_HELPS"
    elif res["d_loss"] < 0 and res["t_loss"] <= -GATE_T:
        res["verdict"] = "FILTER_HURTS"
    else:
        res["verdict"] = "INCONCLUSIVE"
    return res


def describe(events: list, flag: str) -> dict:
    f = [e for e in events if e[flag]]
    o = [e for e in events if not e[flag]]

    def g(xs):
        return {"n": len(xs), "big_loss_rate": _mean([x["big_loss"] for x in xs]),
                "mean_ex20": _mean([x["ex20"] for x in xs]), "mean_ex10": _mean([x["ex10"] for x in xs]),
                "median_ex20": float(np.median([x["ex20"] for x in xs])) if xs else None}
    return {"flagged": g(f), "other": g(o)}


# ── I/O ─────────────────────────────────────────────────────────────────────

def _series(sym):
    """(dates, split-adjusted closes) from the project's loader."""
    t = risk_utils._load_ohlcv_bars(sym)
    if not t or not t[0]:
        return None
    return list(t[0]), [float(x) for x in t[-1]]


def build_events(a, mask, flagged_by) -> list:
    spy = _series("SPY")
    sd, sc = spy
    spos = {d: k for k, d in enumerate(sd)}
    cache, events = {}, []
    for idx in np.flatnonzero(mask):
        sym, day = str(a["sym"][idx]), str(a["day"][idx])
        if sym not in cache:
            cache[sym] = _series(sym)
        s = cache[sym]
        if not s:
            continue
        dates, closes = s
        try:
            i = dates.index(day)
        except ValueError:
            continue
        feat = bar_features(closes, i)

        def spy_at(k, dates=dates):
            return sc[spos[dates[k]]] if k < len(dates) and dates[k] in spos else None
        out = outcome(closes, i, spy_at)
        if feat is None or out is None:
            continue
        ev = {"sym": sym, "day": day, "i": i, **feat, **out}
        ev.update(flagged_by(ev, idx))
        events.append(ev)
    return thin(events)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--panel", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    with open(args.panel, "rb") as f:
        rows = pickle.load(f)
    a = wf.to_arrays(rows)
    admit, waiver_only = oracle_mask(a)
    game = wf.admit_mask(a, wf.CURRENT)

    ev_a = build_events(a, waiver_only, lambda ev, idx: {"far": ev["below_high"] > FAR_PCT})
    ev_b = build_events(a, admit, lambda ev, idx: {"bf200": ev["below_falling_200"]})
    ev_g = build_events(a, game, lambda ev, idx: {"bf200": ev["below_falling_200"],
                                                  "far": ev["below_high"] > FAR_PCT})
    res = {
        "run_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "panel": os.path.abspath(args.panel), "panel_rows": len(rows),
        "panel_days": [min(a["day"].tolist()), max(a["day"].tolist())],
        "params": {"HORIZON": HORIZON, "BIG_LOSS_PCT": BIG_LOSS_PCT, "FAR_PCT": FAR_PCT, "HAC_LAG": HAC_LAG,
                   "oracle_s10_floor": CFG.oracle_s10_floor, "oracle_min_rr": CFG.oracle_min_rr_ratio,
                   "bypass": [CFG.system_bypass_score_floor, CFG.system_bypass_s10_floor]},
        "obs": {"oracle_admits": int(admit.sum()), "waiver_only": int(waiver_only.sum()), "game_admits": int(game.sum())},
        "A_waiver_far_from_high": {"events": len(ev_a), **describe(ev_a, "far"),
                                   **verdict(monthly_diffs(ev_a, "far"), sum(e["far"] for e in ev_a))},
        "B_below_falling_200": {"events": len(ev_b), **describe(ev_b, "bf200"),
                                **verdict(monthly_diffs(ev_b, "bf200"), sum(e["bf200"] for e in ev_b))},
        "context_game_gate": {"events": len(ev_g), "bf200": describe(ev_g, "bf200"), "far": describe(ev_g, "far")},
    }
    out = args.out or os.path.join(paths.data_dir(), f"waiver_trend_loss_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, default=str)
    for k in ("A_waiver_far_from_high", "B_below_falling_200"):
        r = res[k]
        sys.stdout.write(f"{k}: {r['verdict']}  events {r['events']} flagged {r['n_flagged']} months {r['months']}  "
                         f"d_loss {r['d_loss']} (t {r['t_loss']}) halves {r['d_loss_halves']}  d_ret {r['d_ret']} (t {r['t_ret']})\n"
                         f"   flagged {r['flagged']}\n   other   {r['other']}\n")
    sys.stdout.write(f"obs {res['obs']}\nsaved {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
