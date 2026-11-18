"""
leadlag_graph_study.py — R&D #93: a lead-lag "dependency graph" over the whole universe. Does one
stock's move today (a "shark") predict another's move over the next 1-5 sessions (its "remoras"),
beyond the market and the follower's own last move — and does that survive out of sample better
than chance?

Raised 2026-11-17 by the owner: detect the remoras that follow our sharks and predict their next
step; build the whole-universe graph, including surprises across sectors (a transport company
that moves after a software one, shared owners or suppliers). Rules fixed here before the full run.

Data: every screen symbol with real, split-adjusted daily bars (position_watch.bars_from_series),
on SPY's trading days. Log returns. Train = sessions before 2020-01-01 (from 2001-01-01), test =
from 2020-01-01. Everything estimated is estimated on train only and applied unchanged to test.

Per symbol (train only): beta to SPY; residual e = r - beta * r_spy; e winsorized at its train
1st/99th percentiles; AR(1) coefficient phi of e. Follower side uses the whitened residual
w_t = e_t - phi * e_(t-1) (removes the follower's own last move); leader side uses e.
Edge statistic, for leader i, follower j (i != j), lag L in 1..5: Pearson correlation between e_i
at day t and w_j at day t+L, both days inside the same period, pairwise complete;
t = rho * sqrt((n - 2) / (1 - rho^2)), normal p.
  Train: n >= 750; Benjamini-Hochberg at q = 0.05 over ALL train tests (every pair and lag).
  Test: a train discovery is CONFIRMED if n >= 500, the test rho has the same sign, and the
  one-sided test p < 0.01.
Placebo (the decisive control for ~1.8M tests): the whole pipeline again with each leader's
series circularly shifted inside each period by one offset of 252-756 sessions (3 replicates,
numpy seed 93). That breaks real links and keeps each series' own statistics.

Decision rule (fixed before the run):
    GRAPH_REAL   confirmed real edges >= 20 AND >= 3x the largest placebo count
    NO_GRAPH     confirmed real edges <= 1.5x the mean placebo count (or zero)
    INCONCLUSIVE otherwise
Reported for confirmed edges (descriptive): lag, train/test rho, industry (snapshot metaInfo) and
SIC division (EDGAR) of both ends, a cross-sector flag, and the test-period size: the follower's
mean whitened residual L days after the leader's +2 sigma days minus after its -2 sigma days (bp).
Nothing is wired; a GRAPH_REAL result proposes a trading test of its own (costs, liquidity).

Not measured: intraday lead-lag (daily bars only), costs, capacity. Biases: today's universe
(survivors), a 2020+ test window that includes the 2020 crash. Ownership and supplier links are
not in the cache; edges are found from prices and explained afterwards.

Usage: AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/leadlag_graph_study.py
"""
import argparse
import datetime
import glob
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import position_watch as pw  # noqa: E402
from aether import paths  # noqa: E402

START, SPLIT = "2001-01-01", "2020-01-01"
LAGS = (1, 2, 3, 4, 5)
MIN_TRAIN, MIN_TEST = 750, 500
Q = 0.05
P_TEST = 0.01
PLACEBOS, SEED, SHIFT = 3, 93, (252, 756)
MIN_EDGES, RATIO_REAL, RATIO_NONE = 20, 3.0, 1.5
SIGMA = 2.0


def returns_matrix(series: dict, cal: list) -> tuple:
    """series {sym: (days, closes)} -> (symbols, T x N log returns on cal, NaN where missing)."""
    pos = {d: k for k, d in enumerate(cal)}
    syms = sorted(series)
    R = np.full((len(cal), len(syms)), np.nan)
    for j, s in enumerate(syms):
        days, c = series[s]
        for a in range(1, len(days)):
            k = pos.get(days[a])
            if k is not None and pos.get(days[a - 1]) == k - 1 and c[a] > 0 and c[a - 1] > 0:
                R[k, j] = math.log(c[a] / c[a - 1])
    return syms, R


def residualize(R: np.ndarray, spy: np.ndarray, train: np.ndarray) -> np.ndarray:
    """Market residuals with train-only beta, winsorized at train 1st/99th percentiles."""
    E = np.full_like(R, np.nan)
    for j in range(R.shape[1]):
        ok = train & ~np.isnan(R[:, j]) & ~np.isnan(spy)
        if ok.sum() < 60:
            continue
        x, y = spy[ok], R[ok, j]
        beta = float(np.cov(x, y)[0, 1] / np.var(x, ddof=1))
        e = R[:, j] - beta * spy
        lo, hi = np.nanpercentile(e[ok], [1, 99])
        E[:, j] = np.clip(e, lo, hi)
    return E


def whiten(E: np.ndarray, train: np.ndarray) -> np.ndarray:
    """w_t = e_t - phi * e_(t-1), phi = train AR(1) coefficient of each column."""
    W = np.full_like(E, np.nan)
    for j in range(E.shape[1]):
        a, b = E[1:, j], E[:-1, j]
        ok = train[1:] & ~np.isnan(a) & ~np.isnan(b)
        if ok.sum() < 60:
            continue
        phi = float(np.dot(a[ok] - a[ok].mean(), b[ok] - b[ok].mean()) / np.dot(b[ok] - b[ok].mean(), b[ok] - b[ok].mean()))
        W[1:, j] = a - phi * b
    return W


def lagged_corr(A: np.ndarray, B: np.ndarray, lag: int, rows: np.ndarray) -> tuple:
    """rho[i, j] = corr(A[t, i], B[t+lag, j]) over t with t and t+lag in `rows`; n[i, j] pairs."""
    keep = rows[:-lag] & rows[lag:]
    a, b = A[:-lag][keep], B[lag:][keep]
    ma, mb = ~np.isnan(a), ~np.isnan(b)
    a0, b0 = np.where(ma, a, 0.0), np.where(mb, b, 0.0)
    fa, fb = ma.astype(float), mb.astype(float)
    n = fa.T @ fb
    with np.errstate(invalid="ignore", divide="ignore"):
        sx, sy = a0.T @ fb, fa.T @ b0
        sxy = a0.T @ b0
        sxx, syy = (a0 * a0).T @ fb, fa.T @ (b0 * b0)
        cov = sxy / n - (sx / n) * (sy / n)
        vx, vy = sxx / n - (sx / n) ** 2, syy / n - (sy / n) ** 2
        rho = cov / np.sqrt(vx * vy)
    np.fill_diagonal(rho, np.nan)
    return rho, n


def p_values(rho: np.ndarray, n: np.ndarray) -> tuple:
    """(two-sided p, one-sided p for rho > 0, one-sided p for rho < 0), normal approximation."""
    with np.errstate(invalid="ignore", divide="ignore"):
        t = rho * np.sqrt((n - 2) / (1 - rho ** 2))
    z = t / math.sqrt(2)
    erfc = np.vectorize(math.erfc, otypes=[float])
    p_pos = 0.5 * erfc(z)
    p_neg = 0.5 * erfc(-z)
    return np.minimum(1.0, 2 * np.minimum(p_pos, p_neg)), p_pos, p_neg


def bh_mask(p: np.ndarray, q: float = Q) -> np.ndarray:
    """Benjamini-Hochberg discoveries over all finite p (any shape)."""
    flat = p.ravel()
    ok = np.isfinite(flat)
    idx = np.where(ok)[0]
    out = np.zeros(flat.shape, bool)
    if not len(idx):
        return out.reshape(p.shape)
    order = idx[np.argsort(flat[idx])]
    m = len(order)
    passed = flat[order] <= q * np.arange(1, m + 1) / m
    if passed.any():
        out[order[:np.max(np.where(passed)[0]) + 1]] = True
    return out.reshape(p.shape)


def find_edges(E: np.ndarray, W: np.ndarray, train: np.ndarray, test: np.ndarray) -> list:
    """Confirmed edges: (lag, leader i, follower j, train rho, test rho, test n)."""
    per_lag, all_p = [], []
    for lag in LAGS:
        rho, n = lagged_corr(E, W, lag, train)
        p, _pp, _pn = p_values(rho, n)
        p[(n < MIN_TRAIN) | ~np.isfinite(rho)] = np.nan
        per_lag.append((rho, n))
        all_p.append(p)
    disc = bh_mask(np.stack(all_p))
    edges = []
    for k, lag in enumerate(LAGS):
        if not disc[k].any():
            continue
        rho_t, n_t = lagged_corr(E, W, lag, test)
        _p, p_pos, p_neg = p_values(rho_t, n_t)
        for i, j in zip(*np.where(disc[k])):
            r0, r1 = per_lag[k][0][i, j], rho_t[i, j]
            if n_t[i, j] < MIN_TEST or not np.isfinite(r1) or np.sign(r1) != np.sign(r0):
                continue
            if (p_pos[i, j] if r0 > 0 else p_neg[i, j]) < P_TEST:
                edges.append((lag, int(i), int(j), float(r0), float(r1), int(n_t[i, j])))
    return edges


def shift_leaders(E: np.ndarray, rows_sets: list, offset: int) -> np.ndarray:
    """Each period's rows circularly shifted by `offset` (breaks links, keeps each series)."""
    S = E.copy()
    for rows in rows_sets:
        idx = np.where(rows)[0]
        if len(idx) > offset:
            S[idx] = np.roll(E[idx], offset, axis=0)
    return S


def verdict(real: int, placebo: list) -> str:
    mx, mean = max(placebo), float(np.mean(placebo))
    if real == 0 or real <= RATIO_NONE * mean:
        return "NO_GRAPH"
    if real >= MIN_EDGES and real >= RATIO_REAL * mx:
        return "GRAPH_REAL"
    return "INCONCLUSIVE"


def edge_size(E: np.ndarray, W: np.ndarray, i: int, j: int, lag: int, test: np.ndarray, sd: float) -> float | None:
    """Follower's mean w at t+lag after the leader's +2 sigma days minus after its -2 sigma days (bp)."""
    keep = test[:-lag] & test[lag:]
    a, b = E[:-lag, i][keep], W[lag:, j][keep]
    ok = ~np.isnan(a) & ~np.isnan(b)
    up, dn = ok & (a > SIGMA * sd), ok & (a < -SIGMA * sd)
    if up.sum() < 5 or dn.sum() < 5:
        return None
    return round(float((b[up].mean() - b[dn].mean()) * 1e4), 1)


def labels() -> dict:
    """{sym: {"industry", "sic_div"}} from the latest snapshot and the EDGAR submissions cache."""
    out = {}
    for d in glob.glob(os.path.join(paths.symbol_dir(), "*")):
        fs = sorted(glob.glob(os.path.join(d, "*.json")))
        if not fs:
            continue
        try:
            with open(fs[-1], encoding="utf-8") as f:
                mi = (json.load(f).get("metaInfo") or [{}])[0]
        except (OSError, ValueError):
            continue
        out[os.path.basename(d)] = {"industry": mi.get("industry_name") or mi.get("etf_group_name") or None,
                                    "etf": bool(str(mi.get("etf_group_name") or "").strip()), "sic_div": None}
    base = os.path.join(paths.cache_dir(), "edgar_cache")
    try:
        with open(os.path.join(base, "company_tickers.json"), encoding="utf-8") as f:
            m = json.load(f)
        rows = m.values() if isinstance(m, dict) else m
        t2c = {str(r["ticker"]).upper(): int(r["cik_str"]) for r in rows if isinstance(r, dict) and "ticker" in r}
    except (OSError, ValueError):
        t2c = {}
    for s, lab in out.items():
        p = os.path.join(base, f"sub_{t2c.get(s)}.json") if t2c.get(s) else None
        if p and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    sic = str(json.load(f).get("sic") or "")
                lab["sic_div"] = sic[:2] if sic else None
            except (OSError, ValueError):
                pass
    return out


def cross_sector(a: dict, b: dict) -> bool | None:
    if a.get("sic_div") and b.get("sic_div"):
        return a["sic_div"] != b["sic_div"]
    if a.get("industry") and b.get("industry"):
        return a["industry"] != b["industry"]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R&D #93 whole-universe lead-lag graph")
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    series = {}
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
    spy_days = [d for d in series["SPY"][0] if d >= START]
    names = sorted(s for s in series if s != "SPY")
    if a.max_symbols:
        names = names[:a.max_symbols]
    syms, R = returns_matrix({s: series[s] for s in names}, spy_days)
    _s, spy_r = returns_matrix({"SPY": series["SPY"]}, spy_days)
    spy_r = spy_r[:, 0]
    cal = np.array(spy_days)
    train, test = cal < SPLIT, cal >= SPLIT
    E = residualize(R, spy_r, train)
    W = whiten(E, train)
    real = find_edges(E, W, train, test)
    rng = np.random.default_rng(SEED)
    offsets = [int(x) for x in rng.integers(SHIFT[0], SHIFT[1] + 1, PLACEBOS)]
    placebo = [len(find_edges(shift_leaders(E, [train, test], off), W, train, test)) for off in offsets]
    lab = labels()
    sds = np.nanstd(np.where(test[:, None], E, np.nan), axis=0)
    edges = []
    for lag, i, j, r0, r1, n1 in real:
        li, lj = lab.get(syms[i], {}), lab.get(syms[j], {})
        edges.append({"leader": syms[i], "follower": syms[j], "lag": lag, "rho_train": round(r0, 4),
                      "rho_test": round(r1, 4), "n_test": n1, "leader_industry": li.get("industry"),
                      "follower_industry": lj.get("industry"), "leader_sic": li.get("sic_div"),
                      "follower_sic": lj.get("sic_div"), "cross_sector": cross_sector(li, lj),
                      "leader_etf": li.get("etf"), "follower_etf": lj.get("etf"),
                      "test_size_bp": edge_size(E, W, i, j, lag, test, float(sds[i]))})
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "smoke_test": bool(a.max_symbols),
           "symbols": len(syms), "sessions": len(spy_days), "train_sessions": int(train.sum()),
           "test_sessions": int(test.sum()), "tests_per_run": len(syms) * (len(syms) - 1) * len(LAGS),
           "confirmed_real": len(real), "placebo_offsets": offsets, "confirmed_placebo": placebo,
           "verdict": verdict(len(real), placebo), "edges": sorted(edges, key=lambda e: -abs(e["rho_test"]))}
    out = os.path.join(paths.data_dir(), f"leadlag_graph_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    os.makedirs(paths.data_dir(), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str)
    sys.stdout.write(json.dumps({k: rep[k] for k in ("symbols", "confirmed_real", "confirmed_placebo", "verdict")}
                                | {"out": out}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
