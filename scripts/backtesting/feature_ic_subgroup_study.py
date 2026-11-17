"""
Feature IC by subgroup (R&D #68 phase 1): which inputs order the next 20 sessions' returns, overall
and inside subgroups of stocks (sector, size) or market states (regime)? Measurement only.

Our scores barely order returns (rank IC ~0.01-0.03, R&D #60); more names don't rescue them
(#83). One idea is that the inputs carry signal only in some groups and the universe-wide average
hides it (#34). This measures that before anyone fits a weight: no coefficients are fitted, nothing
is wired.

Panel: every (symbol, day) from the Chaikin snapshots 2023+ with a matching OHLCV bar (the
rule_walkforward / setup_gate rules: snapshot price within 10% of the close, 60 bars of history, 20
bars ahead). Inputs known at the day's close:
  s10, l60, br            backtest_ratings.compute_br (the live score rebuild)
  pgr                     backtest_ratings.extract_pgr_corr (1..5)
  money_flow, lt_trend, industry_str, rel_strength   Chaikin checklist: Strong +1, Neutral 0, Weak -1
  obos                    Chaikin overbought/oversold: Optimal +2, Early +1, Neutral 0, Wait -1 (the
                          app's own reading of entry quality; an ordinal assumption, stated)
  mom60, dist_sma20, ret3 price: 60-session return, close vs the 20-session mean, 3-session return,
                          from split-adjusted closes up to the day (price_features)
Outcome: the 20-session return from the NEXT session's close (c[j+1] -> c[j+21]), so it shares no
price with any input. Within a date every stock shares SPY's move, so ranks of the raw return equal
ranks of the excess return. (Changed after the first run, on review: that run used the snapshot
price as both the inputs' end and the outcome's start, which builds in a negative IC; disclosed in
the roadmap with both results.)

Groups: ALL; sector (GICS industry -> sector, standard GICS 2023 map below; ETFs and unmapped names
dropped); regime (SPY close above / below its 200-session mean that day); size (market-cap terciles
that day).

Statistic: for each (input, group) and each date with >= MIN_NAMES names in the group, the
Spearman IC across those names; the mean IC over dates; Newey-West t, lag 19 (daily samples of a
20-session return). Train = dates before SPLIT (2025-01-01); test = SPLIT on.

Decision rule (fixed before the first run):
  1. Discovery, train only: two-sided p from the HAC t (normal approximation) for every (input,
     group) with >= MIN_DATES dates; Benjamini-Hochberg at Q (0.10) across all of them.
  2. Confirmed: discovered AND the test mean IC has the same sign AND |test HAC t| >= 1.96.
  3. Subgroup-specific: a confirmed (input, sector/size/regime) whose ALL-stocks version is not
     confirmed, or whose sign is opposite to ALL's.
  Verdict: SIGNAL_FOUND (list the confirmed pairs) / NO_CONFIRMED_SIGNAL.
  A confirmation is a reason for a weighting study in the portfolio backtest (#58/#62 draws, under
  #67), never a weight change from here.

Not measured: costs, turnover, portfolio construction, interactions between inputs. Biases: today's
universe (survivorship), today's industry labels, snapshots after the #74 quarantine.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/feature_ic_subgroup_study.py [--workers 8]
"""
import argparse
import datetime
import glob
import json
import math
import multiprocessing
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitoring"))

import backtest_ratings as br  # noqa: E402
import position_watch as pw  # noqa: E402
import setup_s10_gate_study as sg  # noqa: E402
from aether import paths  # noqa: E402

FEATURES = ("s10", "l60", "br", "pgr", "money_flow", "lt_trend", "industry_str", "rel_strength", "obos",
            "mom60", "dist_sma20", "ret3")
MIN_NAMES = 10
MIN_DATES = 60
LAG = 19
SPLIT = "2025-01-01"
Q = 0.10
GATE_T = 1.96
MIN_YEAR = "2023"
LEVEL = {"Strong": 1.0, "Neutral": 0.0, "Weak": -1.0}
OBOS = {"Optimal": 2.0, "Early": 1.0, "Neutral": 0.0, "Wait": -1.0}

GICS = {
    "Energy": ["Energy Equipment & Services", "Oil, Gas & Consumable Fuels"],
    "Materials": ["Chemicals", "Construction Materials", "Containers & Packaging", "Metals & Mining",
                  "Paper & Forest Products"],
    "Industrials": ["Aerospace & Defense", "Building Products", "Construction & Engineering", "Electrical Equipment",
                    "Industrial Conglomerates", "Machinery", "Trading Companies & Distributors",
                    "Commercial Services & Supplies", "Professional Services", "Air Freight & Logistics",
                    "Passenger Airlines", "Marine Transportation", "Ground Transportation",
                    "Transportation Infrastructure"],
    "Consumer Discretionary": ["Automobile Components", "Automobiles", "Household Durables", "Leisure Products",
                               "Textiles, Apparel & Luxury Goods", "Hotels, Restaurants & Leisure",
                               "Diversified Consumer Services", "Distributors", "Broadline Retail", "Specialty Retail"],
    "Consumer Staples": ["Consumer Staples Distribution & Retail", "Beverages", "Food Products", "Tobacco",
                         "Household Products", "Personal Care Products"],
    "Health Care": ["Health Care Equipment & Supplies", "Health Care Providers & Services", "Health Care Technology",
                    "Biotechnology", "Pharmaceuticals", "Life Sciences Tools & Services"],
    "Financials": ["Banks", "Financial Services", "Consumer Finance", "Capital Markets", "Mortgage Real Estate Investment Trusts (REITs)",
                   "Insurance"],
    "Information Technology": ["IT Services", "Software", "Communications Equipment",
                               "Technology Hardware, Storage & Peripherals",
                               "Electronic Equipment, Instruments & Components",
                               "Semiconductors & Semiconductor Equipment"],
    "Communication Services": ["Diversified Telecommunication Services", "Wireless Telecommunication Services", "Media",
                               "Entertainment", "Interactive Media & Services"],
    "Utilities": ["Electric Utilities", "Gas Utilities", "Multi-Utilities", "Water Utilities",
                  "Independent Power & Renewable Electricity Producers"],
    "Real Estate": ["Residential REITs", "Specialized REITs", "Real Estate Management & Development", "Office REITs",
                    "Industrial REITs", "Retail REITs", "Diversified REITs", "Health Care REITs", "Hotel & Resort REITs"],
}
SECTOR_OF = {ind: sec for sec, inds in GICS.items() for ind in inds}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def price_features(c: list, j: int) -> dict | None:
    """Price inputs and the outcome from split-adjusted closes `c` around day j.

    Inputs use closes up to and including day j: mom60 = c[j]/c[j-60] - 1, dist_sma20 = c[j] / the
    mean of the 20 closes before j - 1, ret3 = c[j]/c[j-3] - 1. The outcome starts at the NEXT close:
    f20 = c[j+21]/c[j+1] - 1, 20 sessions that share no price with the inputs. (The first version
    used the snapshot's own price at both ends, so any error in it raised one side and lowered the
    other: a built-in negative IC. Review of #263.)"""
    if j < 60 or j + 21 >= len(c) or min(c[j - 60], c[j - 3], c[j], c[j + 1]) <= 0:
        return None
    sma20 = sum(c[j - 20:j]) / 20
    return {"mom60": c[j] / c[j - 60] - 1, "dist_sma20": c[j] / sma20 - 1 if sma20 > 0 else None,
            "ret3": c[j] / c[j - 3] - 1, "f20": c[j + 21] / c[j + 1] - 1}


def _symbol_rows(job):
    sym, sym_dir, ohlcv_dir = job
    ts = sg._load(ohlcv_dir, sym)
    if not ts:
        return []
    dates = sorted(ts)
    pos = {d: i for i, d in enumerate(dates)}
    seas = br.precompute_seasonality(ts)
    adj = pw.bars_from_series(ts)            # split-adjusted, placeholder bars dropped
    if not adj:
        return []
    rows, prev = [], None
    for path in sorted(glob.glob(os.path.join(sym_dir, sym, f"{sym}_*.json"))):
        day = Path(path).stem[len(sym) + 1:]
        if day < MIN_YEAR or day not in pos:
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
        sector = SECTOR_OF.get(str(meta.get("industry_name") or "").strip())
        price = _num(meta.get("Last") or cl.get("lastPrice")) or 0.0
        i = pos[day]
        close = float(ts[day].get("4. close", 0))
        pf = price_features(adj["c"], adj["idx"][day]) if day in adj["idx"] else None
        if (sector is None or price <= 0 or i < 60 or pf is None
                or (close > 0 and abs(price - close) / close > 0.1)):
            prev = data
            continue
        r = br.compute_br(data, prev, price, i, dates, ts, seas)
        rows.append({
            "day": day, "sym": sym, "sector": sector, "mcap": _num(meta.get("marketCap")),
            "s10": r[1], "l60": r[2], "br": r[0], "pgr": float(br.extract_pgr_corr(data)),
            "money_flow": LEVEL.get(str(cl.get("moneyFlow") or "").strip()),
            "lt_trend": LEVEL.get(str(cl.get("ltTrend") or "").strip()),
            "industry_str": LEVEL.get(str(cl.get("industry") or "").strip()),
            "rel_strength": LEVEL.get(str(cl.get("relativeStrength") or "").strip()),
            "obos": OBOS.get(str(cl.get("overboughtOversold") or "").strip()),
            **pf,
        })
        prev = data
    return rows


def build_panel(sym_dir, ohlcv_dir, symbols, workers):
    rows = []
    with multiprocessing.Pool(max(1, workers)) as pool:
        for n, part in enumerate(pool.imap_unordered(_symbol_rows, [(s, sym_dir, ohlcv_dir) for s in symbols],
                                                     chunksize=4), 1):
            rows.extend(part)
            if n % 100 == 0 or n == len(symbols):
                sys.stderr.write(f"  {n}/{len(symbols)} symbols, {len(rows)} rows\n")
    return pd.DataFrame(rows)


def add_groups(df: pd.DataFrame, spy: dict) -> pd.DataFrame:
    """regime: SPY close above its 200-session mean that day; size: market-cap tercile that day."""
    days = sorted(spy)
    c = np.array([float(spy[d]["4. close"]) for d in days])
    above = {d: (i >= 199 and c[i] > c[i - 199:i + 1].mean()) for i, d in enumerate(days)}
    df = df.copy()
    df["regime"] = df["day"].map(lambda d: "SPY>200d" if above.get(d) else "SPY<200d")
    df["size"] = df.groupby("day")["mcap"].transform(
        lambda x: pd.qcut(x.rank(method="first"), 3, labels=["small", "mid", "large"]) if x.notna().sum() >= 3 else None)
    return df


def daily_ic(df: pd.DataFrame, feature: str, group_col: str | None) -> pd.DataFrame:
    """Per (day, group): Spearman IC of `feature` vs f20 among names with both, if >= MIN_NAMES."""
    keys = ["day"] + ([group_col] if group_col else [])
    d = df[keys + [feature, "f20"]].dropna()
    g = d.groupby(keys, observed=True)
    n = g[feature].transform("size")
    d = d[n >= MIN_NAMES]
    g = d.groupby(keys, observed=True)
    rx = g[feature].rank(method="average")
    ry = g["f20"].rank(method="average")
    d = d.assign(rx=rx, ry=ry)
    g = d.groupby(keys, observed=True)
    mx, my = g["rx"].transform("mean"), g["ry"].transform("mean")
    d = d.assign(cxy=(d.rx - mx) * (d.ry - my), cxx=(d.rx - mx) ** 2, cyy=(d.ry - my) ** 2)
    agg = d.groupby(keys, observed=True)[["cxy", "cxx", "cyy"]].sum()
    denom = np.sqrt(agg.cxx * agg.cyy)
    agg["ic"] = np.where(denom > 0, agg.cxy / denom.replace(0, np.nan), np.nan)
    return agg.dropna(subset=["ic"]).reset_index()


def summarize_ic(ics: list) -> dict:
    t = sg._t_hac(ics, LAG) if len(ics) >= LAG + 3 else None
    return {"dates": len(ics), "mean_ic": round(float(np.mean(ics)), 4) if ics else None,
            "t_hac": round(t, 2) if t is not None else None}


def p_two_sided(t):
    return math.erfc(abs(t) / math.sqrt(2)) if t is not None else None


def benjamini_hochberg(pvals: dict, q: float = Q) -> set:
    """Keys of the discoveries at FDR q (pvals: {key: p})."""
    items = sorted(((p, k) for k, p in pvals.items() if p is not None), key=lambda x: x[0])
    m, cut = len(items), -1
    for rank, (p, _k) in enumerate(items, 1):
        if p <= q * rank / m:
            cut = rank
    return {k for _p, k in items[:cut]} if cut > 0 else set()


def evaluate(results: dict) -> dict:
    """results: {(feature, group_kind, group): {"train": summary, "test": summary}} -> verdict."""
    eligible = {k: v["train"] for k, v in results.items() if v["train"]["dates"] >= MIN_DATES}
    disc = benjamini_hochberg({k: p_two_sided(s["t_hac"]) for k, s in eligible.items()})
    confirmed = []
    for k in sorted(disc):
        tr, te = results[k]["train"], results[k]["test"]
        # a HAC t carries its mean's sign, so the t's sign is the test IC's sign
        if te["t_hac"] is not None and abs(te["t_hac"]) >= GATE_T and np.sign(te["t_hac"]) == np.sign(tr["mean_ic"]):
            confirmed.append(k)
    conf_set = set(confirmed)
    specific = []
    for f, kind, grp in confirmed:
        overall = results.get((f, "ALL", "ALL"))
        if (f, "ALL", "ALL") not in conf_set or (overall and np.sign(overall["train"]["mean_ic"]) !=
                                                 np.sign(results[(f, kind, grp)]["train"]["mean_ic"])):
            specific.append((f, kind, grp))
    return {"tests": len(eligible), "discovered": sorted(disc), "confirmed": confirmed, "subgroup_specific": specific,
            "verdict": "SIGNAL_FOUND" if confirmed else "NO_CONFIRMED_SIGNAL"}


def run(df: pd.DataFrame) -> dict:
    results = {}
    for f in FEATURES:
        for kind, col in (("ALL", None), ("sector", "sector"), ("regime", "regime"), ("size", "size")):
            ic = daily_ic(df, f, col)
            groups = [("ALL", ic)] if col is None else [(g, sub) for g, sub in ic.groupby(col, observed=True)]
            for grp, sub in groups:
                sub = sub.sort_values("day")
                results[(f, kind, str(grp))] = {
                    "train": summarize_ic(sub.loc[sub.day < SPLIT, "ic"].tolist()),
                    "test": summarize_ic(sub.loc[sub.day >= SPLIT, "ic"].tolist())}
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    sym_dir, ohlcv_dir = paths.symbol_dir(), paths.ohlcv_dir()
    ohlcv = {os.path.basename(p)[:-len("_daily.json")] for p in glob.glob(os.path.join(ohlcv_dir, "*_daily.json"))}
    symbols = sorted(ohlcv & {d for d in os.listdir(sym_dir) if os.path.isdir(os.path.join(sym_dir, d))})
    if a.max_symbols:
        symbols = symbols[:a.max_symbols]
    df = add_groups(build_panel(sym_dir, ohlcv_dir, symbols, a.workers), sg._load(ohlcv_dir, "SPY"))
    results = run(df)
    ev = evaluate(results)
    rep = {"run": datetime.datetime.now().isoformat(timespec="seconds"), "rows": len(df), "symbols": df.sym.nunique(),
           "first": df.day.min(), "last": df.day.max(), "split": SPLIT, "features": list(FEATURES),
           "group_sizes": {k: df[k].value_counts().to_dict() for k in ("sector", "regime", "size")},
           "evaluation": {k: ([list(x) for x in v] if isinstance(v, list) else v) for k, v in ev.items()},
           "results": {"|".join(k): v for k, v in results.items()}, "smoke_test": bool(a.max_symbols)}
    out = os.path.join(paths.data_dir(), f"feature_ic_subgroup_study_{datetime.datetime.now():%Y-%m-%d_%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=str)
    sys.stdout.write(f"rows {len(df)}, symbols {df.sym.nunique()}, {df.day.min()}..{df.day.max()}\n")
    sys.stdout.write("ALL-stocks IC (train | test):\n")
    for f in FEATURES:
        r = results.get((f, "ALL", "ALL"))
        if r:
            sys.stdout.write(f"  {f:<13} {r['train']['mean_ic']!s:>8} (t {r['train']['t_hac']!s:>6}) | "
                             f"{r['test']['mean_ic']!s:>8} (t {r['test']['t_hac']!s:>6})\n")
    sys.stdout.write("evaluation: " + json.dumps({k: (v if not isinstance(v, list) else [list(x) for x in v])
                                                   for k, v in ev.items()}) + f"\nwrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
