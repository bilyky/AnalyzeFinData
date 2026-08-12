"""
Carry-Unwind Risk-Off Gate Study — "Yen / TLT / SPY self-decaying regime gate".

Backtest-first discovery (Jul-18 optimizer discipline) for a defensive market-regime
gate built on the yen carry trade. Weak yen (FXY down) funds risk-on flows into US
equities; a sudden yen SPIKE (carry unwind) forces deleveraging -> money flees to
long Treasuries (TLT) and SPY sells off. The canonical live example is the Aug-5-2024
unwind (BOJ hike -> yen rip -> global equity air-pocket -> Treasury bid).

The core WORRY this study answers empirically: the relationship HAS A SHELF LIFE.
The BOJ is normalizing (the carry channel decays), and the stock-bond correlation
already flipped positive in 2022. So we must NOT hardcode "yen up -> sell stocks".
Instead the gate is SELF-DECAYING: it only acts while the FXY<->SPY relationship is
demonstrably intact, measured by a rolling correlation. This study proves whether
that conditioning adds value and CALIBRATES it, before any live code is touched.

Everything below uses PURE PRICE, date-aligned across the macro basket, with NO
LOOK-AHEAD: the state (trend scores) and the correlation guard use only bars <= i;
forward SPY returns index strictly future bars, used ONLY to grade the signal.

Signal (evaluated at each aligned day i using only bars[:i+1]):
    State (carry-unwind): FXY trend RISING and TLT trend RISING and SPY trend FALLING
                          -- yen + bonds bid while equities weaken. "Trend" is the
                          same SMA-stack score the live gate uses (see _trend_score).
    Guard (condition B):  rolling corr(FXY_returns, SPY_returns) over CORR_WIN bars
                          is sufficiently negative (yen and stocks genuinely moving
                          opposite LATELY). This is the self-decaying part -- when the
                          carry trade fades and corr drifts toward 0, the guard fails
                          and the gate goes silent on its own.

Because this is a DEFENSIVE gate, "good" = the SPY forward return after the signal is
significantly BELOW the all-days baseline (a negative spread = the gate correctly
flagged danger). This is the opposite sign convention from the RBR study.

Three questions:
    1. Does the STATE+GUARD predict SPY weakness more reliably than the STATE alone?
       (validates condition B -- the shelf-life guard actually adds value)
    2. Era-sliced, does the protective edge concentrate in the 2022 shock and the
       2024 carry-unwind windows and decay elsewhere? (shelf-life made visible)
    3. What is the |corr| -> downgrade-magnitude curve for the confidence-scaled
       live gate? (Table C calibration)

Usage:
    python scripts/backtesting/carry_unwind_study.py

Writes Data/carry_unwind_study.json and prints Table A (conditioning showdown),
Table B (era slice), Table C (confidence-scaling calibration), the parameter sweep,
and the alpha-gate verdict.

Cadence -- recalibrate MONTHLY (like digit_sum_study / pullback_recovery_study), NOT
weekly. The regime relationship is derived from 10+ years of macro data and does not
shift week-to-week; a weekly re-derivation only burns compute and overfits. Re-run
after the monthly OHLCV top-up; if the gate flips or the best corr threshold moves
materially, update the Phase-2 gate constants and re-run the tests.

Phase 2 (wiring the confidence-scaled gate into ai_portfolio_game.get_market_regime)
proceeds ONLY if the alpha gate below PASSes.
"""

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _study_utils import WINDOWS
from aether_logger import get_logger as _get_logger

_log = _get_logger("carry_unwind")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV    = BASE_DIR / "Data" / "Symbol_full"
OUT_FILE = BASE_DIR / "Data" / "carry_unwind_study.json"

# ── Macro basket ──────────────────────────────────────────────────────────────
YEN   = "FXY"   # CurrencyShares Japanese Yen Trust -- yen strengthens => FXY up
BONDS = "TLT"   # iShares 20+yr Treasuries -- risk-off flight-to-safety bid
MKT   = "SPY"   # S&P 500 -- the risk asset we are protecting

# ── State detection (SMA-stack trend score, same primitive as the live gate) ──
# The score is in [-10, +10]. "Rising" = comfortably positive, "falling" = negative.
# Defaults are the loosest thresholds; the sweep tightens them.
FXY_MIN =  2.5   # yen strengthening
TLT_MIN =  2.5   # bonds bid
SPY_MAX = -2.5   # equities weakening

CORR_WIN = 60    # trailing window for the FXY<->SPY return correlation guard
MIN_HISTORY = 200  # need >=200 bars for the SMA(200) leg of the trend score

FWD_WINDOWS = [10, 20, 60]

# ── Alpha gate (decides whether Phase 2 proceeds) ─────────────────────────────
# Defensive gate: PASS needs a statistically significant NEGATIVE 10d spread vs base
# AND the guarded signal must be more reliably-down than the unguarded state.
GATE_SPREAD = -1.0   # <= -1% 10d forward SPY spread vs the all-days base
GATE_T      = 1.96   # |t| on the excess return (magnitude of the protective edge)
GATE_N      = 20     # minimum episodes for a trustworthy signal

# ── Parameter sweep grids ─────────────────────────────────────────────────────
SWEEP_CORR  = [0.0, -0.2, -0.3, -0.4, -0.6]   # require corr <= this (guard tightness)
SWEEP_STATE = [1.25, 2.5, 5.0]                # trend-score magnitude for the state
SWEEP_CWIN  = [40, 60, 90]                    # trailing correlation window

# ── Confidence-scaling calibration bands (Table C) -> live downgrade magnitude ──
# Bands on the rolling FXY<->SPY correlation at signal time. NOTE the curve is NOT
# monotonic: empirically only the deepest band (<=-0.60) is reliably protective while
# the mid bands can be risk-ON, so the downgrade keys off each band's OWN measured
# spread (below), never a naive "more negative corr => bigger downgrade" assumption.
CORR_BANDS = [
    ("<=-0.60", -1.01, -0.60),
    ("-0.60..-0.40", -0.60, -0.40),
    ("-0.40..-0.20", -0.40, -0.20),
    ("-0.20..0", -0.20, 0.0),
    (">=0", 0.0, 1.01),
]

# Extended era buckets. The macro basket goes back to 2007, so PREPEND the GFC and
# pre-2014 history that _study_utils.WINDOWS (built for the 2014+ digit-sum era)
# omits -- the 2008 GFC and 2020 COVID crashes are the single most important eras
# for a defensive gate and must be visible in the era slice.
ERAS_EXT = [
    ("GFC 07-10", "2007-01-01", "2010-12-31"),
    ("11-13",     "2011-01-01", "2013-12-31"),
] + list(WINDOWS)

# Dormancy check -- the operational form of the "can this relationship end?" worry.
# A statistically-significant signal that has NOT fired in the current regime is a
# stale crash-relic, not a live gate. If the winning cohort's last fire predates this
# cutoff (the post-COVID BOJ-normalization era), the verdict is demoted to DORMANT.
RECENT_CUTOFF = "2022-01-01"


def _closes_by_date(symbol):
    """{date: close} for a symbol from the local OHLCV cache, or None if unusable.

    Returns a date->close map (not a bare list) so the macro series can be aligned
    on their shared calendar before correlating -- the one real difference from the
    single-symbol studies, where every row already shares one symbol's dates.
    """
    path = OHLCV / f"{symbol}_daily.json"
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    out = {}
    for d, b in ts.items():
        try:
            out[d] = float(b["4. close"])
        except (KeyError, ValueError):
            continue
    return out or None


def _trend_score(closes):
    """SMA-stack trend score in [-10, +10]; None if < MIN_HISTORY bars.

    KEEP IN SYNC with ai_portfolio_game.calculate_ticker_trend_score (the canonical
    live definition). Replicated here (rather than imported) so the study never
    triggers ai_portfolio_game module-load side effects, matching how the other
    backtesting studies replicate their live logic.
    """
    if len(closes) < MIN_HISTORY:
        return None
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200
    price = closes[-1]
    s_20 = 2.5 if price > sma20 else -2.5
    s_50 = 2.5 if price > sma50 else -2.5
    s_200 = 2.5 if price > sma200 else -2.5
    s_cross1 = 1.25 if sma20 > sma50 else -1.25
    s_cross2 = 1.25 if sma50 > sma200 else -1.25
    return s_20 + s_50 + s_200 + s_cross1 + s_cross2


def _pearson(xs, ys):
    """Pearson correlation of two equal-length series, or None if undefined."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _returns(closes):
    """Simple daily returns; returns[i] corresponds to the move INTO closes[i+1]."""
    out = []
    for a, b in zip(closes, closes[1:]):
        out.append((b - a) / a if a > 0 else 0.0)
    return out


def _fwd(closes, i, w):
    j = i + w
    if j >= len(closes):
        return None
    e, f = closes[i], closes[j]
    if e <= 0 or f <= 0:
        return None
    return (f - e) / e * 100.0


def _build_signals(dates, spy, fxy, tlt, corr_win, fxy_min, tlt_min, spy_max):
    """Scan the aligned basket; return (signals, base_stats).

    A signal row is any day whose STATE fires (carry-unwind). Each row carries the
    rolling FXY<->SPY correlation at that day (the guard input, applied later so one
    scan serves every SWEEP_CORR threshold) and the forward SPY returns.

    base_stats are computed over EVERY scannable day (state or not) -- the all-days
    universe baseline the spreads are measured against.
    """
    n = len(dates)
    spy_ret = _returns(spy)   # spy_ret[k] is the move from day k to day k+1
    fxy_ret = _returns(fxy)
    signals = []
    base = {w: [] for w in FWD_WINDOWS}

    # Start once every leg has >=MIN_HISTORY closes AND a full corr window of returns.
    start = max(MIN_HISTORY, corr_win + 1)
    for i in range(start, n):
        # all-days baseline (uses only future bars for the outcome)
        for w in FWD_WINDOWS:
            r = _fwd(spy, i, w)
            if r is not None:
                base[w].append(r)

        fxy_t = _trend_score(fxy[:i + 1])
        tlt_t = _trend_score(tlt[:i + 1])
        spy_t = _trend_score(spy[:i + 1])
        if fxy_t is None or tlt_t is None or spy_t is None:
            continue
        if not (fxy_t >= fxy_min and tlt_t >= tlt_min and spy_t <= spy_max):
            continue

        # rolling corr guard over the trailing corr_win returns ending at day i
        # (returns index k covers k->k+1, so the last usable return ending at i is i-1)
        win_spy = spy_ret[i - corr_win:i]
        win_fxy = fxy_ret[i - corr_win:i]
        corr = _pearson(win_fxy, win_spy)
        if corr is None:
            continue

        row = {"date": dates[i], "corr": round(corr, 4),
               "fxy_t": fxy_t, "tlt_t": tlt_t, "spy_t": spy_t}
        for w in FWD_WINDOWS:
            row[f"fwd{w}"] = _fwd(spy, i, w)
        signals.append(row)

    base_stats = {w: (sum(v) / len(v) if v else 0.0) for w, v in base.items()}
    base_up = {w: (sum(1 for x in v if x > 0) / len(v) if v else 0.5)
               for w, v in base.items()}
    return signals, base_stats, base_up


def _agg(rows, base_ret, base_up):
    """avg/win/n/spread/z/t for a set of signal rows at each forward window.

    z   -- down-rate vs the universe up-rate (binomial); positive z => more DOWN days
           than the market baseline (the protective direction).
    t   -- t-stat on the 10d excess return magnitude (mean - base) / SE.
    """
    out = {}
    for w in FWD_WINDOWS:
        f = [r[f"fwd{w}"] for r in rows if r.get(f"fwd{w}") is not None]
        nn = len(f)
        if not nn:
            out[w] = {"n": 0, "avg": None, "win": 0.0, "spread": None, "z": 0.0, "t": 0.0}
            continue
        mean = sum(f) / nn
        win = sum(1 for x in f if x > 0) / nn
        base_dn = 1 - base_up[w]
        sig_dn = 1 - win
        z = 0.0
        if 0 < base_dn < 1:
            se = math.sqrt(base_dn * (1 - base_dn) / nn)
            z = (sig_dn - base_dn) / se if se > 0 else 0.0
        t = 0.0
        if nn >= 2:
            var = sum((x - mean) ** 2 for x in f) / (nn - 1)
            sd = math.sqrt(var)
            if sd > 0:
                t = (mean - base_ret[w]) / (sd / math.sqrt(nn))
        out[w] = {"n": nn, "avg": round(mean, 3), "win": round(win, 4),
                  "spread": round(mean - base_ret[w], 3), "z": round(z, 3),
                  "t": round(t, 2)}
    return out


def _f(x):
    return f"{x:+.2f}" if isinstance(x, (int, float)) else "  -  "


def qualifies(r):
    """A sweep row clears the alpha gate: protective spread AND significant t.

    Single source of truth for the gate predicate — macro_signal_scan imports this
    so the battery and the carry study apply an identical bar.
    """
    return (r["spread"] is not None and r["spread"] <= GATE_SPREAD
            and abs(r["t"]) >= GATE_T)


def run():
    spy_m = _closes_by_date(MKT)
    fxy_m = _closes_by_date(YEN)
    tlt_m = _closes_by_date(BONDS)
    missing = [s for s, m in ((MKT, spy_m), (YEN, fxy_m), (BONDS, tlt_m)) if not m]
    if missing:
        _log.console(f"ABORT — missing/unusable OHLCV cache for: {', '.join(missing)}")
        return False

    # Date-align on the shared calendar of all three legs.
    common = sorted(set(spy_m) & set(fxy_m) & set(tlt_m))
    if len(common) < MIN_HISTORY + max(FWD_WINDOWS) + max(SWEEP_CWIN) + 1:
        _log.console(f"ABORT — only {len(common)} shared dates across {MKT}/{YEN}/{BONDS}; "
                     f"need much more history.")
        return False
    spy = [spy_m[d] for d in common]
    fxy = [fxy_m[d] for d in common]
    tlt = [tlt_m[d] for d in common]

    _log.console(f"Aligned {len(common)} shared trading days across {MKT}/{YEN}/{BONDS} "
                 f"({common[0]} .. {common[-1]})\n")

    # Default-parameter scan for Tables A/B/C.
    signals, base_ret, base_up = _build_signals(
        common, spy, fxy, tlt, CORR_WIN, FXY_MIN, TLT_MIN, SPY_MAX)

    _log.console(f"Baseline (all days): 10d avg={_f(base_ret[10])}%  up-rate={base_up[10]:.3f} | "
                 f"state-fires days={len(signals)}\n")

    # ── Table A — conditioning showdown ──────────────────────────────────────
    _log.console("=" * 78)
    _log.console("TABLE A — Conditioning showdown (does the corr guard add value?)")
    _log.console("=" * 78)
    _log.console(f"{'signal':<22}{'n':>6}{'avg10%':>9}{'win10':>8}{'spread10':>10}"
                 f"{'z10':>7}{'t10':>7}")
    default_guard = -0.3
    state_only = signals
    guarded = [r for r in signals if r["corr"] <= default_guard]
    table_a = {}
    for label, rows in (("state-only", state_only),
                        (f"state+corr<={default_guard}", guarded)):
        full = _agg(rows, base_ret, base_up)
        table_a[label] = full
        s = full[10]
        _log.console(f"{label:<22}{s['n']:>6}{_f(s['avg']):>9}{s['win']:>8.3f}"
                     f"{_f(s['spread']):>10}{s['z']:>7.2f}{s['t']:>7.2f}")

    # ── Parameter sweep (computed FIRST so Table B can slice the WINNING cohort) ──
    sweep_rows = []
    # Cache one scan per (state, cwin); the corr threshold is applied as a filter.
    scan_cache = {}
    for state in SWEEP_STATE:
        for cwin in SWEEP_CWIN:
            key = (state, cwin)
            if key not in scan_cache:
                scan_cache[key] = _build_signals(
                    common, spy, fxy, tlt, cwin, state, state, -state)
            sigs, bret, bup = scan_cache[key]
            for cmax in SWEEP_CORR:
                rows = [r for r in sigs if r["corr"] <= cmax]
                s = _agg(rows, bret, bup)[10]
                if s["n"] < GATE_N:
                    continue
                sweep_rows.append({"corr_max": cmax, "state_min": state,
                                   "corr_win": cwin, **s})

    # Rank: qualifying combos (clear the protective spread floor AND significant t)
    # first, then by most-negative spread.
    sweep_rows.sort(key=lambda r: (qualifies(r), -(r["spread"] or 0)), reverse=True)
    qualified = [s for s in sweep_rows if qualifies(s) and s["n"] >= GATE_N]
    best = qualified[0] if qualified else None

    _log.console("\n" + "=" * 90)
    _log.console("PARAMETER SWEEP — corr guard x state strength x corr window "
                 "(most-protective first)")
    _log.console("=" * 90)
    _log.console(f"{'corr<=':>7}{'state>=':>8}{'cwin':>6}{'n':>6}{'avg10%':>9}"
                 f"{'win10':>8}{'spread10':>10}{'z10':>7}{'t10':>7}")
    for s in sweep_rows[:12]:
        mark = "  <-" if qualifies(s) else ""
        _log.console(f"{s['corr_max']:>7.2f}{s['state_min']:>8.2f}{s['corr_win']:>6}"
                     f"{s['n']:>6}{_f(s['avg']):>9}{s['win']:>8.3f}{_f(s['spread']):>10}"
                     f"{s['z']:>7.2f}{s['t']:>7.2f}{mark}")

    # ── Table B — WINNING-COHORT era slice + DORMANCY (shelf-life made visible) ──
    # Slice the exact cohort that produced the verdict (the best combo) across the
    # FULL history incl. the GFC, and check whether it still fires in the modern
    # BOJ-normalization regime. A significant-but-dormant signal is a crash relic,
    # not a live 2026 carry gate.
    if best:
        bsigs, bret, bup = scan_cache[(best["state_min"], best["corr_win"])]
        # fires = every guard-passing day (dormancy is about WHEN it last fired, so it
        # must include the most recent fires even before their 10d outcome settles —
        # that unsettled tail is exactly where a monthly re-run first sees a reawakening).
        # cohort = the settled subset used for the forward-return stats/era slice.
        fires = [r for r in bsigs if r["corr"] <= best["corr_max"]]
        cohort = [r for r in fires if r.get("fwd10") is not None]
        unguarded = _agg(bsigs, bret, bup)[10]
    else:
        fires, cohort, bret, bup, unguarded = [], [], base_ret, base_up, {"win": 0.0}

    _log.console("\n" + "=" * 78)
    _log.console("TABLE B — WINNING-COHORT forward SPY return by era (+ dormancy check)")
    _log.console("=" * 78)
    _log.console(f"{'era':<12}{'n':>6}{'avg10%':>9}{'win10':>8}{'spread10':>10}{'z10':>7}")
    eras = {}
    for label, s0, e0 in ERAS_EXT:
        rows = [r for r in cohort if s0 <= r["date"] <= e0]
        s = _agg(rows, bret, bup)[10]
        eras[label] = s
        _log.console(f"{label:<12}{s['n']:>6}{_f(s['avg']):>9}{s['win']:>8.3f}"
                     f"{_f(s['spread']):>10}{s['z']:>7.2f}")

    dormancy = {"last_fire": None, "fires_since_cutoff": 0,
                "cutoff": RECENT_CUTOFF, "is_dormant": True}
    if fires:
        dts = sorted(r["date"] for r in fires)
        dormancy["last_fire"] = dts[-1]
        dormancy["fires_since_cutoff"] = sum(1 for d in dts if d >= RECENT_CUTOFF)
        dormancy["is_dormant"] = dormancy["fires_since_cutoff"] == 0
    _log.console(f"\n  last fire: {dormancy['last_fire']} | fires since "
                 f"{RECENT_CUTOFF}: {dormancy['fires_since_cutoff']} | "
                 f"DORMANT: {dormancy['is_dormant']}")

    # ── Table C — confidence-scaling calibration ─────────────────────────────
    _log.console("\n" + "=" * 78)
    _log.console("TABLE C — Confidence-scaling: SPY 10d outcome by corr band (guard curve)")
    _log.console("=" * 78)
    _log.console(f"{'corr band':<16}{'n':>6}{'avg10%':>9}{'win10':>8}{'spread10':>10}"
                 f"{'z10':>7}{'-> downgrade':>14}")
    corr_calibration = []
    for name, lo, hi in CORR_BANDS:
        rows = [r for r in signals if lo <= r["corr"] < hi]
        s = _agg(rows, base_ret, base_up)[10]
        # More-negative spread + enough history => a bigger, more confident downgrade.
        if s["n"] >= GATE_N and s["spread"] is not None and s["spread"] <= GATE_SPREAD:
            mag = 2 if s["spread"] <= 2 * GATE_SPREAD else 1
        else:
            mag = 0
        corr_calibration.append({"band": name, "lo": lo, "hi": hi,
                                 "downgrade_notches": mag, **s})
        _log.console(f"{name:<16}{s['n']:>6}{_f(s['avg']):>9}{s['win']:>8.3f}"
                     f"{_f(s['spread']):>10}{s['z']:>7.2f}{mag:>14}")

    # ── Alpha gate (statistical PASS is necessary but NOT sufficient — a dormant
    #    signal is demoted to CONDITIONAL: real once, but not live in this regime) ──
    beats_unguarded = bool(best) and best["win"] < unguarded["win"]
    stat_pass = bool(best) and beats_unguarded
    passes = stat_pass and not dormancy["is_dormant"]

    _log.console("\n" + "=" * 78)
    _log.console("ALPHA GATE (protective spread + beats unguarded state + still live)")
    _log.console("=" * 78)
    if best:
        _log.console(f"  combo: corr<={best['corr_max']:.2f}  state>=|{best['state_min']:.2f}|  "
                     f"corr_win={best['corr_win']}")
        _log.console(f"  n={best['n']} (need >= {GATE_N})")
        _log.console(f"  10d spread={_f(best['spread'])}% (need <= {GATE_SPREAD}%)")
        _log.console(f"  excess-return |t|={abs(best['t']):.2f} (need >= {GATE_T})")
        _log.console(f"  guarded win10 {best['win']:.3f} < unguarded "
                     f"{unguarded['win']:.3f}  => {beats_unguarded}")
        _log.console(f"  statistically significant: {stat_pass}")
        _log.console(f"  still firing in current regime (since {RECENT_CUTOFF}): "
                     f"{not dormancy['is_dormant']}  (last fire {dormancy['last_fire']})")
    else:
        _log.console("  No combo clears the protective spread floor with significant t.")
        if sweep_rows:
            t = sweep_rows[0]
            _log.console(f"  closest: corr<={t['corr_max']:.2f} state>=|{t['state_min']:.2f}| "
                         f"cwin={t['corr_win']} | spread={_f(t['spread'])}% t={t['t']:.2f} n={t['n']}")

    if passes:
        verdict = "PASS — statistically real AND still live; proceed to Phase 2"
    elif stat_pass and dormancy["is_dormant"]:
        verdict = ("CONDITIONAL — statistically real but DORMANT (crash-relic: no "
                   f"fires since {RECENT_CUTOFF}). Do NOT wire as a live signal now; "
                   "re-run monthly and treat a re-awakening (fires_since_cutoff>0) as "
                   "the trigger to revisit Phase 2.")
    else:
        verdict = "FAIL — park the factor"
    _log.console(f"\n  ==> {verdict}")

    # ── Persist ──────────────────────────────────────────────────────────────
    payload = {
        "meta": {
            "aligned_days": len(common), "first": common[0], "last": common[-1],
            "basket": {"mkt": MKT, "yen": YEN, "bonds": BONDS},
            "base_avg10": round(base_ret[10], 3), "base_up10": round(base_up[10], 4),
            "params": {"FXY_MIN": FXY_MIN, "TLT_MIN": TLT_MIN, "SPY_MAX": SPY_MAX,
                       "CORR_WIN": CORR_WIN, "default_guard": default_guard},
            "gate": {"pass": passes, "stat_pass": stat_pass, "best_combo": best,
                     "beats_unguarded": beats_unguarded, "dormancy": dormancy,
                     "verdict": verdict},
        },
        "table_a": table_a,
        "eras": eras,
        "corr_calibration": corr_calibration,
        "sweep": sweep_rows,
        "signals": signals,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved {len(signals)} state-fire signals + stats to {OUT_FILE}")
    return passes


if __name__ == "__main__":
    run()
