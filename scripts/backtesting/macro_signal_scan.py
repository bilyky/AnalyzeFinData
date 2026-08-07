"""
Macro Risk-Off Signal Battery — generalize the carry-unwind gate to other havens.

The yen carry-unwind study (carry_unwind_study.py) found a real but DORMANT signal:
"FXY up + TLT up + SPY down, guarded by negative FXY<->SPY correlation" protected
against SPY drawdowns in 2008 and 2020, but has not fired since (the BOJ-normalization
regime killed it). Natural follow-up: are there SIMILAR flight-to-safety relationships
that are still LIVE today?

This scanner reuses the carry-unwind machinery on a battery of "haven-divergence"
signals, one per safe-haven leg, all with the SAME structure so results are directly
comparable:

    State (at each aligned day i, bars <= i only):
        HAVEN trend RISING  and  SPY trend FALLING
        (the haven catches a bid while equities weaken)
    Guard (self-decaying condition B):
        rolling corr(HAVEN_returns, SPY_returns) <= threshold
        (haven and stocks genuinely moving OPPOSITE lately -- the relationship is
         intact; when it decays toward 0 the guard fails and the signal goes silent)
    Outcome:
        forward SPY return at 10/20/60d. Protective = significantly BELOW baseline.

Havens tested (US-listed ETF proxies present in the OHLCV cache):
    TLT  long Treasuries        -- the classic flight-to-safety bond bid
    GDX  gold miners            -- gold proxy (leveraged/noisy vs spot; best we cache)
    UUP  US dollar bullish      -- dollar spike = global risk-off / liquidity squeeze
    FXY  Japanese yen           -- the carry-unwind baseline (2-leg form here)
    FXE  euro                   -- CONTROL: not really a haven, expect little/no edge

NOT testable with the current cache (no proxy): credit spreads (HYG/LQD/JNK), VIX
ETFs, spot gold (GLD), short/mid rates (SHY/IEF).

Each haven gets the same treatment as the carry study: a parameter sweep, an alpha
gate, an era slice, and a DORMANCY check (has the winning cohort fired since
RECENT_CUTOFF?). The battery is ranked so LIVE, statistically-significant signals
surface first, DORMANT crash-relics next, and no-edge legs last.

Usage:
    python scripts/backtesting/macro_signal_scan.py

Writes Data/macro_signal_scan.json. Recalibrate MONTHLY (same rationale as the
carry study): re-run after the OHLCV top-up; a haven flipping from DORMANT to LIVE
(fires_since_cutoff > 0) is the trigger to consider wiring it into get_market_regime.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Reuse the carry-unwind study's pure, tested helpers (import is side-effect free —
# its scan is guarded by __main__), so the two studies stay in lock-step.
from carry_unwind_study import (
    _closes_by_date, _trend_score, _pearson, _returns, _fwd, _agg,
    FWD_WINDOWS, GATE_SPREAD, GATE_T, GATE_N, MIN_HISTORY, RECENT_CUTOFF,
    SWEEP_CORR, SWEEP_STATE, SWEEP_CWIN, ERAS_EXT,
)
from aether_logger import get_logger as _get_logger

_log = _get_logger("macro_scan")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUT_FILE = BASE_DIR / "Data" / "macro_signal_scan.json"

MKT = "SPY"

# (label, haven symbol, note). Order is display order; ranking is by edge below.
HAVENS = [
    ("Bonds",  "TLT", "long Treasuries — classic flight-to-safety"),
    ("Gold",   "GDX", "gold miners (leveraged proxy vs spot)"),
    ("Dollar", "UUP", "USD spike = global risk-off / liquidity squeeze"),
    ("Yen",    "FXY", "carry-unwind baseline (2-leg form)"),
    ("Euro",   "FXE", "control — not a true haven"),
]


def _build_pair_signals(dates, haven, spy, corr_win, haven_min, spy_max):
    """Generalized 2-leg version of carry_unwind_study._build_signals.

    Signal fires when the haven trend is rising AND SPY trend is falling; each row
    carries the rolling haven<->SPY return correlation (guard applied later) and the
    forward SPY returns. base_stats are over EVERY scannable day (all-days baseline).
    No look-ahead: trend/corr use bars <= i; forward returns index strictly future.
    """
    n = len(dates)
    spy_ret = _returns(spy)
    hav_ret = _returns(haven)
    signals = []
    base = {w: [] for w in FWD_WINDOWS}

    start = max(MIN_HISTORY, corr_win + 1)
    for i in range(start, n):
        for w in FWD_WINDOWS:
            r = _fwd(spy, i, w)
            if r is not None:
                base[w].append(r)

        hav_t = _trend_score(haven[:i + 1])
        spy_t = _trend_score(spy[:i + 1])
        if hav_t is None or spy_t is None:
            continue
        if not (hav_t >= haven_min and spy_t <= spy_max):
            continue

        corr = _pearson(hav_ret[i - corr_win:i], spy_ret[i - corr_win:i])
        if corr is None:
            continue

        row = {"date": dates[i], "corr": round(corr, 4), "hav_t": hav_t, "spy_t": spy_t}
        for w in FWD_WINDOWS:
            row[f"fwd{w}"] = _fwd(spy, i, w)
        signals.append(row)

    base_ret = {w: (sum(v) / len(v) if v else 0.0) for w, v in base.items()}
    base_up = {w: (sum(1 for x in v if x > 0) / len(v) if v else 0.5)
               for w, v in base.items()}
    return signals, base_ret, base_up


def _qual(r):
    return (r["spread"] is not None and r["spread"] <= GATE_SPREAD
            and abs(r["t"]) >= GATE_T)


def _scan_haven(label, sym, note, spy_m):
    """Full sweep + gate + dormancy for one haven. Returns a result dict or None."""
    hav_m = _closes_by_date(sym)
    if not hav_m:
        _log.console(f"  [{label}/{sym}] SKIP — no usable OHLCV cache.")
        return None
    common = sorted(set(spy_m) & set(hav_m))
    if len(common) < MIN_HISTORY + max(FWD_WINDOWS) + max(SWEEP_CWIN) + 1:
        _log.console(f"  [{label}/{sym}] SKIP — only {len(common)} shared days.")
        return None
    spy = [spy_m[d] for d in common]
    hav = [hav_m[d] for d in common]

    # Sweep (cache one scan per state/cwin; corr threshold applied as a filter).
    scan_cache, sweep_rows = {}, []
    for state in SWEEP_STATE:
        for cwin in SWEEP_CWIN:
            key = (state, cwin)
            if key not in scan_cache:
                scan_cache[key] = _build_pair_signals(common, hav, spy, cwin, state, -state)
            sigs, bret, bup = scan_cache[key]
            for cmax in SWEEP_CORR:
                rows = [r for r in sigs if r["corr"] <= cmax]
                s = _agg(rows, bret, bup)[10]
                if s["n"] < GATE_N:
                    continue
                sweep_rows.append({"corr_max": cmax, "state_min": state,
                                   "corr_win": cwin, **s})
    sweep_rows.sort(key=lambda r: (_qual(r), -(r["spread"] or 0)), reverse=True)
    qualified = [s for s in sweep_rows if _qual(s) and s["n"] >= GATE_N]
    best = qualified[0] if qualified else None

    # Winning-cohort era slice + dormancy + beats-unguarded check.
    eras, dormancy = {}, {"last_fire": None, "fires_since_cutoff": 0,
                          "cutoff": RECENT_CUTOFF, "is_dormant": True}
    beats_unguarded = False
    if best:
        bsigs, bret, bup = scan_cache[(best["state_min"], best["corr_win"])]
        cohort = [r for r in bsigs if r["corr"] <= best["corr_max"]
                  and r.get("fwd10") is not None]
        unguarded = _agg(bsigs, bret, bup)[10]
        beats_unguarded = best["win"] < unguarded["win"]
        for elabel, s0, e0 in ERAS_EXT:
            eras[elabel] = _agg([r for r in cohort if s0 <= r["date"] <= e0], bret, bup)[10]
        if cohort:
            dts = sorted(r["date"] for r in cohort)
            dormancy["last_fire"] = dts[-1]
            dormancy["fires_since_cutoff"] = sum(1 for d in dts if d >= RECENT_CUTOFF)
            dormancy["is_dormant"] = dormancy["fires_since_cutoff"] == 0

    stat_pass = bool(best) and beats_unguarded
    live = stat_pass and not dormancy["is_dormant"]
    status = "LIVE" if live else ("DORMANT" if stat_pass else "NONE")
    return {"label": label, "symbol": sym, "note": note, "days": len(common),
            "best": best, "stat_pass": stat_pass, "beats_unguarded": beats_unguarded,
            "dormancy": dormancy, "eras": eras, "status": status,
            "sweep_top": sweep_rows[:5]}


def run():
    spy_m = _closes_by_date(MKT)
    if not spy_m:
        _log.console(f"ABORT — no usable {MKT} cache.")
        return
    _log.console(f"Scanning {len(HAVENS)} haven-divergence signals vs {MKT}...\n")

    results = []
    for label, sym, note in HAVENS:
        r = _scan_haven(label, sym, note, spy_m)
        if r:
            results.append(r)

    # Rank: LIVE first, then DORMANT, then NONE; within a tier by most-negative spread.
    order = {"LIVE": 2, "DORMANT": 1, "NONE": 0}
    results.sort(key=lambda r: (order[r["status"]],
                                -((r["best"] or {}).get("spread") or 0)), reverse=True)

    _log.console("=" * 96)
    _log.console("MACRO RISK-OFF BATTERY — haven-divergence signals ranked (LIVE first)")
    _log.console("=" * 96)
    _log.console(f"{'haven':<8}{'sym':<5}{'status':<9}{'n':>5}{'spread10':>10}{'t10':>7}"
                 f"{'last_fire':>13}{'since':>7}  best combo")
    for r in results:
        b = r["best"]
        if b:
            combo = f"corr<={b['corr_max']:.2f} state>=|{b['state_min']:.2f}| cw={b['corr_win']}"
            spread = f"{b['spread']:+.2f}"; t = f"{b['t']:+.2f}"
        else:
            combo, spread, t = "(no qualifying combo)", "  -  ", "  -  "
        d = r["dormancy"]
        _log.console(f"{r['label']:<8}{r['symbol']:<5}{r['status']:<9}"
                     f"{(b['n'] if b else 0):>5}{spread:>10}{t:>7}"
                     f"{str(d['last_fire']):>13}{d['fires_since_cutoff']:>7}  {combo}")

    _log.console("\nLegend: LIVE = significant AND still firing since "
                 f"{RECENT_CUTOFF}; DORMANT = significant crash-relic, not firing now; "
                 "NONE = no qualifying protective edge.")
    live = [r["label"] for r in results if r["status"] == "LIVE"]
    _log.console(f"\n  ==> LIVE risk-off signals worth wiring: "
                 f"{', '.join(live) if live else 'NONE — all dormant or no edge'}")

    with open(OUT_FILE, "w") as f:
        json.dump({"market": MKT, "cutoff": RECENT_CUTOFF, "results": results}, f)
    _log.console(f"\nSaved battery to {OUT_FILE}")


if __name__ == "__main__":
    run()
