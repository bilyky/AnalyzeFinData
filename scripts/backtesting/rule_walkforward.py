"""
Entry-Rule Walk-Forward — retune the buy gates each month on what was known then, and check
whether that beats today's fixed rules on months it never saw.

Why: the live buy gates (Short10 floor, Setup, R/R >= 2, combined >= 5) admit almost nothing,
and a 2026-10 scan found 48 runs of +40% in 60 days since April that we never bought - most
blocked by the Short10 floor on their Setup = 1 days. The owner asked for a model that adjusts
its parameters to the current market and keeps adjusting as new winners appear. Tuning on
known winners is hindsight, so every choice here is made walk-forward:

    for each month M (from WF_START):
        TRAIN = observations from the TRAIN_MONTHS before M whose 20-day outcome was already
                known on M's first day (entry date <= M - 30 calendar days)
        pick the rule with the best TRAIN objective (below)
        TEST  = month M, traded with that rule

Rule grid (each a full gate stack; scores from backtest_ratings.compute_br):
    s10_floor    {-2, 0, 1, 2, 3}       Short10 >= floor (and S10+L60 >= 5)
    setup        {full, sma, dir, off}  both halves of risk_utils.setup_ok / one / none
    min_rr       {0, 1, 2}              R/R (production resolve_stop/target_detailed) >= min and
                                        target gain >= 5%, waived for elite breakouts (R&D #32);
                                        0 = no R/R gate
    waived       {False, True}          score with the R&D #34 breakout waiver always on
    leader_path  {False, True}          also admit Chaikin all-Strong leaders (R&D #55)
    rebound_path {False, True}          also admit 60-day losers of 20%+ with money flow not Weak
CURRENT = (2, full, 2, False, False, False), today's live stack (adaptive floor not modelled).

Per-date return of a rule: the mean 20-day forward return of what it admits that day
(winsorized 1/99%), or 0 when it admits nothing (cash). The 1/99% bounds are taken over the
whole sample, test months included: a small use of later data, kept for simplicity. TRAIN objective: the mean of that
per-date return, among rules that admit on at least 20% of TRAIN dates and whose admitted
names lose 20%+ in 20 days no more often than the whole universe does (pre-registered;
catch-rate is reported, never optimized, or "admit everything" would win).

Decision rule (fixed before running):
    Primary   per test date, adaptive minus CURRENT per-date return; Newey-West t across
              dates, lag 19 (daily samples of a 20-day return).
    ADAPTIVE_HELPS  mean > 0 AND HAC t >= 1.96 AND the adaptive picks' winner catch rate
                    >= CURRENT's -> propose an adaptive mode (weekly re-pick, bounded steps,
                    owner approval), as its own PR gated on the portfolio backtest.
    ADAPTIVE_HURTS  mean < 0 AND HAC t <= -1.96.
    INCONCLUSIVE    otherwise -> keep the fixed rules.
Reported alongside: a STATIC rule (the single grid rule with the best whole-sample per-date
return, in-sample, labelled as such), catch rate of winner runs (+40% in 60 days) admitted
within their first 20 trading days, loss share, admitted per date, and which rule each
month picked.

Not measured: stops, sizing, slots, costs (the portfolio backtest does those). Biases:
today's universe; digit_sum index fitted on the same history; unadjusted closes (entries
skipped when snapshot and OHLCV close differ by >10%, returns winsorized).

Usage:
    python scripts/backtesting/rule_walkforward.py [--data-dir PATH] [--panel FILE] [--rebuild] [--out FILE]
"""
import argparse
import datetime
import glob
import itertools
import json
import multiprocessing
import os
import pickle
import sys
from pathlib import Path

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
import backtest_ratings as br
import setup_s10_gate_study as sg
from aether import risk_utils

MIN_YEAR = 2023
WF_START = "2024-01"
TRAIN_MONTHS = 12
KNOWN_LAG_DAYS = 30            # a 20-trading-day outcome is known ~28 calendar days later
MIN_ADMIT_DAY_SHARE = 0.20
WINNER_PCT, LOSS_PCT = 40.0, -20.0
MIN_COMBINED = 5.0
GATE_T = 1.96
LOOKBACK_BARS = 260

GRID = {"s10_floor": (-2.0, 0.0, 1.0, 2.0, 3.0), "setup": ("full", "sma", "dir", "off"),
        "min_rr": (0.0, 1.0, 2.0), "waived": (False, True), "leader_path": (False, True),
        "rebound_path": (False, True)}
CURRENT = (2.0, "full", 2.0, False, False, False)
FIELDS = ("day", "sym", "s10", "s10w", "l60", "above", "up3", "rr", "gain", "leader",
          "mf_weak", "past60", "f20", "f60", "setup_full")


# ── Extraction ────────────────────────────────────────────────────────────────

def _risk(price, idx, dates, ts):
    """(R/R, target gain %) from the production resolvers on bars up to idx."""
    lo = max(0, idx - LOOKBACK_BARS + 1)
    win = dates[lo: idx + 1]
    hi = [float(ts[d].get("2. high", 0)) for d in win]
    low = [float(ts[d].get("3. low", 0)) for d in win]
    cl = [float(ts[d].get("4. close", 0)) for d in win]
    stop = risk_utils.resolve_stop_detailed(price, highs=hi, lows=low, closes=cl)["stop"] or 0
    target = risk_utils.resolve_target_detailed(price, highs=hi, lows=low, closes=cl)["target"] or 0
    if not (stop and target and price > stop):
        return 0.0, 0.0
    return (target - price) / (price - stop), (target - price) / price * 100


def _symbol_rows(job):
    sym, sym_dir, ohlcv_dir = job
    ts = sg._load(ohlcv_dir, sym)
    if not ts:
        return []
    dates = sorted(ts)
    pos = {d: i for i, d in enumerate(dates)}
    seas = br.precompute_seasonality(ts)
    rows, prev = [], None
    for path in sorted(glob.glob(os.path.join(sym_dir, sym, f"{sym}_*.json"))):
        day = Path(path).stem[len(sym) + 1:]
        if day < str(MIN_YEAR) or day not in pos:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            prev = None
            continue
        if data.get("status") == "invalid symbol":
            prev = data
            continue
        meta = (data.get("metaInfo") or [{}])[0]
        cl = data.get("checklist_stocks") or {}
        try:
            price = float(meta.get("Last") or cl.get("lastPrice") or 0)
        except (TypeError, ValueError):
            price = 0.0
        i = pos[day]
        close = float(ts[day].get("4. close", 0))
        if price <= 0 or i < 60 or i + 20 >= len(dates) or (close > 0 and abs(price - close) / close > 0.1):
            prev = data
            continue
        r = br.compute_br(data, prev, price, i, dates, ts, seas)
        rw = br.compute_br(data, prev, price, i, dates, ts, seas, setup_ok=True)
        sma = sum(float(ts[d]["4. close"]) for d in dates[i - 20: i]) / 20
        c = lambda k: float(ts[dates[k]]["4. close"])
        rr, gain = _risk(price, i, dates, ts)
        leader = (br.extract_pgr_corr(data) == 5 and all(str(cl.get(k) or "").strip() == "Strong"
                                                          for k in ("ltTrend", "moneyFlow", "relativeStrength")))
        rows.append((day, sym, r[1], rw[1], r[2], price > sma, price > c(i - 3), rr, gain, leader,
                     str(cl.get("moneyFlow") or "").strip() == "Weak", (price - c(i - 60)) / c(i - 60) * 100,
                     (c(i + 20) - price) / price * 100,
                     (c(i + 60) - price) / price * 100 if i + 60 < len(dates) else float("nan"),
                     bool(br.compute_setup_ok(price, i, dates, ts))))
        prev = data
    return rows


def build_panel(data_dir, path, rebuild=False, workers=None):
    if not rebuild and os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    sym_dir, ohlcv_dir = os.path.join(data_dir, "Symbol"), os.path.join(data_dir, "Symbol_full")
    syms = sorted({Path(p).name.replace("_daily.json", "") for p in glob.glob(os.path.join(ohlcv_dir, "*_daily.json"))}
                  & {d for d in os.listdir(sym_dir) if os.path.isdir(os.path.join(sym_dir, d))})
    rows = []
    with multiprocessing.Pool(workers or max(1, (os.cpu_count() or 2) - 2)) as pool:
        for n, r in enumerate(pool.imap_unordered(_symbol_rows, [(s, sym_dir, ohlcv_dir) for s in syms], chunksize=4), 1):
            rows.extend(r)
            if n % 100 == 0 or n == len(syms):
                sys.stderr.write(f"  {n}/{len(syms)} symbols, {len(rows)} obs\n")
    with open(path, "wb") as f:
        pickle.dump(rows, f)
    return rows


# ── Rules ─────────────────────────────────────────────────────────────────────

def to_arrays(rows) -> dict:
    a = {k: np.array([r[i] for r in rows]) for i, k in enumerate(FIELDS)}
    a["day"] = a["day"].astype(str)
    return a


def admit_mask(a, rule) -> np.ndarray:
    """Boolean mask of observations the rule would buy."""
    floor, setup, min_rr, waived, leader_path, rebound_path = rule
    s10 = a["s10w"] if waived else a["s10"]
    l60 = a["l60"]   # long_score does not read setup_ok, so the waiver leaves L60 unchanged
    m = (s10 >= floor) & (s10 + l60 >= MIN_COMBINED)
    if setup == "full":
        m &= a["setup_full"]          # risk_utils.setup_ok itself; the halves below are inline
    elif setup == "sma":
        m &= a["above"]
    elif setup == "dir":
        m &= a["up3"]
    if min_rr > 0:
        elite = (s10 + l60 >= risk_utils.CFG.system_bypass_score_floor) & (s10 >= risk_utils.CFG.system_bypass_s10_floor)
        m &= ((a["rr"] >= min_rr) & (a["gain"] >= 5.0)) | elite
    if leader_path:
        m |= a["leader"]
    if rebound_path:
        m |= (a["past60"] <= -20.0) & ~a["mf_weak"]
    return m


def winner_starts(a) -> np.ndarray:
    """Index of the first observation of each run of +40% 60-day returns per symbol (a new run
    starts after a gap of more than 15 calendar days)."""
    idx = np.where(a["f60"] >= WINNER_PCT)[0]
    order = idx[np.lexsort((a["day"][idx], a["sym"][idx]))]
    starts, last_sym, last_day = [], None, None
    for k in order:
        sym, day = a["sym"][k], datetime.date.fromisoformat(a["day"][k])
        if sym != last_sym or (day - last_day).days > 15:
            starts.append(k)
        last_sym, last_day = sym, day
    return np.array(starts, dtype=int)


def caught(a, mask, starts, window_days=28) -> np.ndarray:
    """Per winner start: admitted on any of its symbol's observations within window_days."""
    by_sym = {}
    for k in np.where(mask)[0]:
        by_sym.setdefault(a["sym"][k], []).append(datetime.date.fromisoformat(a["day"][k]))
    out = np.zeros(len(starts), dtype=bool)
    for j, k in enumerate(starts):
        d0 = datetime.date.fromisoformat(a["day"][k])
        out[j] = any(0 <= (d - d0).days <= window_days for d in by_sym.get(a["sym"][k], ()))
    return out


def per_date_return(day_idx, ndays, f20w, mask):
    """Mean winsorized 20-day return of admitted obs per date; 0 (cash) where none admitted."""
    s = np.bincount(day_idx[mask], weights=f20w[mask], minlength=ndays)
    n = np.bincount(day_idx[mask], minlength=ndays)
    return np.where(n > 0, s / np.maximum(n, 1), 0.0), n


def train_window(days: np.ndarray, month: str) -> np.ndarray:
    """Indices of `days` whose 20-day outcome was known on `month`'s first day: dated at least
    KNOWN_LAG_DAYS before it, and within TRAIN_MONTHS before it. Never the month itself."""
    m0 = datetime.date.fromisoformat(month + "-01")
    known = (m0 - datetime.timedelta(days=KNOWN_LAG_DAYS)).isoformat()
    first = (m0 - datetime.timedelta(days=31 * TRAIN_MONTHS)).isoformat()
    return np.where((days >= first) & (days <= known))[0]


def choose(train_days, ret, cnt, loss_cnt, base_loss_share):
    """Index of the rule with the best mean per-date return on train_days, among rules that
    admit on >= MIN_ADMIT_DAY_SHARE of the days and lose 20%+ no more often than the universe."""
    best, best_v = None, -np.inf
    for r in range(ret.shape[0]):
        n = cnt[r, train_days]
        if (n > 0).mean() < MIN_ADMIT_DAY_SHARE or n.sum() == 0:
            continue
        if loss_cnt[r, train_days].sum() / n.sum() > base_loss_share:
            continue
        v = ret[r, train_days].mean()
        if v > best_v:
            best, best_v = r, v
    return best


def verdict(diff_mean, t_hac, catch_adaptive, catch_current) -> str:
    if diff_mean is None or t_hac is None:
        return "INCONCLUSIVE"
    if diff_mean > 0 and t_hac >= GATE_T and catch_adaptive >= catch_current:
        return "ADAPTIVE_HELPS"
    if diff_mean < 0 and t_hac <= -GATE_T:
        return "ADAPTIVE_HURTS"
    return "INCONCLUSIVE"


def run(rows) -> dict:
    a = to_arrays(rows)
    lo, hi = np.quantile(a["f20"], [0.01, 0.99])
    f20w = np.clip(a["f20"], lo, hi)
    days = np.array(sorted(set(a["day"])))
    day_idx = np.searchsorted(days, a["day"])
    loss = a["f20"] <= LOSS_PCT
    rules = list(itertools.product(*GRID.values()))
    ret = np.zeros((len(rules), len(days)))
    cnt = np.zeros((len(rules), len(days)), dtype=int)
    loss_cnt = np.zeros((len(rules), len(days)), dtype=int)
    masks = []
    for r, rule in enumerate(rules):
        m = admit_mask(a, rule)
        masks.append(m)
        ret[r], cnt[r] = per_date_return(day_idx, len(days), f20w, m)
        loss_cnt[r] = np.bincount(day_idx[m & loss], minlength=len(days))
    cur = rules.index(CURRENT)
    starts = winner_starts(a)
    start_month = np.array([a["day"][k][:7] for k in starts])

    months = sorted({d[:7] for d in days if d[:7] >= WF_START})
    chosen, adaptive_ret = {}, np.full(len(days), np.nan)
    adaptive_cnt = np.zeros(len(days))
    universe, _ = per_date_return(day_idx, len(days), f20w, np.ones(len(a["day"]), dtype=bool))
    caught_adaptive = np.zeros(len(starts), dtype=bool)
    in_test = np.zeros(len(starts), dtype=bool)
    for mth in months:
        train = train_window(days, mth)
        test = np.where(np.char.startswith(days, mth))[0]
        in_train = np.isin(a["day"], days[train])
        base_loss = float(loss[in_train].mean()) if in_train.any() else 0.0
        r = choose(train, ret, cnt, loss_cnt, base_loss)
        if r is None:
            r = cur
        chosen[mth] = dict(zip(GRID, rules[r]))
        adaptive_ret[test] = ret[r, test]
        adaptive_cnt[test] = cnt[r, test]
        sel = start_month == mth
        in_test |= sel
        caught_adaptive[sel] = caught(a, masks[r], starts[sel])
    test_days = np.where(~np.isnan(adaptive_ret))[0]
    diff = list(adaptive_ret[test_days] - ret[cur, test_days])
    t_hac, t = sg._t_hac(diff, 19), sg._t_one_sample(diff)
    caught_current = caught(a, masks[cur], starts[in_test])
    static = int(np.argmax(ret[:, test_days].mean(axis=1)))
    caught_static = caught(a, masks[static], starts[in_test])

    def summary(series, name, catches):
        n = int(in_test.sum())
        return {"rule": name, "mean_per_date_20d": round(float(np.nanmean(series)), 3),
                "winner_runs_caught": f"{int(catches.sum())}/{n}",
                "catch_rate": round(float(catches.mean()), 3) if n else None}

    cur_cnt = cnt[cur, test_days]
    rep = {
        "counts": {"obs": len(rows), "dates": len(days), "rules": len(rules), "test_months": len(months),
                   "winner_runs_total": len(starts), "winner_runs_in_test": int(in_test.sum())},
        "adaptive": summary(adaptive_ret[test_days], "walk-forward pick", caught_adaptive[in_test]),
        "current": dict(summary(ret[cur, test_days], dict(zip(GRID, CURRENT)), caught_current),
                        admit_days_share=round(float((cur_cnt > 0).mean()), 3)),
        "static_in_sample": summary(ret[static, test_days], dict(zip(GRID, rules[static])), caught_static),
        "adaptive_minus_current": {"days": len(diff), "mean": round(float(np.mean(diff)), 3),
                                   "t": round(t, 2) if t is not None else None,
                                   "t_hac": round(t_hac, 2) if t_hac is not None else None},
        "chosen_by_month": chosen,
        # Equal-weight average of every stock each date: a rule that only buys more names
        # would match this, not beat it.
        "universe_mean_per_date_20d": round(float(universe[test_days].mean()), 3),
        "admitted_per_date": {"adaptive": round(float(adaptive_cnt[test_days].mean()), 1),
                              "current": round(float(cnt[cur, test_days].mean()), 1)},
    }
    for name, series in (("adaptive_minus_universe", adaptive_ret[test_days]),
                         ("current_minus_universe", ret[cur, test_days])):
        d = list(series - universe[test_days])
        th = sg._t_hac(d, 19)
        rep[name] = {"mean": round(float(np.mean(d)), 3), "t_hac": round(th, 2) if th is not None else None}
    rep["verdict"] = verdict(rep["adaptive_minus_current"]["mean"], rep["adaptive_minus_current"]["t_hac"],
                             caught_adaptive[in_test].mean(), caught_current.mean())
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.dirname(_HERE)), "Data"))
    ap.add_argument("--panel", default=None, help="pickle cache of the extracted observations")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    panel = args.panel or os.path.join(args.data_dir, "rule_walkforward_panel.pkl")
    rows = build_panel(args.data_dir, panel, args.rebuild)
    rep = run(rows)
    rep["meta"] = {"run_date": str(datetime.date.today()), "first_obs": min(r[0] for r in rows),
                   "last_obs": max(r[0] for r in rows), "wf_start": WF_START, "train_months": TRAIN_MONTHS}
    sg._out(json.dumps(rep, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)


if __name__ == "__main__":
    main()
