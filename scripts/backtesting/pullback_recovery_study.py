"""
Rubber-Band Reversal (RBR) Study — "AI-Age Overreaction Pullback + Fast Recovery".

Backtest-first discovery for the pattern watched live in Jul-2026: a machine/
headline-driven earnings gap overshoots to the downside, then V-recovers. The
canonical anchor is INTC: $100.23 -> $81.79 (-18.4% over 4 sessions), then a
higher-low reversal bar (Jul-30 low 85.97 > 81.79, close +11%) and a +13% snap-back.

This study answers three questions with PURE PRICE/VOLUME and NO LOOK-AHEAD:
    1. Does the two-leg RBR pattern carry forward-return alpha at 10d / 60d?
    2. Which overreaction magnitudes (depth x speed) recover best?
    3. THE KEY TEST: does waiting for the confirmed recovery bar beat catching
       the knife as it falls? (knife-catch vs confirmed-recovery, head to head)

Pattern (two legs, evaluated at each "current bar" i using only bars[:i+1]):
    Leg 1 - Overreaction drawdown: a big, fast decline from a local swing-high
            close (pre_high) down to a trough low, optionally intensified by an
            overnight gap-down (the backtestable catalyst proxy) and/or
            capitulation volume.
    Leg 2 - Recovery confirmation: the trough is already in (t_idx < i), today
            makes a HIGHER LOW (low[i] > trough_low), closes strong (green and in
            the upper half of its range), on above-average volume.

Entry rules compared:
    knife     - enter the first bar the drawdown crosses the depth threshold
                (buying as it falls, trough is today)
    confirmed - enter on the Leg-2 higher-low reversal bar (trough already in)

Note on fractals: risk_utils.detect_support (k-after Sperandeo fractal) needs k
bars AFTER the pivot to confirm, so it could not confirm INTC's Jul-29 low until
~3 sessions later — too slow for a "fast recovery" catch. So the ENTRY trigger
here uses a strictly-past running-minimum higher-low (fires on the reversal bar);
the k-after fractal stays reserved for the downstream stop ladder, not the entry.

Usage:
    python scripts/backtesting/pullback_recovery_study.py

Writes Data/pullback_recovery_study.json and prints four sections: Table A
(knife vs confirmed), Table B (depth x speed buckets), the parameter sweep, and
the alpha gate verdict.

Cadence — recalibrate MONTHLY (like digit_sum_study), NOT weekly. The optimal
combo is derived from 25+ years x 500+ symbols, so it does not shift meaningfully
week-to-week, and a weekly re-derivation only burns compute and invites overfitting
to the last few sessions. Re-run after the monthly OHLCV top-up; if the best
qualifying combo (depth/speed/range/gap) moves materially or the gate flips, update
the RBR_* detector constants in aether/patterns.py and re-run the tests. The DAILY/
WEEKLY-cadence piece is the live monitor (scripts/monitoring/rbr_watch.py), which
scans for current Leg-1 setups off the already-refreshed bars and needs no sweep.
"""

import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import instruments
from aether_logger import get_logger as _get_logger

_log = _get_logger("pullback_recovery")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV    = BASE_DIR / "Data" / "Symbol_full"
OUT_FILE = BASE_DIR / "Data" / "pullback_recovery_study.json"

# ── Pattern parameters (detection uses the LOOSEST criteria; the depth/speed
#    sweep is then done implicitly by bucketing, so one pass yields every cell) ──
LOOKBACK      = 10     # bars to search back for the pre-drop swing high
DEPTH_MIN     = -0.10  # a drawdown must be at least this deep to qualify at all
SPEED_MAX     = 8      # ... and reach its trough within this many bars
GAP_PCT       = 0.03   # overnight gap-down >= 3% marks a catalyst bar
VOL_MULT      = 1.10   # reversal/capitulation volume vs 20-bar average
REV_UPPER_HALF = 0.50  # reversal close must sit in the upper half of its range
VOL_WIN       = 20     # trailing window for the average-volume baseline
WARMUP        = VOL_WIN + LOOKBACK + 2

FWD_WINDOWS   = [10, 60]   # matches S10 / L60

# ── Alpha gate (decides whether Phase 2 proceeds) ──
GATE_SPREAD   = 1.0    # >= ~1% 10d forward-return spread vs universe base
GATE_T        = 1.96   # 95% confidence on the EXCESS-RETURN t-stat (magnitude edge)
GATE_N        = 40     # minimum episodes for a trustworthy combo

# ── Parameter sweep grid (searched offline over the recorded confirmed signals) ──
SWEEP_DEPTH   = [-0.10, -0.12, -0.15, -0.18, -0.20]  # require drop_pct <= this
SWEEP_SPEED   = [3, 4, 5, 6, 8]                       # require speed <= this
SWEEP_RANGE   = [0.0, 0.5, 0.6, 0.7]                  # require reversal close range-pos >= this
SWEEP_VOL     = [0.0, 1.0, 1.2, 1.5]                  # require reversal vol_ratio >= this
SWEEP_GAP     = [None, False, True]                  # None=any, False=no-gap only, True=gap only


def _bars(path):
    """Return parallel float arrays (dates, open, high, low, close, volume)."""
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    dates = sorted(ts.keys())
    if len(dates) < WARMUP + max(FWD_WINDOWS):
        return None
    o, h, l, c, v = [], [], [], [], []
    for d in dates:
        b = ts[d]
        try:
            o.append(float(b["1. open"]));  h.append(float(b["2. high"]))
            l.append(float(b["3. low"]));   c.append(float(b["4. close"]))
            v.append(float(b.get("5. volume", 0) or 0))
        except (KeyError, ValueError):
            return None
    return dates, o, h, l, c, v


def _depth_bucket(drop_pct):
    if drop_pct <= -0.20: return "d20"   # >= 20% overshoot
    if drop_pct <= -0.15: return "d15"   # 15-20%
    return "d10"                          # 10-15%


def _speed_bucket(speed):
    return "fast" if speed <= 4 else "med"   # fast: <=4 bars, med: 5-8


def _fwd(closes, i, w):
    j = i + w
    if j >= len(closes):
        return None
    e, f = closes[i], closes[j]
    if e <= 0 or f <= 0:
        return None
    return (f - e) / e * 100.0


def _drawdown_at(i, o, h, l, c):
    """Leg-1 evaluation at bar i using only bars <= i.

    Returns dict(pre_high, trough_low, t_idx, drop_pct, speed, had_gap) or None.
    """
    lo = max(0, i - LOOKBACK)
    # pre_high = highest close in the window; the drawdown is measured from there
    h_idx = max(range(lo, i + 1), key=lambda j: c[j])
    pre_high = c[h_idx]
    if pre_high <= 0 or h_idx == i:
        return None
    # trough = lowest low from the high forward to today
    t_idx = min(range(h_idx, i + 1), key=lambda j: l[j])
    trough_low = l[t_idx]
    drop_pct = (trough_low - pre_high) / pre_high
    speed = t_idx - h_idx
    if drop_pct > DEPTH_MIN or speed < 1 or speed > SPEED_MAX:
        return None
    had_gap = any(o[j] < c[j - 1] * (1 - GAP_PCT) for j in range(h_idx + 1, i + 1))
    return dict(pre_high=pre_high, trough_low=trough_low, t_idx=t_idx,
                drop_pct=drop_pct, speed=speed, had_gap=had_gap)


def _vol_ratio(v, i):
    """Reversal-bar volume divided by the trailing VOL_WIN average (0 if unknown)."""
    base = [x for x in v[i - VOL_WIN:i] if x > 0]
    if not base:
        return 0.0
    return v[i] / (sum(base) / len(base))


def _signals_for_symbol(sym, arrays):
    """Yield at most one knife + one confirmed record per drawdown EPISODE.

    An episode opens when a qualifying drawdown first appears and closes when price
    recovers out of it (the drawdown window no longer qualifies). Within an episode:
      - knife fires once, on the first bar the drop crosses the depth threshold
        (trough is today — buying as it falls);
      - confirmed fires once, on the first higher-low green bar after the trough.
    This episode model (not a fixed bar cooldown) is what lets INTC's Jul-30
    reversal fire even though an unrelated bounce occurred a week earlier.

    The confirmed GATE is deliberately loose (higher low + green close); the
    reversal-strength knobs (range position, volume ratio, depth, speed) are stored
    raw per signal so the offline sweep can tighten them without re-scanning.
    """
    dates, o, h, l, c, v = arrays
    n = len(c)
    out = []
    in_dd = fired_knife = fired_conf = False

    for i in range(WARMUP, n):
        dd = _drawdown_at(i, o, h, l, c)
        if not dd:
            in_dd = False   # recovered / aged out — episode closed
            continue
        if not in_dd:
            in_dd, fired_knife, fired_conf = True, False, False

        if dd["t_idx"] == i and not fired_knife:
            # knife-catch: trough is today (still falling / at the low)
            fired_knife = True
            out.append(_record(sym, dates[i], "knife", dd, c, i, None))
        elif dd["t_idx"] < i and not fired_conf:
            # confirmed-recovery: trough already in, today prints a higher-low green bar
            if l[i] > dd["trough_low"] and c[i] > c[i - 1]:
                fired_conf = True
                rng = h[i] - l[i]
                rev = {
                    "range_pos": round((c[i] - l[i]) / rng, 3) if rng > 0 else 0.0,
                    "ret_prev": round((c[i] - c[i - 1]) / c[i - 1] * 100, 2),
                    "vol_ratio": round(_vol_ratio(v, i), 3),
                }
                out.append(_record(sym, dates[i], "confirmed", dd, c, i, rev))
    return out


def _record(sym, date, rule, dd, c, i, rev):
    r = {"symbol": sym, "date": date, "rule": rule,
         "depth": _depth_bucket(dd["drop_pct"]), "speed": _speed_bucket(dd["speed"]),
         "gap": bool(dd["had_gap"]),
         "drop_pct": round(dd["drop_pct"] * 100, 2),   # raw depth, %
         "speed_raw": dd["speed"]}                      # raw speed, bars
    if rev:
        r.update(rev)
    for w in FWD_WINDOWS:
        r[f"fwd{w}"] = _fwd(c, i, w)
    return r


def _agg(records, base10, base10ret):
    """Aggregate a list of records into avg/win/n/z stats.

    base10    - universe base up-rate (fraction of bars with fwd10>0), for z
    base10ret - universe mean 10d forward return, for the spread
    """
    f10 = [r["fwd10"] for r in records if r["fwd10"] is not None]
    f60 = [r["fwd60"] for r in records if r["fwd60"] is not None]
    n10 = len(f10)
    if not n10:
        return {"n": 0, "avg10": None, "avg60": None, "win10": 0.0,
                "spread10": None, "z": 0.0, "t_excess": 0.0}
    mean = sum(f10) / n10
    win10 = sum(1 for x in f10 if x > 0) / n10
    # win-rate z vs the universe base up-rate (binomial)
    z = 0.0
    if 0 < base10 < 1:
        se = math.sqrt(base10 * (1 - base10) / n10)
        z = (mean and (win10 - base10) / se) if se > 0 else 0.0
    # t-stat on EXCESS return (the magnitude edge — the right test for this factor,
    # since the alpha lives in fat right-tail winners, not raw hit-rate)
    t_excess = 0.0
    if n10 >= 2:
        var = sum((x - mean) ** 2 for x in f10) / (n10 - 1)
        sd = math.sqrt(var)
        if sd > 0:
            t_excess = (mean - base10ret) / (sd / math.sqrt(n10))
    return {
        "n": n10,
        "avg10": round(mean, 3),
        "avg60": round(sum(f60) / len(f60), 3) if f60 else None,
        "win10": round(win10, 4),
        "spread10": round(mean - base10ret, 3),
        "z": round(z, 3),
        "t_excess": round(t_excess, 2),
    }


def _matches(r, depth_max, speed_max, range_min, vol_min, gap):
    return (r["drop_pct"] <= depth_max * 100
            and r["speed_raw"] <= speed_max
            and r.get("range_pos", 0.0) >= range_min
            and r.get("vol_ratio", 0.0) >= vol_min
            and (gap is None or r["gap"] == gap))


def _filter(all_recs, rule, combo):
    """Knife records sharing the best combo's depth/speed/gap footprint
    (knife bars have no reversal features, so range/vol filters don't apply)."""
    if not combo:
        return [r for r in all_recs if r["rule"] == rule]
    return [r for r in all_recs if r["rule"] == rule
            and r["drop_pct"] <= combo["depth_max"] * 100
            and r["speed_raw"] <= combo["speed_max"]
            and (combo["gap"] is None or r["gap"] == combo["gap"])]


def _sweep(conf, knife, base10, base10ret):
    """Grid-search the reversal-detection knobs. For each combo compute the
    confirmed-recovery stats AND the knife stats on the same depth/speed/gap
    footprint, so we can require that waiting for confirmation is more reliable
    (higher win-rate) than catching the knife. Returns combos with n>=GATE_N and a
    positive spread, ranked so that fully-qualifying combos surface first."""
    rows = []
    for depth_max in SWEEP_DEPTH:
        for speed_max in SWEEP_SPEED:
            # knife has no reversal features, so its footprint is depth/speed/gap only
            for gap in SWEEP_GAP:
                krecs = [r for r in knife
                         if _matches(r, depth_max, speed_max, 0.0, 0.0, gap)]
                kwin = _agg(krecs, base10, base10ret)["win10"]
                for range_min in SWEEP_RANGE:
                    for vol_min in SWEEP_VOL:
                        recs = [r for r in conf
                                if _matches(r, depth_max, speed_max, range_min, vol_min, gap)]
                        s = _agg(recs, base10, base10ret)
                        if s["n"] < GATE_N or (s["spread10"] or -99) <= 0:
                            continue
                        s["knife_win10"] = kwin
                        s["beats_knife"] = s["win10"] > kwin
                        rows.append({"depth_max": depth_max, "speed_max": speed_max,
                                     "range_min": range_min, "vol_min": vol_min,
                                     "gap": gap, **s})
    # Rank: fully-qualifying combos (clear the >=1% floor AND beat the knife's
    # reliability) surface first; within a tier, by excess-return t-stat.
    rows.sort(key=lambda r: (r["spread10"] >= GATE_SPREAD and r["beats_knife"],
                             r["t_excess"]), reverse=True)
    return rows


def run():
    files = sorted(OHLCV.glob("*_daily.json"))
    _log.console(f"Scanning {len(files)} symbols for the Rubber-Band Reversal pattern...\n")

    all_recs = []
    # universe base rates: every bar's 10d/60d forward outcome (for spread + z)
    base_up10 = base_up60 = base_tot10 = base_tot60 = 0
    base10ret_sum = base10ret_n = 0

    n_used = 0
    for path in files:
        sym = path.stem.replace("_daily", "")
        try:
            if instruments.is_excluded(sym):
                continue
        except Exception:
            pass
        arrays = _bars(path)
        if not arrays:
            continue
        n_used += 1
        c = arrays[4]
        for i in range(WARMUP, len(c)):
            r10 = _fwd(c, i, 10)
            if r10 is not None:
                base_tot10 += 1; base_up10 += 1 if r10 > 0 else 0
                base10ret_sum += r10; base10ret_n += 1
            r60 = _fwd(c, i, 60)
            if r60 is not None:
                base_tot60 += 1; base_up60 += 1 if r60 > 0 else 0
        all_recs.extend(_signals_for_symbol(sym, arrays))

    base10 = base_up10 / base_tot10 if base_tot10 else 0.5
    base60 = base_up60 / base_tot60 if base_tot60 else 0.5
    base10ret = base10ret_sum / base10ret_n if base10ret_n else 0.0

    _log.console(f"Universe: {n_used} symbols | base up-rate 10d={base10:.3f} 60d={base60:.3f} | "
          f"base avg 10d return={base10ret:+.3f}%\n")

    # ── Table A: knife-catch vs confirmed-recovery (the core hypothesis test) ──
    _log.console("=" * 74)
    _log.console("TABLE A — Entry-rule showdown: KNIFE-CATCH vs CONFIRMED-RECOVERY")
    _log.console("=" * 74)
    _log.console(f"{'rule':<12}{'n':>6}{'avg10%':>9}{'avg60%':>9}{'win10':>8}{'spread10':>10}{'z':>8}")
    rule_stats = {}
    for rule in ("knife", "confirmed"):
        recs = [r for r in all_recs if r["rule"] == rule]
        s = _agg(recs, base10, base10ret)
        rule_stats[rule] = s
        _log.console(f"{rule:<12}{s['n']:>6}{_f(s['avg10']):>9}{_f(s['avg60']):>9}"
              f"{s['win10']:>8.3f}{_f(s['spread10']):>10}{s['z']:>8.2f}")

    # ── Table B: confirmed-recovery forward return by depth x speed (x gap) ──
    _log.console("\n" + "=" * 74)
    _log.console("TABLE B — CONFIRMED-RECOVERY forward return by drawdown depth x speed")
    _log.console("=" * 74)
    _log.console(f"{'depth':<7}{'speed':<6}{'gap':<5}{'n':>6}{'avg10%':>9}{'avg60%':>9}{'win10':>8}{'z':>8}")
    conf = [r for r in all_recs if r["rule"] == "confirmed"]
    buckets = defaultdict(list)
    for r in conf:
        buckets[(r["depth"], r["speed"], r["gap"])].append(r)
    rows_out = []
    for key in sorted(buckets, key=lambda k: (k[0], k[1], k[2])):
        depth, speed, gap = key
        s = _agg(buckets[key], base10, base10ret)
        if s["n"] < 5:
            continue
        _log.console(f"{depth:<7}{speed:<6}{str(gap):<5}{s['n']:>6}{_f(s['avg10']):>9}"
              f"{_f(s['avg60']):>9}{s['win10']:>8.3f}{s['z']:>8.2f}")
        rows_out.append({"type": "confirmed_bucket", "depth": depth, "speed": speed,
                         "gap": gap, **s})

    # ── Parameter sweep: find the reversal-signal combination with the best edge ──
    knife = [r for r in all_recs if r["rule"] == "knife"]
    sweep_rows = _sweep(conf, knife, base10, base10ret)
    _log.console("\n" + "=" * 96)
    _log.console("PARAMETER SWEEP — top confirmed-recovery combinations "
          "(fully-qualifying first, then by excess-return t-stat)")
    _log.console("=" * 96)
    _log.console(f"{'depth<=':>8}{'speed<=':>8}{'range>=':>8}{'vol>=':>7}{'gap':>6}"
          f"{'n':>6}{'avg10%':>9}{'avg60%':>9}{'win10':>8}{'kwin':>7}{'spread':>8}{'t':>7}{'':>4}")
    for s in sweep_rows[:12]:
        mark = "  <-" if (s["spread10"] >= GATE_SPREAD and s["beats_knife"]) else ""
        _log.console(f"{s['depth_max']:>8.2f}{s['speed_max']:>8}{s['range_min']:>8.1f}"
              f"{s['vol_min']:>7.1f}{str(s['gap']):>6}{s['n']:>6}{_f(s['avg10']):>9}"
              f"{_f(s['avg60']):>9}{s['win10']:>8.3f}{s['knife_win10']:>7.3f}"
              f"{_f(s['spread10']):>8}{s['t_excess']:>7.2f}{mark:>4}")

    # ── The gate — the best combo that clears the economic floor AND beats the
    #    knife's reliability (waiting for confirmation must actually add value) ──
    qualified = [s for s in sweep_rows if s["spread10"] >= GATE_SPREAD
                 and s["n"] >= GATE_N and s["beats_knife"]]
    best = qualified[0] if qualified else None
    ks_win = best["knife_win10"] if best else rule_stats["knife"]["win10"]
    passes = bool(best) and best["t_excess"] >= GATE_T
    _log.console("\n" + "=" * 74)
    _log.console("ALPHA GATE (best swept combo that clears >=1% spread AND beats the knife)")
    _log.console("=" * 74)
    if best:
        _log.console(f"  combo: depth<={best['depth_max']:.2f}  speed<={best['speed_max']}  "
              f"range>={best['range_min']:.1f}  vol>={best['vol_min']:.1f}  gap={best['gap']}")
        _log.console(f"  n={best['n']} (need >= {GATE_N})")
        _log.console(f"  10d spread={_f(best['spread10'])}% (need >= {GATE_SPREAD}%)")
        _log.console(f"  excess-return t={best['t_excess']:.2f} (need >= {GATE_T})")
        _log.console(f"  confirmed win10 {best['win10']:.3f} > knife win10 {ks_win:.3f} "
              f"(on the same depth/speed/gap footprint)")
    else:
        _log.console("  No combo clears the >=1% spread floor AND beats the knife on win-rate.")
        if sweep_rows:
            t = sweep_rows[0]
            _log.console(f"  closest by t: depth<={t['depth_max']:.2f} speed<={t['speed_max']} "
                  f"range>={t['range_min']:.1f} gap={t['gap']} | spread={_f(t['spread10'])}% "
                  f"win {t['win10']:.3f} vs knife {t['knife_win10']:.3f}")
    _log.console(f"\n  ==> {'PASS — proceed to Phase 2 with the above combo' if passes else 'FAIL — park the factor'}")

    # ── Persist (list-of-rows schema, like digit_sum_study.json) ──
    payload = {
        "meta": {"base_up10": round(base10, 4), "base_up60": round(base60, 4),
                 "base_avg10": round(base10ret, 3), "symbols": n_used,
                 "params": {"LOOKBACK": LOOKBACK, "DEPTH_MIN": DEPTH_MIN,
                            "SPEED_MAX": SPEED_MAX, "GAP_PCT": GAP_PCT},
                 "gate": {"pass": passes, "best_combo": best, "knife_win10": ks_win}},
        "rule_stats": rule_stats,
        "buckets": rows_out,
        "sweep": sweep_rows,
        "signals": all_recs,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved {len(all_recs)} signals + stats to {OUT_FILE}")
    return passes


def _f(x):
    return f"{x:+.2f}" if isinstance(x, (int, float)) else "  -  "


if __name__ == "__main__":
    run()
