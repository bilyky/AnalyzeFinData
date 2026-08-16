"""
Proper Levels for Leveraged / Inverse / Crypto Instruments — Phase 1 (Backtest Study).

The level backtest (backtest_levels.py) found the long swing-low/high framework
(risk_utils.detect_support / detect_resistance) works for ~85% of the universe
(median win-rate ~65%) but FAILS on the leveraged / inverse / crypto ETF cohort
(SQQQ, TQQQ, BITO, SOXL, SOXS, …) — those clustered at 33-49% win-rate. As a
temporary stopgap that cohort is excluded from new long BUYs (instruments.is_excluded)
and routed to a generic ATR stop/target (price ± 2.5·ATR). The CLAUDE.md Aug-8 roadmap
asks for a proper direction-aware / volatility-band / mean-reversion algorithm,
BACKTEST-VALIDATED before the exclusion is lifted (the Jul-18 optimizer discipline).

This is Phase 1 ONLY: a standalone study that designs candidate level algorithms and
re-scores the cohort. It touches NO live/production code and does NOT modify
backtest_levels.py — it imports that module's canonical outcome metric (`_evaluate`,
`aggregate`) so win-rate stays a single source of truth, and reimplements only the
short walk-forward loop (with precomputed causal features for speed). Production keeps
the ATR stopgap until — and only if — this study PASSes.

Load-bearing data finding (verified against the cache, not assumed)
-------------------------------------------------------------------
Data/Symbol_full/*_daily.json is raw Alpha-Vantage, UNADJUSTED — no adjusted-close
field. The cohort splits almost yearly (SQQQ: 8 reverse-splits ~4-5× up, e.g.
2025-11-19 15.12→80.92; TQQQ: 8 forward-splits ~0.5× down, e.g. 2021-01-20
197.96→101.44). On unadjusted data, pre-split pivots sit at ¼ or 4× the current
price scale, so detect_support returns absurd levels and detect_resistance finds none
— poisoning the win-rate differently per fund. So SPLIT-ADJUSTMENT is the study's
first step, and "B1 on the adjusted cohort vs the universe" is the opening table: it
quantifies how much of the 33-49% was a data artifact rather than an algorithm failure.
(Production detect_support / resolve_stop_detailed also run on this unadjusted close
today → a live correctness bug flagged for Phase 2, not fixed here.)

Method
------
Split-adjust every series once (open-agreement guard so a real intraday crash is NOT
mistaken for a split). Split the cohort into two DIRECTION-aware sub-cohorts and gate
each separately (an inverse/decay ETF in a secular downtrend has no positive-expectancy
long setup regardless of algorithm; pooling would let TQQQ's bull run mask SQQQ):
    long_levered  — bull-levered + long-crypto + short-vol (trend up in bull regimes)
    inverse_decay — inverse-equity + inverse-commodity + long-vol + inverse-crypto
                    (structurally decay / trend down)

Five level generators, all price+ATR only (volume not needed), each forced through the
same ordering guard `support < price < resistance` (else skip) — the #1 look-ahead
guard, since _evaluate wins ties at day 0 so any target ≤ price is an instant spurious
win (K-MR's target=mid is below price whenever price > its EMA):
    B1    swing baseline (detect_support / detect_resistance) — the method being replaced
    B2    ATR incumbent   price ± 2.5·ATR(14) — what the cohort gets in production NOW
    K     Keltner         mid=EMA(n); support=mid−m·ATR(n), target=mid+m·ATR(n)
    K-MR  Keltner+revert   support=mid−m·ATR(n), target=mid  (the target-nearness TRAP detector)
    CH    Chandelier      support=max(high, N)−m·ATR(N), target=mid+m·ATR(n)

Primary metric = expectancy in R-multiples (E_R): risk=price−support, reward=
resistance−price, RR=reward/risk; per-sample outcome = +RR (target_first), −1
(stop_first), or realized (close_{i+h}−price)/risk (neither — horizon expiry folded
into the economics, not dropped). E_R captures win-rate, target-distance and
decided-rate in one number and can't be gamed by a nearer target. Win-rate (imported
from backtest_levels.aggregate) is reported as the ~65%-parity figure.

Gate at ONE production horizon with NON-OVERLAPPING samples (step ≥ horizon) so n isn't
~4× inflated; horizon 10 is reported as a sensitivity strip only. PASS (per sub-cohort,
all required): candidate = argmax E_R among {K, K-MR, CH}; E_R>0 and ≥E_R(B1)+ε and
≥E_R(B2)+ε; win_rate ≥ universe_ref−TOL; n_decided≥GATE_N; median(RR)≥median(RR of
B2)−δ; decided_rate≥0.5. Verdict: both sub-cohorts PASS → PASS; one → CONDITIONAL
(scope Phase 2 to that sub-cohort); else → FAIL (cohort keeps its ATR stop).

Usage:
    python scripts/backtesting/leveraged_levels_study.py

Writes Data/leveraged_levels_study.json. Recalibrate MONTHLY (same rationale as the
digit_sum / carry studies — the winning combo comes from decades of data and doesn't
move week-to-week). Phase 2 (wiring the winner into risk_utils, fixing the live
unadjusted-close bug, and lifting instruments.is_excluded for a passing sub-cohort) is
deferred and gated on this verdict.

PRODUCTION-DATA NEXT STEPS  (for the agent re-running this with the full cohort)
-------------------------------------------------------------------------------
Phase 1's verdict is FAIL, but it is *thin*: only 6 of 57 cohort tickers are cached
(the screener only fetches its active universe), so the per-sub-cohort n is small and
the verdict is DIRECTIONAL, not final. Before this study can be trusted to gate Phase 2:

  1. Back-fill OHLCV for the 51 missing cohort tickers into Data/Symbol_full/*_daily.json
     via the normal RapidAPI/Alpha-Vantage cache path (same fetch the screener uses for
     its active set — do NOT hand-edit JSON). The missing set (from instruments.py
     _LEVERAGED_INVERSE + _CRYPTO minus what's cached) is:
         BITX BTF DRIP DUST ETHA ETHD ETHE ETHU FAS FAZ FNGD FNGU GBTC JDST JNUG KOLD
         LABD LABU NUGT PSQ QID QLD SCO SDS SH SOXL SOXS SPXL SPXS SPXU SRTY SSO SVIX
         SVXY TECL TECS TMF TMV TNA TZA UCO UPRO URTY UVIX UVXY VIXY WEBL WEBS YANG
         YBTC YINN
  2. Re-run `python scripts/backtesting/leveraged_levels_study.py` (the split-adjuster
     handles the new tickers' frequent splits automatically) and re-read the verdict.
  3. Only THEN consider band methods. Do NOT wire Chandelier despite its high E_R — it
     games the R-multiple (support sits near price → tiny risk → exploding RR at 33%
     win-rate); the win-rate floor already rejects it, and that rejection must stand.
  4. Keep the instruments.is_excluded gate until a sub-cohort actually clears the gate.
     The live split-adjust fix (risk_utils._split_adjust_ohlcv) is ALREADY SHIPPED and
     is independent of this study's verdict — it corrects level/ATR math for every
     symbol, not just this cohort.
"""

import json
import os
import statistics
import sys
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Canonical outcome metric — single source of truth, do NOT re-implement.
from backtest_levels import _evaluate, aggregate

import instruments
import risk_utils
from aether_logger import get_logger as _get_logger


_log = _get_logger("leveraged_levels")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV = BASE_DIR / "Data" / "Symbol_full"
OUT_FILE = BASE_DIR / "Data" / "leveraged_levels_study.json"

# --- walk-forward / gate parameters -----------------------------------------
GATE_HORIZON = 20          # production swing horizon; gate here, report 10 as sensitivity
REPORT_HORIZONS = [10, 20]
STEP = GATE_HORIZON        # non-overlapping samples (no n-inflation) at the gate horizon
START_AFTER = 200          # warm-up: history for EMA/ATR/pivots + post-split settling
MIN_SAMPLES = 15           # a symbol must yield ≥ this many decided B2 samples to contribute
GATE_N = 30                # min pooled decided samples for a sub-cohort verdict

# --- sweep grids ------------------------------------------------------------
SWEEP_M = [2.0, 2.5, 3.0]      # ATR multiple for the band methods
SWEEP_N = [14, 20, 22]         # EMA / ATR / rolling-high window
WINDOWS = sorted(set(SWEEP_N) | {14})   # 14 always needed for the B2 incumbent

# --- gate tolerances --------------------------------------------------------
EPS = 0.05        # E_R must beat both baselines by at least this (R units)
TOL_WR = 5.0      # win-rate may sit up to this many pp below the universe reference
DELTA_RR = 0.10   # candidate median RR must be within this of the B2 incumbent's
DECIDED_RATE_MIN = 0.5   # ≥ half of samples must resolve (target or stop) within horizon

# --- split-detection heuristic ----------------------------------------------
# A day-over-day close ratio outside this band is a split candidate; confirmed only if
# the OPEN moved by ~the same factor (open-agreement guard) — on a real intraday crash
# the open sits near the prior close (r_open≈1) while the close craters, so r_open and
# r_close DISAGREE and the move is preserved. Verified on real bars: SQQQ/TQQQ splits
# show |r_open−r_close|/r_close ≤ 0.12; an SVXY-2018-style crash would show ≈1.0.
SPLIT_HI, SPLIT_LO = 1.8, 0.55
SPLIT_OPEN_AGREE = 0.35

# --- direction-aware sub-cohorts --------------------------------------------
# Structurally-declining products: inverse-equity, inverse-commodity/bond, LONG
# volatility (all decay and move inverse to their underlying), and inverse crypto.
# Everything else in the cohort (bull-levered, long-crypto, SHORT-vol) is long-biased.
_DECAY = frozenset({
    # inverse equity
    "SQQQ", "SPXU", "SPXS", "SDS", "QID", "SH", "PSQ", "TZA", "SRTY", "FAZ",
    "SOXS", "LABD", "TECS", "WEBS", "YANG", "FNGD",
    # inverse commodity / rates
    "DUST", "JDST", "DRIP", "KOLD", "SCO", "TMV",
    # long volatility (structurally decaying, inverse to equity)
    "UVXY", "VIXY", "UVIX",
    # inverse crypto
    "BITI", "ETHD",
})


def _subcohort(sym):
    return "inverse_decay" if sym in _DECAY else "long_levered"


# ---------------------------------------------------------------------------
# Loaders + data hygiene
# ---------------------------------------------------------------------------
def _raw_bars(sym):
    """Load unadjusted daily bars. Returns (dates, o, h, l, c) or None."""
    path = OHLCV / f"{sym}_daily.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except (json.JSONDecodeError, OSError):
        return None
    dates = sorted(ts.keys())
    if len(dates) < START_AFTER + GATE_HORIZON + 2:
        return None
    o = [float(ts[d]["1. open"]) for d in dates]
    h = [float(ts[d]["2. high"]) for d in dates]
    l = [float(ts[d]["3. low"]) for d in dates]
    c = [float(ts[d]["4. close"]) for d in dates]
    return dates, o, h, l, c


def _split_adjust(dates, o, h, l, c):
    """Back-adjust for splits so the series is on one continuous (current) price scale.

    Detects a split between bars i-1 and i when the close ratio r=c[i]/c[i-1] leaves
    [SPLIT_LO, SPLIT_HI] AND the open moved by ~the same factor (open-agreement guard,
    so a real intraday crash — open near prior close, only the close down — is NOT
    adjusted away). All bars strictly before a split are multiplied by its factor
    (compounding across multiple splits). Returns (o2, h2, l2, c2, splits) where splits
    is a list of {date, ratio} for the audit.
    """
    n = len(c)
    if n < 2:
        return o[:], h[:], l[:], c[:], []
    factor = [1.0] * n
    splits = []
    cum = 1.0
    # Walk newest→oldest: factor[i] holds the product of split ratios AFTER bar i;
    # a split detected at i then folds into cum so all older bars pick it up.
    for i in range(n - 1, 0, -1):
        factor[i] = cum
        prev_c = c[i - 1]
        if prev_c <= 0:
            continue
        r_close = c[i] / prev_c
        if r_close >= SPLIT_HI or r_close <= SPLIT_LO:
            r_open = (o[i] / prev_c) if prev_c else r_close
            if abs(r_open - r_close) <= SPLIT_OPEN_AGREE * r_close:
                cum *= r_close
                splits.append({"date": dates[i], "ratio": round(r_close, 3)})
    factor[0] = cum
    o2 = [o[i] * factor[i] for i in range(n)]
    h2 = [h[i] * factor[i] for i in range(n)]
    l2 = [l[i] * factor[i] for i in range(n)]
    c2 = [c[i] * factor[i] for i in range(n)]
    splits.reverse()   # chronological
    return o2, h2, l2, c2, splits


# ---------------------------------------------------------------------------
# Precomputed causal features (each cell uses only bars ≤ its index → no look-ahead)
# ---------------------------------------------------------------------------
def _ema_series(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    e = sum(vals[:n]) / n     # SMA seed
    out[n - 1] = e
    k = 2.0 / (n + 1)
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1.0 - k)
        out[i] = e
    return out


def _atr_series(h, l, c, period):
    """Mean of the last `period` true ranges ending at each bar — matches
    risk_utils._atr_from_series semantics (simple mean of trs[-period:])."""
    n = len(c)
    out = [None] * n
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    for i in range(period, n):          # need `period` TRs: tr[i-period+1 .. i]
        out[i] = sum(tr[i - period + 1:i + 1]) / period
    return out


def _rollhigh_series(h, N):
    n = len(h)
    out = [None] * n
    for i in range(N - 1, n):
        out[i] = max(h[i - N + 1:i + 1])
    return out


def _build_features(h, l, c):
    """EMA / ATR / rolling-high arrays for every swept window, computed once."""
    feats = {"ema": {}, "atr": {}, "rhigh": {}}
    for w in WINDOWS:
        feats["ema"][w] = _ema_series(c, w)
        feats["atr"][w] = _atr_series(h, l, c, w)
        feats["rhigh"][w] = _rollhigh_series(h, w)
    return feats


# ---------------------------------------------------------------------------
# Level generators — return (support, resistance) or (None, None) if the ordering
# guard support < price < resistance fails (skip; kills the day-0 instant-win trap).
# ---------------------------------------------------------------------------
def _guard(support, resistance, price):
    if support is None or resistance is None:
        return None, None
    if not (support < price < resistance):
        return None, None
    return support, resistance


def _levels(method, feats, h, l, c, i, price, m, n):
    if method == "B1":
        s = risk_utils.detect_support(price, l[:i + 1])
        r = risk_utils.detect_resistance(price, h[:i + 1])
        return _guard(s, r, price)
    if method == "B2":
        atr = feats["atr"][14][i]
        if atr is None:
            return None, None
        return _guard(price - 2.5 * atr, price + 2.5 * atr, price)
    mid = feats["ema"][n][i]
    atr = feats["atr"][n][i]
    if mid is None or atr is None:
        return None, None
    if method == "K":
        return _guard(mid - m * atr, mid + m * atr, price)
    if method == "K-MR":
        return _guard(mid - m * atr, mid, price)          # target = mean (revert)
    if method == "CH":
        rh = feats["rhigh"][n][i]
        if rh is None:
            return None, None
        return _guard(rh - m * atr, mid + m * atr, price)
    raise ValueError(f"unknown method {method}")


# ---------------------------------------------------------------------------
# Walk-forward: build levels at each bar, score against the forward window.
# ---------------------------------------------------------------------------
def _walk(feats, h, l, c, method, m, n, horizon=GATE_HORIZON, step=STEP,
          start_after=START_AFTER):
    recs = []
    total = min(len(h), len(l), len(c))
    for i in range(max(start_after, risk_utils.PIVOT_K), total - horizon, step):
        price = c[i]
        if not price or price <= 0:
            continue
        if h[i] == l[i] == c[i]:          # degenerate / synthetic bar
            continue
        support, resistance = _levels(method, feats, h, l, c, i, price, m, n)
        if support is None or resistance is None:
            continue
        rec = _evaluate(support, resistance,
                        l[i + 1:i + 1 + horizon], h[i + 1:i + 1 + horizon])
        rec["price"] = price
        risk = price - support
        reward = resistance - price
        rr = reward / risk
        outcome = rec.get("outcome")
        if outcome == "target_first":
            r_mult = rr
        elif outcome == "stop_first":
            r_mult = -1.0
        else:                              # neither → realized R at horizon expiry
            r_mult = (c[i + horizon] - price) / risk
        rec["rr"] = rr
        rec["r_mult"] = r_mult
        recs.append(rec)
    return recs


def _metrics(recs):
    """E_R + decided-rate + median RR (local), win-rate (imported aggregate)."""
    agg = aggregate(recs)
    o = agg.get("outcome") or {}
    both = [r for r in recs if "outcome" in r]
    decided = o.get("target_first", 0) + o.get("stop_first", 0)
    rms = [r["r_mult"] for r in both]
    rrs = [r["rr"] for r in both]
    return {
        "n_both": len(both),
        "n_decided": decided,
        "e_r": round(sum(rms) / len(rms), 3) if rms else None,
        "win_rate": o.get("win_rate"),
        "median_rr": round(statistics.median(rrs), 2) if rrs else None,
        "decided_rate": round(decided / len(both), 3) if both else None,
    }


# ---------------------------------------------------------------------------
# Per-method best combo over a set of already-loaded symbols (one sub-cohort).
# ---------------------------------------------------------------------------
def _best_combo(sym_data, method, horizon=GATE_HORIZON):
    """Pool walk-forward records across a sub-cohort and pick the (m, n) combo with
    the highest E_R (subject to GATE_N decided samples; else the most-populated combo
    for honest reporting). Baselines B1/B2 have a single degenerate combo."""
    combos = ([(0.0, 0)] if method in ("B1", "B2")
              else [(m, n) for m in SWEEP_M for n in SWEEP_N])
    scored = []
    for m, n in combos:
        pooled = []
        per_symbol = {}
        for sym, d in sym_data.items():
            recs = _walk(d["feats"], d["h"], d["l"], d["c"], method, m, n, horizon)
            pooled.extend(recs)
            met = _metrics(recs)
            per_symbol[sym] = met["win_rate"]
        met = _metrics(pooled)
        met["m"], met["n"] = m, n
        met["per_symbol_win"] = per_symbol
        scored.append(met)
    eligible = [s for s in scored if s["n_decided"] >= GATE_N and s["e_r"] is not None]
    pool = eligible if eligible else scored
    return max(pool, key=lambda s: (s["e_r"] if s["e_r"] is not None else -9.0))


# ---------------------------------------------------------------------------
# Gate one sub-cohort.
# ---------------------------------------------------------------------------
def _gate(by_method, universe_ref):
    b1, b2 = by_method["B1"], by_method["B2"]
    cands = {k: by_method[k] for k in ("K", "K-MR", "CH")}
    best_name, v = max(cands.items(),
                       key=lambda kv: (kv[1]["e_r"] if kv[1]["e_r"] is not None else -9.0))
    reasons = []

    def need(cond, why):
        if not cond:
            reasons.append(why)

    need(v["e_r"] is not None and v["e_r"] > 0, "E_R<=0")
    need(v["e_r"] is not None and b1["e_r"] is not None and v["e_r"] >= b1["e_r"] + EPS,
         f"E_R not >= B1+{EPS}")
    need(v["e_r"] is not None and b2["e_r"] is not None and v["e_r"] >= b2["e_r"] + EPS,
         f"E_R not >= B2(incumbent)+{EPS}")
    need(v["win_rate"] is not None and v["win_rate"] >= universe_ref - TOL_WR,
         f"win_rate < universe_ref-{TOL_WR} ({universe_ref - TOL_WR:.1f})")
    need(v["n_decided"] >= GATE_N, f"n_decided < {GATE_N}")
    need(b2["median_rr"] is not None and v["median_rr"] is not None
         and v["median_rr"] >= b2["median_rr"] - DELTA_RR, "median_RR < B2-δ")
    need(v["decided_rate"] is not None and v["decided_rate"] >= DECIDED_RATE_MIN,
         f"decided_rate < {DECIDED_RATE_MIN}")

    return {
        "pass": not reasons,
        "best_method": best_name,
        "best_combo": {"m": v["m"], "n": v["n"]},
        "e_r": v["e_r"], "win_rate": v["win_rate"], "n_decided": v["n_decided"],
        "median_rr": v["median_rr"], "decided_rate": v["decided_rate"],
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _load_symbols(symbols):
    """Split-adjust + precompute features for each symbol; drop thin histories."""
    out, splits_audit, thin = {}, {}, []
    for sym in symbols:
        raw = _raw_bars(sym)
        if not raw:
            continue
        dates, o, h, l, c = raw
        o, h, l, c, splits = _split_adjust(dates, o, h, l, c)
        feats = _build_features(h, l, c)
        # Sample-count gate under B2 (always produces ordered levels).
        b2 = _walk(feats, h, l, c, "B2", 0.0, 0)
        decided = sum(1 for r in b2 if r.get("outcome") in ("target_first", "stop_first"))
        if decided < MIN_SAMPLES:
            thin.append({"symbol": sym, "decided": decided})
            continue
        out[sym] = {"h": h, "l": l, "c": c, "feats": feats}
        if splits:
            splits_audit[sym] = splits
    return out, splits_audit, thin


def _universe_reference(normal_syms):
    """Median per-symbol B1 win-rate over the NORMAL universe, computed the same
    adjusted / guarded / non-overlapping way — the ~65% parity target."""
    sym_data, _, _ = _load_symbols(normal_syms)
    wins = []
    for _sym, d in sym_data.items():
        recs = _walk(d["feats"], d["h"], d["l"], d["c"], "B1", 0.0, 0)
        wr = _metrics(recs)["win_rate"]
        if wr is not None:
            wins.append(wr)
    ref = round(statistics.median(wins), 1) if wins else 0.0
    return ref, len(wins)


def run():
    all_syms = sorted(p.name[:-11] for p in OHLCV.glob("*_daily.json"))
    cohort = [s for s in all_syms if instruments.is_excluded(s)]
    normals = [s for s in all_syms if not instruments.is_excluded(s)]
    _log.console(f"Universe: {len(all_syms)} cached symbols — "
                 f"{len(cohort)} excluded-cohort, {len(normals)} normal.\n")

    _log.console("Computing universe reference (B1 win-rate over normals, adjusted)...")
    universe_ref, ref_n = _universe_reference(normals)
    _log.console(f"  universe_ref (median per-symbol B1 win-rate, N={ref_n}) = "
                 f"{universe_ref:.1f}%\n")

    sym_data, splits_audit, thin = _load_symbols(cohort)
    _log.console(f"Cohort loaded: {len(sym_data)} symbols with >= {MIN_SAMPLES} decided "
                 f"samples; {len(thin)} dropped as thin-history.")
    _log.console(f"Split-adjustments applied to {len(splits_audit)} symbols "
                 f"({sum(len(v) for v in splits_audit.values())} splits total).\n")

    methods = ["B1", "B2", "K", "K-MR", "CH"]
    sub_names = ["long_levered", "inverse_decay"]
    sub_syms = {sn: {s: d for s, d in sym_data.items() if _subcohort(s) == sn}
                for sn in sub_names}

    results = {}          # sub -> {method -> best-combo metrics}
    gates = {}
    for sn in sub_names:
        data = sub_syms[sn]
        _log.console("=" * 92)
        _log.console(f"SUB-COHORT: {sn}  ({len(data)} symbols: "
                     f"{', '.join(sorted(data)) or '—'})")
        _log.console("=" * 92)
        if not data:
            _log.console("  (no symbols) — skipping.\n")
            results[sn] = {}
            gates[sn] = {"pass": False, "reasons": ["no symbols"], "best_method": None}
            continue
        by_method = {mth: _best_combo(data, mth) for mth in methods}
        results[sn] = by_method
        _log.console(f"  {'method':<7}{'combo':>10}{'E_R':>9}{'win%':>8}"
                     f"{'med_RR':>9}{'decided':>9}{'n_dec':>8}")
        for mth in methods:
            v = by_method[mth]
            combo = "-" if mth in ("B1", "B2") else f"m{v['m']}/n{v['n']}"
            _log.console(f"  {mth:<7}{combo:>10}{_fmt(v['e_r']):>9}"
                         f"{_fmt(v['win_rate']):>8}{_fmt(v['median_rr']):>9}"
                         f"{_fmt(v['decided_rate']):>9}{v['n_decided']:>8}")
        g = _gate(by_method, universe_ref)
        gates[sn] = g
        verdict = "PASS" if g["pass"] else "FAIL"
        _log.console(f"\n  GATE: {verdict}  — best candidate {g['best_method']} "
                     f"(combo m{g['best_combo']['m']}/n{g['best_combo']['n']}), "
                     f"E_R={_fmt(g['e_r'])}, win={_fmt(g['win_rate'])}%, "
                     f"n_decided={g['n_decided']}")
        if g["reasons"]:
            _log.console(f"        blocked by: {'; '.join(g['reasons'])}")
        _log.console("")

    # Horizon-10 sensitivity strip (best-combo candidate per sub-cohort, reported only).
    sens = {}
    for sn in sub_names:
        data = sub_syms[sn]
        g = gates[sn]
        if not data or not g.get("best_method"):
            continue
        mth = g["best_method"]
        m, n = g["best_combo"]["m"], g["best_combo"]["n"]
        pooled = []
        for _sym, d in data.items():
            pooled.extend(_walk(d["feats"], d["h"], d["l"], d["c"], mth, m, n, horizon=10))
        sens[sn] = _metrics(pooled)

    # Overall verdict.
    passes = [sn for sn in sub_names if gates[sn]["pass"]]
    if len(passes) == 2:
        verdict, note = "PASS", "candidate beats the ATR incumbent on both sub-cohorts"
    elif len(passes) == 1:
        verdict = "CONDITIONAL"
        note = f"scope Phase 2 to sub-cohort: {passes[0]}"
    else:
        verdict, note = "FAIL", "cohort keeps its ATR stop; no candidate cleared the gate"

    _log.console("=" * 92)
    _log.console(f"VERDICT: {verdict} — {note}")
    _log.console("=" * 92)
    _log.console(f"Opening finding: adjusted B1 win-rate vs universe_ref {universe_ref:.1f}% "
                 "quantifies the split-artifact share of the original 33-49%.")
    for sn in sub_names:
        b1 = results[sn].get("B1", {})
        if b1:
            _log.console(f"  {sn:<14} adjusted-B1 win={_fmt(b1.get('win_rate'))}% "
                         f"(E_R={_fmt(b1.get('e_r'))}, n_dec={b1.get('n_decided')})")

    payload = {
        "meta": {
            "gate_horizon": GATE_HORIZON, "step": STEP, "start_after": START_AFTER,
            "sweep_m": SWEEP_M, "sweep_n": SWEEP_N,
            "gate_n": GATE_N, "min_samples": MIN_SAMPLES,
            "eps": EPS, "tol_wr": TOL_WR, "delta_rr": DELTA_RR,
            "decided_rate_min": DECIDED_RATE_MIN,
            "universe_ref": universe_ref, "universe_ref_n": ref_n,
            "verdict": verdict, "note": note,
            "sub_cohorts": {sn: sorted(sub_syms[sn]) for sn in sub_names},
            "thin_history_excluded": thin,
        },
        "gates": gates,
        "methods": results,
        "sensitivity_h10": sens,
        "split_adjustments": splits_audit,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    _log.console(f"\nSaved study to {OUT_FILE}")
    return payload


def _fmt(x):
    if isinstance(x, bool) or x is None:
        return "  -  "
    if isinstance(x, (int, float)):
        return f"{x:+.2f}" if abs(x) < 100 else f"{x:.1f}"
    return str(x)


if __name__ == "__main__":
    run()
