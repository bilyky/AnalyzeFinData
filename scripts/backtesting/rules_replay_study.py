"""
Rules-version replay: did a change to the scoring rules break the game? (R&D #126, owner 2026-11-24.)
Pre-registered 2026-11-24.

The PROD game beat SPY in June and July 2026 (game +1.7% / +1.4% vs SPY +0.8% / +0.0%), then
trailed in August (+1.4% vs +2.7%) and September (-1.4% vs -0.6%). The scoring code changed
several times in between. This study replays each version of the score formulas on the same data
with the same simulator, so the only difference between runs is the formula.

VERSIONS (aether/scoring.py at each commit; short_score and long_score are taken from it)
  v0721  389c74b  2026-07-21  the package refactor, before the digit-sum term
  v0731  c536a77  2026-07-31  end of July (digit-sum added 07-22, Long60 recalibrated 07-24)
  v0812  4bf2127  2026-08-12  pattern weights made contrarian (candle / chart / momentum x -0.30)
  v0817  7ff9ea2  2026-08-17  candlestick coefficient flipped to +0.30
  v0824  f05e685  2026-08-24  R&D #34 waiver fires on Setup as well as a Bull regime
  now    de16f1d  2026-11-24  today's main
  Every version gets the same inputs: today's backtest_ratings field builder (today's candlestick,
  chart, momentum, RBR, seasonality ... computations). Only the weights that turn fields into
  Short10 / Long60 differ. Check made before writing this: patterns.py applied no candlestick sign
  at any of the five old commits (the sign moved into patterns.py later and back out on
  2026-10-01, 3bb136e), so each old formula reads the raw candlestick tally exactly as it did at
  its own commit. The digit-sum term is 0 in every version (its index is in-sample; R&D #72 and
  the per-stock check judge it).

REPLAY
  The panel: portfolio_backtest._symbol_scores per symbol (2023+, symbols with OHLCV and a Chaikin
  cache), with backtest_ratings' score functions swapped for the version's. The portfolio:
  portfolio_backtest.simulate with today's rules (risk rules on, the live 1% per-trade cap,
  10 bps a side), identical for every version. R&D #62's draws: N_DRAWS = 50, seed 62, a random
  80% of the symbols, a start 0-60 sessions late, split 2025-01-01, each half from fresh equity.

DECISION RULE (risk first, as the owner's bar of 2026-11-24)
  For a pair (A, B) and each half: dd = share of draws where A's max drawdown is no deeper than
  B's; ret = share where A's CAGR is above B's.
  A BETTER_THAN B   dd >= 0.8 and ret >= 0.8 on both halves.
  A WORSE_THAN B    dd <= 0.2 and ret <= 0.2 on both halves.
  REGRESSION_FOUND  some old version is BETTER_THAN now. The culprit steps are the consecutive
                    pairs (v_k, v_k+1) where v_k is BETTER_THAN v_k+1; a step after August
                    (v0824 -> now) is a bundle of later changes and would need its own bisect.
  NO_REGRESSION     no old version is BETTER_THAN now.
  What each verdict does: REGRESSION_FOUND -> propose undoing the culprit step in its own PR (a
  live scoring change, under the risk rules, with a shadow period first); NO_REGRESSION -> the
  formula changes did not break the game; look at gates, exits and data next (feature audit).

REPORTED, NOT JUDGED
  Medians over draws (CAGR, max drawdown, Sharpe, trades) for every version and SPY; each version
  vs SPY on the same dd / ret measures; the full-universe single path.

NOT MEASURED / BIASES
  Gates, exits and the profile logic are today's for every version (a July gate change is not
  replayed); the simulator does not model scale-outs, pyramid adds, the cash-deployment upgrade or
  the 07:00-only stop check; today's symbol list; about 20% of Chaikin snapshots carry the wrong
  date's data (R&D #74), the same for every version; 2023-2026 is one market cycle.

Usage (from a worktree, reading the real caches):
    AETHER_CACHE_DIR=<main>/Data python scripts/backtesting/rules_replay_study.py [--draws 50] [--max-symbols N]
"""
import argparse
import datetime
import glob
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import backtest_ratings as _br
import portfolio_backtest as pb
from aether import instruments
from aether.config import CFG

VERSIONS = (
    ("v0721", "389c74b2a1ce528c369eabd616f15820508bb86b"),
    ("v0731", "c536a77e4a7865584a8950c1f4220ff032622a99"),
    ("v0812", "4bf2127ddaaeb0eecfe3df90c83fd19cd13a6b61"),
    ("v0817", "7ff9ea2cb1ce55976017c1586e0b02377e0fd00b"),
    ("v0824", "f05e6854320e640d50105e22b10203e0f1ce9932"),
    ("now", "de16f1da629d245eb862005fb8a536fae86829f8"),
)
MIN_YEAR = 2023
SPLIT = "2025-01-01"
N_DRAWS = 50
PASS = 0.8
FAIL = 0.2
COST_BPS = 10.0
OUT_DIR = os.path.join(BASE_DIR, "Data")


# ── Versions ──────────────────────────────────────────────────────────────────

def version_source(commit: str) -> str:
    return subprocess.run(["git", "show", f"{commit}:aether/scoring.py"], cwd=BASE_DIR, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def load_version(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _init_worker(path: str, name: str) -> None:
    """Each worker swaps backtest_ratings' score functions for the version's."""
    mod = load_version(path, name)
    _br._short_score_fn = mod.short_score
    _br._long_score_fn = mod.long_score


def build_version_panel(tag, path, symbols, workers, cache_dir) -> dict:
    with open(path, "rb") as f:
        src_hash = hashlib.sha256(f.read()).hexdigest()
    key = hashlib.sha256(f"{src_hash}|{MIN_YEAR}|{','.join(symbols)}".encode()).hexdigest()[:16]
    cache = os.path.join(cache_dir, f"rules_replay_panel_{tag}_{key}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    scores = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(path, f"scoring_{tag}")) as ex:
        for sym, s in ex.map(pb._symbol_scores, [(s, MIN_YEAR) for s in symbols], chunksize=4):
            if s:
                scores[sym] = s
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(scores, f)
    return scores


# ── The verdict ───────────────────────────────────────────────────────────────

def pair_rates(per_draw: list, a: str, b: str) -> dict:
    """Per half: share of draws where a's drawdown is no deeper than b's, and a's CAGR is above b's."""
    out = {}
    for half in ("train", "test"):
        rows = [d[half] for d in per_draw if d[half].get(a) and d[half].get(b)]
        n = len(rows)
        out[half] = {
            "n": n,
            "dd": round(sum(r[a]["max_drawdown_pct"] >= r[b]["max_drawdown_pct"] for r in rows) / n, 3) if n else None,
            "ret": round(sum(r[a]["cagr_pct"] > r[b]["cagr_pct"] for r in rows) / n, 3) if n else None,
        }
    return out


def compare(rates: dict) -> str:
    vals = [rates[h][k] for h in ("train", "test") for k in ("dd", "ret")]
    if None in vals:
        return "INCONCLUSIVE"
    if all(v >= PASS for v in vals):
        return "BETTER_THAN"
    if all(v <= FAIL for v in vals):
        return "WORSE_THAN"
    return "INCONCLUSIVE"


def verdict(per_draw: list, tags: list) -> dict:
    """tags in time order, the last one is today's."""
    now = tags[-1]
    vs_now = {t: compare(pair_rates(per_draw, t, now)) for t in tags[:-1]}
    steps = {f"{a}->{b}": compare(pair_rates(per_draw, a, b)) for a, b in zip(tags, tags[1:])}
    found = [t for t, v in vs_now.items() if v == "BETTER_THAN"]
    return {"label": "REGRESSION_FOUND" if found else "NO_REGRESSION", "older_better_than_now": found,
            "culprit_steps": [s for s, v in steps.items() if v == "BETTER_THAN"], "vs_now": vs_now, "steps": steps}


# ── Driver ────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--draws", type=int, default=N_DRAWS)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--max-symbols", type=int, default=None, help="smoke test on a slice (don't read results)")
    a = ap.parse_args(argv)
    ohlcv = {os.path.basename(f)[:-len("_daily.json")] for f in glob.glob(os.path.join(_br.OHLCV_DIR, "*_daily.json"))}
    cached = {d for d in os.listdir(_br.SYM_DIR) if os.path.isdir(os.path.join(_br.SYM_DIR, d))}
    symbols = sorted(ohlcv & cached)
    if a.max_symbols:
        symbols = sorted(set(symbols[:a.max_symbols]) | {"SPY"})
    tmp = tempfile.mkdtemp(prefix="rules_replay_")
    cache_dir = tmp if a.max_symbols else OUT_DIR
    panels = {}
    for tag, commit in VERSIONS:
        path = os.path.join(tmp, f"scoring_{tag}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(version_source(commit))
        panels[tag] = build_version_panel(tag, path, symbols, a.workers, cache_dir)
        pb._out(f"  panel {tag}: {len(panels[tag])} symbols")
    bars = {s: b for s in symbols if (ts := _br.load_ohlcv(s)) and (b := pb.bars_from_series(ts))}
    first = min(min(v) for v in panels["now"].values() if v)
    calendar = [d for d in sorted(bars["SPY"]["idx"]) if d >= first]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    kw = {"risk_rules": True, "cost_bps": COST_BPS, "excluded": excluded,
          "max_trade_risk_pct": CFG.system_max_trade_risk_pct}
    tags = [t for t, _c in VERSIONS]

    def table(syms, days):
        out = {t: pb.metrics(pb.simulate({s: panels[t][s] for s in syms if s in panels[t]}, bars, days, **kw))
               for t in tags}
        out["spy"] = pb.metrics(pb.buy_and_hold(bars, days, ["SPY"], COST_BPS))
        return out

    pb._out(f"\nRules replay (R&D #126): {len(symbols)} symbols, {calendar[0]} -> {calendar[-1]}, split {SPLIT}, "
            f"{a.draws} draws")
    per_draw = []
    for n, path in enumerate(pb.draw_paths(symbols, calendar, a.draws, split=SPLIT), 1):
        full = [d for d in calendar if d >= path["start"]]
        per_draw.append({"train": table(path["symbols"], [d for d in full if d < SPLIT]),
                         "test": table(path["symbols"], [d for d in full if d >= SPLIT])})
        if n % 10 == 0:
            pb._out(f"  {n}/{a.draws} draws")
    v = verdict(per_draw, tags)
    medians = {h: {t: {k: (float(np.median(x)) if (x := [d[h][t][k] for d in per_draw
                                                          if d[h].get(t) and d[h][t].get(k) is not None]) else None)
                       for k in ("cagr_pct", "max_drawdown_pct", "sharpe", "n_trades")}
                   for t in [*tags, "spy"]} for h in ("train", "test")}
    vs_spy = {t: pair_rates(per_draw, t, "spy") for t in tags}
    single = {t: pb.metrics(pb.simulate(panels[t], bars, calendar, **kw)) for t in tags}
    for h in ("train", "test"):
        pb._out(f"\n  {h}: medians        CAGR%   MaxDD%  Sharpe  trades")
        for t in [*tags, "spy"]:
            m = medians[h][t]
            pb._out(f"  {t:<8}{m['cagr_pct']!s:>16.6}{m['max_drawdown_pct']!s:>9.6}{m['sharpe']!s:>8.5}{m['n_trades']!s:>8.5}")
    pb._out("\n  vs now: " + ", ".join(f"{t} {x}" for t, x in v["vs_now"].items()))
    pb._out("  steps:  " + ", ".join(f"{s} {x}" for s, x in v["steps"].items()))
    pb._out(f"\n  R&D #126 verdict: {v['label']}  culprit steps: {v['culprit_steps'] or '-'}")
    rep = {"run_at": datetime.datetime.now().isoformat(timespec="seconds"), "symbols": len(symbols),
           "calendar": [calendar[0], calendar[-1]], "split": SPLIT, "n_draws": a.draws,
           "smoke_test": bool(a.max_symbols), "versions": dict(VERSIONS), "verdict": v, "medians": medians,
           "pair_rates_vs_now": {t: pair_rates(per_draw, t, "now") for t in tags[:-1]},
           "pair_rates_steps": {f"{x}->{y}": pair_rates(per_draw, x, y) for x, y in zip(tags, tags[1:])},
           "vs_spy": vs_spy, "single_path": single}
    if not a.max_symbols:
        out = os.path.join(OUT_DIR, f"rules_replay_study_{datetime.date.today().isoformat()}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, default=str)
        pb._out(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
