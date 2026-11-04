"""
PROD Review Rules Study (2026-11-04): three behaviours seen on PROD, tested before any rule change.

The 2026-11-04 PROD review (docs/prod-notes/2026-11-04-prod-review-238.md) found three trading
behaviours that need evidence, not a quick fix:

  (A) Elite-waiver R/R (item 2). The R&D #32 waiver lets an elite breakout (S10+L60 >= 8 and
      S10 >= 2) skip the R/R >= 2 and 5% target-gain gates entirely. PROD bought COMP on 11-03 at
      R/R 0.08:1 with a 0.95% target.
  (B) Profit-lock multiplier (item 3). The live profit-lock trails a winner's stop at
      peak - k x ATR with k from the DAY's profile (DEFENSIVE 1.5, BALANCED 2.5, AGGRESSIVE 3.5),
      and the stop never moves down. One DEFENSIVE day (breadth downgrade) therefore tightens
      every winner for good: on 11-04 MSCI's stop went from $597.66 to $636.79.
  (C) Scale-out then scale-in (item 4). Bank-As-You-Go sells part of a winner and pyramiding
      later buys it back higher: MSCI sold at $591.63 on 10-20, added at $610.62 on 10-22.

(A) and (B) replay portfolio_backtest.simulate on the rule_walkforward panel (as
entry_gates_portfolio.py does) with today's live entry gates passed through `admit`, and with the
LIVE profile: the SPY-L60 band plus the SPY-RSP breadth downgrade, from
ai_portfolio_game.breadth_downgrade and trend_score_from_closes (the live functions). The
portfolio backtest alone uses the SPY band only.

Variants
  (A) current        live gates: S10 >= 2, Setup full, R/R >= 2 and gain >= 5% unless elite
      waiver_off     no waiver: every buy needs R/R >= 2 and gain >= 5%
      waiver_floor   the waiver still needs R/R >= WAIVER_FLOOR_RR (0.5)
  (B) lock_day       live profit-lock + breakeven lock, multiple from the day's profile
      lock_entry     the same, multiple fixed at the entry profile
      (no_lock, today's portfolio backtest without the profit-lock, is reported for context)

Decision rules (fixed before the first run), on the test window (TEST_START onward), each a
daily portfolio return difference with a Newey-West t, lag 5:
  (A) waiver_off - current and waiver_floor - current (two tests, so one |t| ~ 2 is weak):
      TIGHTEN    mean > 0 AND HAC t >= 1.96 AND max drawdown no more than MAX_EXTRA_DD points
                 deeper than current's -> propose that variant as its own PR (risk rules apply).
      WAIVER_HELPS  waiver_off - current: mean < 0 AND HAC t <= -1.96 -> keep the waiver.
      INCONCLUSIVE  otherwise -> keep today's waiver.
  (B) lock_entry - lock_day:
      ENTRY_MULT  mean > 0 AND HAC t >= 1.96 AND drawdown no more than MAX_EXTRA_DD points deeper
                  -> propose fixing the multiple at entry, as its own PR.
      DAY_MULT    mean < 0 AND HAC t <= -1.96 -> keep the live rule.
      INCONCLUSIVE otherwise -> keep the live rule.
  (C) The portfolio backtest has no scale-outs or pyramiding, so (C) reads the PROD ledger
      (ai_portfolio_game.json history): every BUY_SCALE_IN, whether a Bank-As-You-Go scale-out of
      the same symbol came in the CHURN_DAYS calendar days before it, the add price vs that sale
      price, and the add's return to the next full exit or the latest cached close (nominal prices:
      a split after the add would distort it). One portfolio's ledger: a direction, not a
      significance test (CHURN_ADDS_WORSE / NO_CLEAR_DIFFERENCE / TOO_FEW, no verdict).

Not simulated: overrides, bottom-confirmed buys, the cash-deployment upgrade (DEFENSIVE ->
BALANCED with > 40% cash), scarcity sizing, scale-outs, covered calls, rotation, the adaptive S10
floor, the 07:00-only stop check (simulated stops fill intraday). Today's universe (survivorship).
Scores and gates come from the rule_walkforward panel (rows need 20 future bars, so each
symbol's last 20 bars carry no scores in any variant).

Usage (from a worktree, reading the real caches; build the panel with rule_walkforward.py first):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/prod_review_rules_study.py \\
        --panel <rule_wf_panel.pkl> --game <ai_portfolio_game.json> [--out FILE]
"""
import argparse
import bisect
import datetime
import json
import os
import pickle
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import ai_portfolio_game as game
import backtest_ratings as br
import entry_gates_portfolio as egp
import instruments
import portfolio_backtest as pb
import rule_walkforward as wf
import setup_s10_gate_study as sg
from aether.config import CFG

TEST_START = "2024-01-01"
HAC_LAG = 5
GATE_T = 1.96
MAX_EXTRA_DD = 5.0
WAIVER_FLOOR_RR = 0.5
CHURN_DAYS = 14
MIN_LEDGER_N = 3


# ── Live profile (SPY band + breadth downgrade) ──────────────────────────────

def closes_by_day(ts: dict) -> tuple[list, list]:
    """(sorted dates, closes) from a raw OHLCV time series, as ai_portfolio_game._load_closes reads it."""
    days = sorted(ts)
    return days, [float(ts[d]["4. close"]) for d in days]


def live_profiles(calendar, spy_l60: dict, spy_ts: dict, rsp_ts: dict) -> dict:
    """{day: profile} the live get_market_regime would pick from data up to that day's close."""
    sd, sc = closes_by_day(spy_ts)
    rd, rc = closes_by_day(rsp_ts)
    out = {}
    for day in calendar:
        base = pb.profile_for(spy_l60.get(day, [None, None])[1])
        spy = game.trend_score_from_closes(sc[:bisect.bisect_right(sd, day)])
        rsp = game.trend_score_from_closes(rc[:bisect.bisect_right(rd, day)])
        out[day] = game.breadth_downgrade(base, spy, rsp)
    return out


def profile_changes(profiles: dict) -> int:
    seq = [profiles[d] for d in sorted(profiles)]
    return sum(1 for a, b in zip(seq, seq[1:]) if a != b)


# ── (A) waiver variants ──────────────────────────────────────────────────────

def rr_masks(a) -> dict:
    """Admit masks of the three (A) variants. `current` must equal wf.admit_mask(a, wf.CURRENT)."""
    floor, setup, min_rr, waived, leader, rebound = wf.CURRENT
    base = wf.admit_mask(a, (floor, setup, 0.0, waived, leader, rebound))   # every gate but R/R
    s10 = a["s10w"] if waived else a["s10"]
    elite = (s10 + a["l60"] >= CFG.system_bypass_score_floor) & (s10 >= CFG.system_bypass_s10_floor)
    rr_ok = (a["rr"] >= min_rr) & (a["gain"] >= 5.0)
    return {"current": base & (rr_ok | elite),
            "waiver_off": base & rr_ok,
            "waiver_floor": base & (rr_ok | (elite & (a["rr"] >= WAIVER_FLOOR_RR)))}


def as_set(a, mask) -> frozenset:
    return frozenset(zip(a["sym"][mask].tolist(), a["day"][mask].tolist()))


# ── Verdicts ─────────────────────────────────────────────────────────────────

def _passes(test, dd_variant, dd_base) -> bool:
    m, t = test.get("mean_daily_pct"), test.get("t_hac")
    return m is not None and t is not None and m > 0 and t >= GATE_T and dd_variant >= dd_base - MAX_EXTRA_DD


def _hurts(test) -> bool:
    m, t = test.get("mean_daily_pct"), test.get("t_hac")
    return m is not None and t is not None and m < 0 and t <= -GATE_T


def waiver_verdict(tests: dict, dd: dict) -> str:
    """tests: {variant: diff_test vs current}; dd: {variant: test-window max drawdown %}."""
    passing = [v for v in ("waiver_off", "waiver_floor") if _passes(tests[v], dd[v], dd["current"])]
    if passing:
        return "TIGHTEN:" + ",".join(passing)
    if _hurts(tests["waiver_off"]):
        return "WAIVER_HELPS"
    return "INCONCLUSIVE"


def lock_verdict(test: dict, dd_entry, dd_day) -> str:
    if _passes(test, dd_entry, dd_day):
        return "ENTRY_MULT"
    if _hurts(test):
        return "DAY_MULT"
    return "INCONCLUSIVE"


# ── (C) PROD ledger ──────────────────────────────────────────────────────────

def _date(s):
    return datetime.date.fromisoformat(s)


def ledger_scale_ins(history: list, last_close) -> dict:
    """Each BUY_SCALE_IN, flagged when a scale-out of the same symbol came in the CHURN_DAYS before.

    last_close(sym, entry_date, exit_date) -> the close to mark the add at: the price of the next
    full SELL of that symbol when the ledger has one, else this callable's answer (latest close).
    """
    rows = []
    for k, tx in enumerate(history):
        if tx.get("type") != "BUY_SCALE_IN":
            continue
        sym, day, px = tx["symbol"], tx["date"], float(tx["price"])
        prior = [t for t in history[:k] if t.get("symbol") == sym and t.get("type") == "SELL"
                 and "Scale-out" in str(t.get("details")) and 0 <= (_date(day) - _date(t["date"])).days <= CHURN_DAYS]
        exit_px = next((float(t["price"]) for t in history[k + 1:] if t.get("symbol") == sym and t.get("type") == "SELL"
                        and "Scale-out" not in str(t.get("details"))), None)
        if exit_px is None:
            exit_px = last_close(sym, day)
        row = {"symbol": sym, "date": day, "price": px, "after_scale_out": bool(prior),
               "ret_pct": round((exit_px / px - 1) * 100, 2) if exit_px else None}
        if prior:
            row["sold_at"] = float(prior[-1]["price"])
            row["vs_sale_pct"] = round((px / row["sold_at"] - 1) * 100, 2)
        rows.append(row)

    def summary(group):
        r = [x["ret_pct"] for x in group if x["ret_pct"] is not None]
        return {"n": len(group), "mean_ret_pct": round(float(np.mean(r)), 2) if r else None,
                "median_ret_pct": round(float(np.median(r)), 2) if r else None}

    churn = [x for x in rows if x["after_scale_out"]]
    other = [x for x in rows if not x["after_scale_out"]]
    out = {"after_scale_out": summary(churn), "other_scale_ins": summary(other), "rows": rows}
    if len(churn) < MIN_LEDGER_N or len(other) < MIN_LEDGER_N:
        out["direction"] = "TOO_FEW"
    else:
        worse = (out["after_scale_out"]["mean_ret_pct"] < out["other_scale_ins"]["mean_ret_pct"]
                 and out["after_scale_out"]["median_ret_pct"] < out["other_scale_ins"]["median_ret_pct"])
        out["direction"] = "CHURN_ADDS_WORSE" if worse else "NO_CLEAR_DIFFERENCE"
    return out


def latest_close_fn():
    cache = {}

    def last_close(sym, _day):
        if sym not in cache:
            ts = br.load_ohlcv(sym)
            cache[sym] = float(ts[max(ts)]["4. close"]) if ts else None
        return cache[sym]
    return last_close


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--game", required=True, help="ai_portfolio_game.json (the PROD copy)")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.panel, "rb") as f:
        rows = pickle.load(f)
    a = wf.to_arrays(rows)
    panel = {}
    for r in rows:
        panel.setdefault(r[1], {})[r[0]] = [r[2], r[4]]
    symbols = sorted(set(panel) | {"SPY"})
    raw = {s: br.load_ohlcv(s) for s in set(symbols) | {"RSP"}}
    bars = {s: b for s in symbols if raw.get(s) and (b := pb.bars_from_series(raw[s]))}
    first, last = min(a["day"]), max(a["day"])
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if first <= d <= last]
    test_cal = [d for d in calendar if d >= TEST_START]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    profiles = live_profiles(calendar, panel.get("SPY", {}), raw["SPY"], raw["RSP"])

    masks = rr_masks(a)
    assert np.array_equal(masks["current"], wf.admit_mask(a, wf.CURRENT)), "current gates drifted from rule_walkforward"
    admits = {k: egp.in_set(as_set(a, m)) for k, m in masks.items()}
    runs = {"current": (admits["current"], None),
            "waiver_off": (admits["waiver_off"], None),
            "waiver_floor": (admits["waiver_floor"], None),
            "no_lock": (admits["current"], None),
            "lock_day": (admits["current"], {"lock": "day"}),
            "lock_entry": (admits["current"], {"lock": "entry"})}
    # `current` and `no_lock` are the same run; keep both names so each study reads on its own.
    out = {"meta": {"run_date": str(datetime.date.today()), "test_start": TEST_START,
                    "calendar": [calendar[0], calendar[-1]], "symbols": len(symbols), "cost_bps": args.cost_bps,
                    "profile_changes_test": profile_changes({d: profiles[d] for d in test_cal}),
                    "profile_days_test": {p: sum(1 for d in test_cal if profiles[d] == p)
                                          for p in ("AGGRESSIVE", "BALANCED", "DEFENSIVE")},
                    "admitted_obs": {k: int(m.sum()) for k, m in masks.items()}},
           "test": {}, "full": {}}
    daily = {}
    for window, cal in (("test", test_cal), ("full", calendar)):
        for name, (adm, ex) in runs.items():
            res = pb.simulate(panel, bars, cal, risk_rules=True, cost_bps=args.cost_bps, excluded=excluded,
                              max_trade_risk_pct=CFG.system_max_trade_risk_pct, admit=adm, exit_rules=ex,
                              profile_fn=profiles.get)
            out[window][name] = pb.metrics(res)
            daily[(window, name)] = egp.daily_returns(res["curve"])

    def diff(x, y):
        return egp.diff_test(daily[("test", x)], daily[("test", y)])

    dd = {k: out["test"][k]["max_drawdown_pct"] for k in runs}
    a_tests = {v: diff(v, "current") for v in ("waiver_off", "waiver_floor")}
    b_test = diff("lock_entry", "lock_day")
    out["waiver"] = {"vs_current_test": a_tests, "verdict": waiver_verdict(a_tests, dd)}
    out["lock"] = {"entry_minus_day_test": b_test, "lock_day_minus_no_lock_test": diff("lock_day", "no_lock"),
                   "verdict": lock_verdict(b_test, dd["lock_entry"], dd["lock_day"])}

    with open(args.game, encoding="utf-8") as f:
        history = json.load(f).get("history", [])
    out["scale_in_ledger"] = ledger_scale_ins(history, latest_close_fn())

    sg._out(json.dumps({k: out[k] for k in ("meta", "waiver", "lock")}, indent=1))
    led = out["scale_in_ledger"]
    sg._out(f"\n(C) scale-ins: after a scale-out {led['after_scale_out']}, others {led['other_scale_ins']}, "
            f"direction {led['direction']}")
    cols = ("cagr_pct", "total_return_pct", "max_drawdown_pct", "sharpe", "avg_exposure_pct", "n_trades", "win_rate")
    for window in ("test", "full"):
        sg._out(f"\n[{window}]  " + "  ".join(cols))
        for name, m in out[window].items():
            sg._out(f"  {name:<14}" + "".join(f"{str(m.get(c)):>12}" for c in cols))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
