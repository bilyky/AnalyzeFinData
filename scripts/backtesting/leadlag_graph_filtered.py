"""
leadlag_graph_filtered.py — R&D #93b: the #93 lead-lag graph again, with the stale-price filter
fixed BEFORE the run and two separate test windows.

Why: #93 (GRAPH_REAL, 95 links vs 0 in placebos) had a third of its links pointing into thinly
traded ETFs and foreign listings, where a late close "follows" everything; that filter was chosen
after the run. This run fixes it in advance and asks each link to hold twice. Written after #93's
result (disclosed); rules fixed here before this script's full run.

Universe (decided from TRAIN data only, so no look-ahead):
  - follower: a US SEC filer (SIC code in the EDGAR submissions cache), not an ETF (snapshot
    metaInfo.etf_group_name), median daily dollar volume (close x volume) >= $20M over its
    train-window bars;
  - leader: any screen symbol (ETFs allowed) with the same >= $20M train median;
  - SPY is the market, never a leader or follower.
Windows: train 2001-01-01 .. 2015-12-31; test A 2016-01-01 .. 2020-12-31; test B from 2021-01-01.
Method: as #93 (leadlag_graph_study): train-only SPY beta and winsor cut-offs; follower whitened
for its own last move (train AR(1)); rho between leader residual at t and follower at t+L, L 1..5;
train n >= 750; Benjamini-Hochberg q 0.05 over all allowed train tests.
A link is CONFIRMED only if, in BOTH test A and test B: n >= 500, same sign as train, one-sided
p < 0.01.
Placebo: the same pipeline with each leader shifted circularly inside each window by one offset of
252-756 sessions; 3 replicates, numpy seed 931.
Decision rule (as #93):
    GRAPH_REAL   confirmed >= 20 AND >= 3x the largest placebo count
    NO_GRAPH     confirmed <= 1.5x the mean placebo count (or zero)
    INCONCLUSIVE otherwise
Also reported: the same counts if only test A or only test B were required (context), and each
confirmed link's rho per window and its +-2 sigma follower move in each test window (bp).

Not measured: costs, the overnight vs intraday split, capacity (that is #95). Biases: today's
universe (survivors); the $20M bar uses each stock's own train years, so a stock that was small
then and large now is out.

Usage: AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/leadlag_graph_filtered.py
"""
import argparse
import datetime
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import leadlag_graph_study as lg  # noqa: E402
import position_watch as pw  # noqa: E402
from aether import paths  # noqa: E402

START, TEST_A, TEST_B = "2001-01-01", "2016-01-01", "2021-01-01"
MIN_DV = 20e6
SEED = 931


def allowed(syms: list, dollar_vol: dict, lab: dict) -> tuple:
    """(leader ok, follower ok) boolean arrays over syms."""
    lead = np.array([dollar_vol.get(s, 0.0) >= MIN_DV for s in syms])
    fol = np.array([lead[k] and bool(lab.get(s, {}).get("sic_div")) and not lab.get(s, {}).get("etf")
                    for k, s in enumerate(syms)])
    return lead, fol


def train_dollar_volume(days: list, closes: list, vols: list, end: str) -> float:
    dv = [c * v for d, c, v in zip(days, closes, vols, strict=True) if START <= d < end and c > 0 and v > 0]
    return float(np.median(dv)) if dv else 0.0


def pair_corr(a: np.ndarray, b: np.ndarray, lag: int, rows: np.ndarray) -> tuple:
    """(rho, n): corr(a[t], b[t+lag]) over t with t and t+lag in rows, pairwise complete."""
    keep = rows[:-lag] & rows[lag:]
    x, y = a[:-lag][keep], b[lag:][keep]
    ok = ~np.isnan(x) & ~np.isnan(y)
    n = int(ok.sum())
    if n < 3 or np.std(x[ok]) == 0 or np.std(y[ok]) == 0:
        return float("nan"), n
    return float(np.corrcoef(x[ok], y[ok])[0, 1]), n


def confirmed_in(E, W, rows, lag, i, j, sign) -> tuple:
    r, nn = pair_corr(E[:, i], W[:, j], lag, rows)
    if not np.isfinite(r):
        return False, r, nn
    _p, pp, pn = lg.p_values(np.array([[r]]), np.array([[nn]]))
    p = float(pp[0, 0] if sign > 0 else pn[0, 0])
    return (nn >= lg.MIN_TEST and np.sign(r) == sign and p < lg.P_TEST), r, nn


def find_edges(E, W, train, test_a, test_b, lead_ok, fol_ok) -> dict:
    """{"both": [...], "a_only_rule": n, "b_only_rule": n}; each edge (lag, i, j, r_train, r_a, r_b)."""
    allow = np.outer(lead_ok, fol_ok)
    np.fill_diagonal(allow, False)
    per_lag, all_p = [], []
    for lag in lg.LAGS:
        rho, n = lg.lagged_corr(E, W, lag, train)
        p, _pp, _pn = lg.p_values(rho, n)
        p[(n < lg.MIN_TRAIN) | ~np.isfinite(rho) | ~allow] = np.nan
        per_lag.append(rho)
        all_p.append(p)
    disc = lg.bh_mask(np.stack(all_p))
    both, a_n, b_n = [], 0, 0
    for k, lag in enumerate(lg.LAGS):
        for i, j in zip(*np.where(disc[k])):
            r0 = float(per_lag[k][i, j])
            sign = 1.0 if r0 > 0 else -1.0
            ok_a, ra, _na = confirmed_in(E, W, test_a, lag, i, j, sign)
            ok_b, rb, _nb = confirmed_in(E, W, test_b, lag, i, j, sign)
            a_n += ok_a
            b_n += ok_b
            if ok_a and ok_b:
                both.append((lag, int(i), int(j), r0, ra, rb))
    return {"both": both, "a_only_rule": int(a_n), "b_only_rule": int(b_n)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R&D #93b filtered lead-lag graph, two test windows")
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    series, dvol = {}, {}
    for p in sorted(glob.glob(os.path.join(paths.ohlcv_dir(), "*_daily.json"))):
        s = os.path.basename(p)[:-len("_daily.json")]
        if s != "SPY" and not os.path.isdir(os.path.join(paths.symbol_dir(), s)):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                b = pw.bars_from_series(json.load(f).get("Time Series (Daily)", {}))
        except (OSError, ValueError):
            continue
        if b:
            series[s] = (b["days"], b["c"])
            dvol[s] = train_dollar_volume(b["days"], b["c"], b["v"], TEST_A)
    spy_days = [d for d in series["SPY"][0] if d >= START]
    names = sorted(s for s in series if s != "SPY")
    if a.max_symbols:
        names = names[:a.max_symbols]
    syms, R = lg.returns_matrix({s: series[s] for s in names}, spy_days)
    _s, spy_r = lg.returns_matrix({"SPY": series["SPY"]}, spy_days)
    spy_r = spy_r[:, 0]
    cal = np.array(spy_days)
    train, test_a, test_b = cal < TEST_A, (cal >= TEST_A) & (cal < TEST_B), cal >= TEST_B
    lab = lg.labels()
    lead_ok, fol_ok = allowed(syms, dvol, lab)
    E = lg.residualize(R, spy_r, train)
    W = lg.whiten(E, train)
    real = find_edges(E, W, train, test_a, test_b, lead_ok, fol_ok)
    rng = np.random.default_rng(SEED)
    offsets = [int(x) for x in rng.integers(lg.SHIFT[0], lg.SHIFT[1] + 1, lg.PLACEBOS)]
    plac = [find_edges(lg.shift_leaders(E, [train, test_a, test_b], off), W, train, test_a, test_b, lead_ok, fol_ok)
            for off in offsets]
    counts = [len(x["both"]) for x in plac]
    sd_a = np.nanstd(np.where(test_a[:, None], E, np.nan), axis=0)
    sd_b = np.nanstd(np.where(test_b[:, None], E, np.nan), axis=0)
    edges = []
    for lag, i, j, r0, ra, rb in real["both"]:
        li, lj = lab.get(syms[i], {}), lab.get(syms[j], {})
        edges.append({"leader": syms[i], "follower": syms[j], "lag": lag, "rho_train": round(r0, 4),
                      "rho_a": round(ra, 4), "rho_b": round(rb, 4),
                      "size_bp_a": lg.edge_size(E, W, i, j, lag, test_a, float(sd_a[i])),
                      "size_bp_b": lg.edge_size(E, W, i, j, lag, test_b, float(sd_b[i])),
                      "leader_industry": li.get("industry"), "follower_industry": lj.get("industry"),
                      "cross_sector": lg.cross_sector(li, lj), "leader_etf": li.get("etf")})
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "smoke_test": bool(a.max_symbols),
           "symbols": len(syms), "leaders_allowed": int(lead_ok.sum()), "followers_allowed": int(fol_ok.sum()),
           "confirmed_both": len(real["both"]), "confirmed_a_only_rule": real["a_only_rule"],
           "confirmed_b_only_rule": real["b_only_rule"], "placebo_offsets": offsets, "placebo_both": counts,
           "placebo_a_only_rule": [x["a_only_rule"] for x in plac], "placebo_b_only_rule": [x["b_only_rule"] for x in plac],
           "verdict": lg.verdict(len(real["both"]), counts),
           "edges": sorted(edges, key=lambda e: -abs(e["rho_b"]))}
    out = os.path.join(paths.data_dir(), f"leadlag_graph_filtered_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(json.dumps({k: rep[k] for k in ("symbols", "leaders_allowed", "followers_allowed", "confirmed_both",
                                                    "placebo_both", "verdict")} | {"out": out}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
