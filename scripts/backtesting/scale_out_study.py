"""
Scale-Out ("Bank-As-You-Go") Study — Rule B of the Defensive Risk-Management Overlay.

Backtest-first discovery for a per-position capital-preservation primitive: instead
of holding a full lot to a single trailing stop, BANK a fraction of the position at
ATR-scaled profit tiers and let the RESIDUAL trail the same stop. The doc's claim
(plans/options-challenge-strategy.md) is *reliability* — locking gains before a
reversal — not higher mean return. So the gate is deliberately asymmetric:

    reduced DOWNSIDE / variance at an ACCEPTABLE mean cost — NOT a mean-return edge.

Selling a fraction into strength mechanically caps the right tail (you give up some
upside on pure runners), so a naive mean-return test would REJECT a rule that is
nonetheless correct capital preservation. This study measures whether the left-tail
protection is real, statistically significant, and worth the upside given up.

Method — a PAIRED simulation with NO LOOK-AHEAD. At each sampled entry bar i we open
a notional 1-share-equivalent long at close[i], fix ATR from bars[:i+1], and follow
the SAME forward path under two exit policies:

    HOLD  (baseline, = today's engine) — the whole lot trails a chandelier stop
          (high-water-since-entry - MULT*ATR, ratcheting up only), exits when a bar's
          low pierces the stop, else marks out at close[i+MAX_HOLD].
    SCALE (candidate) — identical trailing stop on the RESIDUAL, but bank BANK_FRACS
          of the lot the first time price trades through each TIER_MULT*ATR profit
          tier (filled at the tier price — a realistic resting limit). The residual
          trails to the same stop. Blended return = banked tiers + residual exit.

Both policies see the same bars and the same stop mechanic; the ONLY difference is
banking. So:
    - immediate losers (never reach tier 1)      -> SCALE == HOLD  (diff 0)
    - run-up then reversal to stop               -> SCALE  > HOLD  (banked the peak)
    - pure runner to MAX_HOLD                     -> SCALE  < HOLD  (capped upside)

Gate (matches the house |t| >= 1.96, n >= 20 discipline, but on the DOWNSIDE):
    1. n_downside >= GATE_N episodes where HOLD lost money,
    2. paired t on (SCALE - HOLD) over those downside episodes >= GATE_T (banking
       demonstrably cuts realized losses),
    3. variance reduced overall (std_scale < std_hold),
    4. acceptable mean cost — the mean upside given up <= COST_CAP.
Park with a documented null (like project_gann_sq9) if it fails.

Usage:
    python scripts/backtesting/scale_out_study.py

Writes Data/scale_out_study.json. Recalibrate MONTHLY (like digit_sum_study) after
the OHLCV top-up — the tier/fraction geometry is derived from 25+ yr x 500+ symbols
and does not move week-to-week.
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

_log = _get_logger("scale_out")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# OHLCV cache is a large gitignored dir that lives in the primary working tree; a
# _wt_ worktree won't have it, so allow an env override to point at the shared cache.
OHLCV    = Path(os.environ.get("AETHER_OHLCV_DIR") or (BASE_DIR / "Data" / "Symbol_full"))
OUT_FILE = BASE_DIR / "Data" / "scale_out_study.json"

# ── Simulation parameters ──
ATR_PERIOD   = 14
STOP_MULT    = 2.5    # chandelier trailing-stop ATR multiple (rules.atr_multiplier default)
MAX_HOLD     = 60     # bars to follow an entry forward (mark out at the horizon)
ENTRY_STRIDE = 2      # sample every Nth bar as an entry (halves runtime; unbiased for the paired diff)
MIN_ATR_PCT  = 0.005  # ignore entries where ATR is < 0.5% of price (degenerate / illiquid)
WARMUP       = ATR_PERIOD + 2

# ── The scale-out ladder under test (bank a fraction at each ATR profit tier) ──
#    Default banks 30% at +1.5xATR and 30% at +3.0xATR, trailing the remaining 40%.
TIER_MULTS = [1.5, 3.0]     # profit tiers, in ATR multiples above entry
BANK_FRACS = [0.30, 0.30]   # fraction of the ORIGINAL lot banked at each tier

# ── Gate ──
GATE_T   = 1.96   # paired t on downside episodes (banking must cut realized losses)
GATE_N   = 20     # minimum downside episodes
COST_CAP = 2.0    # acceptable mean upside given up, in percentage points


def _bars(path):
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    dates = sorted(ts.keys())
    if len(dates) < WARMUP + MAX_HOLD + 5:
        return None
    o, h, l, c = [], [], [], []
    for d in dates:
        b = ts[d]
        try:
            o.append(float(b["1. open"])); h.append(float(b["2. high"]))
            l.append(float(b["3. low"]));  c.append(float(b["4. close"]))
        except (KeyError, ValueError):
            return None
    return o, h, l, c


def _atr_at(h, l, c, i, period=ATR_PERIOD):
    """Simple-average True Range over the `period` bars ending at i (no look-ahead).
    Mirrors risk_utils._atr_from_series on the slice bars[:i+1]."""
    lo = max(1, i - period + 1)
    trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1]))
           for j in range(lo, i + 1)]
    return sum(trs) / len(trs) if trs else None


def _simulate(o, h, l, c, i, atr):
    """Return (hold_ret, scale_ret) in % for an entry at close[i].

    Both policies share one ratcheting chandelier stop; SCALE additionally banks
    BANK_FRACS at entry + TIER_MULT*ATR. Fills: stop at the stop price unless the
    bar GAPS below it (then at the open); tiers at the tier price (resting limit).
    """
    entry = c[i]
    if entry <= 0:
        return None
    floor_stop = entry - STOP_MULT * atr
    high_water = h[i]
    stop = floor_stop

    banked_frac = 0.0
    banked_ret = 0.0                       # realized % return contribution from tiers
    next_tier = 0
    tier_prices = [entry + m * atr for m in TIER_MULTS]

    end = min(i + MAX_HOLD, len(c) - 1)
    hold_ret = scale_ret = None

    for j in range(i + 1, end + 1):
        # ratchet the stop up on new highs (chandelier, never lowers)
        high_water = max(high_water, h[j])
        stop = max(stop, high_water - STOP_MULT * atr)

        # SCALE: bank each tier the first time the bar trades through it
        while next_tier < len(tier_prices) and h[j] >= tier_prices[next_tier]:
            frac = BANK_FRACS[next_tier]
            banked_ret += frac * (tier_prices[next_tier] - entry) / entry * 100.0
            banked_frac += frac
            next_tier += 1

        # stop hit? (gap-aware fill)
        if l[j] <= stop:
            exit_px = min(o[j], stop) if o[j] < stop else stop
            r = (exit_px - entry) / entry * 100.0
            hold_ret = r
            scale_ret = banked_ret + (1.0 - banked_frac) * r
            return hold_ret, scale_ret

    # never stopped — mark out the residual at the horizon close
    r = (c[end] - entry) / entry * 100.0
    hold_ret = r
    scale_ret = banked_ret + (1.0 - banked_frac) * r
    return hold_ret, scale_ret


def _stats(pairs):
    """pairs = list of (hold, scale). Returns the full stat block + gate inputs."""
    n = len(pairs)
    holds = [p[0] for p in pairs]
    scales = [p[1] for p in pairs]
    mean_h = sum(holds) / n
    mean_s = sum(scales) / n
    var_h = sum((x - mean_h) ** 2 for x in holds) / (n - 1) if n > 1 else 0.0
    var_s = sum((x - mean_s) ** 2 for x in scales) / (n - 1) if n > 1 else 0.0
    sd_h, sd_s = math.sqrt(var_h), math.sqrt(var_s)

    # left tail (5th percentile) — the reliability payoff
    sh, ss = sorted(holds), sorted(scales)
    p5 = max(0, int(0.05 * n) - 1)
    tail_h, tail_s = sh[p5], ss[p5]

    # downside episodes: where HOLD lost money — did banking cut the loss?
    down = [(hd, sc) for hd, sc in pairs if hd < 0]
    n_down = len(down)
    diffs = [sc - hd for hd, sc in down]          # >= 0 when banking helped
    mean_d = sum(diffs) / n_down if n_down else 0.0
    t_down = 0.0
    if n_down >= 2:
        vd = sum((x - mean_d) ** 2 for x in diffs) / (n_down - 1)
        sdd = math.sqrt(vd)
        if sdd > 0:
            t_down = mean_d / (sdd / math.sqrt(n_down))

    cost = mean_h - mean_s                          # mean upside given up (pp)
    return {
        "n": n,
        "mean_hold": round(mean_h, 3), "mean_scale": round(mean_s, 3),
        "std_hold": round(sd_h, 3), "std_scale": round(sd_s, 3),
        "var_ratio": round(var_s / var_h, 4) if var_h > 0 else None,
        "tail5_hold": round(tail_h, 3), "tail5_scale": round(tail_s, 3),
        "n_downside": n_down,
        "mean_downside_benefit": round(mean_d, 3),
        "t_downside": round(t_down, 2),
        "mean_cost": round(cost, 3),
    }


def run():
    files = sorted(OHLCV.glob("*_daily.json"))
    _log.console(f"Scanning {len(files)} symbols for scale-out vs hold-to-stop...\n")

    pairs = []
    per_symbol = defaultdict(list)
    n_used = 0
    for path in files:
        sym = path.stem.replace("_daily", "")
        try:
            if instruments.is_excluded(sym):
                continue
        except Exception:
            pass
        b = _bars(path)
        if not b:
            continue
        o, h, l, c = b
        n = len(c)
        n_used += 1
        for i in range(WARMUP, n - MAX_HOLD, ENTRY_STRIDE):
            atr = _atr_at(h, l, c, i)
            if not atr or atr <= 0 or atr < MIN_ATR_PCT * c[i]:
                continue
            res = _simulate(o, h, l, c, i, atr)
            if res is None:
                continue
            pairs.append(res)
            per_symbol[sym].append(res)

    if not pairs:
        _log.console("No episodes — aborting.")
        return False

    s = _stats(pairs)

    _log.console("=" * 74)
    _log.console("SCALE-OUT vs HOLD-TO-STOP  (paired, same bars, same trailing stop)")
    _log.console("=" * 74)
    _log.console(f"  ladder: bank {BANK_FRACS} of the lot at {TIER_MULTS} xATR, trail the rest @ {STOP_MULT}xATR")
    _log.console(f"  universe: {n_used} symbols | {s['n']} paired episodes\n")
    _log.console(f"  {'':<18}{'HOLD':>12}{'SCALE':>12}")
    _log.console(f"  {'mean return %':<18}{s['mean_hold']:>12.3f}{s['mean_scale']:>12.3f}")
    _log.console(f"  {'std of returns':<18}{s['std_hold']:>12.3f}{s['std_scale']:>12.3f}")
    _log.console(f"  {'5th-pctile %':<18}{s['tail5_hold']:>12.3f}{s['tail5_scale']:>12.3f}")
    _log.console(f"\n  variance ratio (scale/hold): {s['var_ratio']}  (< 1 = less variance)")
    _log.console(f"  mean cost (upside given up): {s['mean_cost']:+.3f} pp  (cap {COST_CAP})")
    _log.console(f"\n  DOWNSIDE episodes (HOLD < 0): n={s['n_downside']}")
    _log.console(f"    mean loss cut by banking:  {s['mean_downside_benefit']:+.3f} pp")
    _log.console(f"    paired t:                  {s['t_downside']:.2f}  (need >= {GATE_T})")

    # ── Gate ──
    g_n     = s["n_downside"] >= GATE_N
    g_t     = s["t_downside"] >= GATE_T and s["mean_downside_benefit"] > 0
    g_var   = s["var_ratio"] is not None and s["var_ratio"] < 1.0
    g_cost  = s["mean_cost"] <= COST_CAP
    passes  = g_n and g_t and g_var and g_cost

    _log.console("\n" + "=" * 74)
    _log.console("ALPHA GATE — reduced downside/variance at acceptable mean cost")
    _log.console("=" * 74)
    _log.console(f"  [{'x' if g_n else ' '}] downside n >= {GATE_N}          (n={s['n_downside']})")
    _log.console(f"  [{'x' if g_t else ' '}] downside t >= {GATE_T}         (t={s['t_downside']}, benefit={s['mean_downside_benefit']:+.3f}pp)")
    _log.console(f"  [{'x' if g_var else ' '}] variance reduced          (ratio={s['var_ratio']})")
    _log.console(f"  [{'x' if g_cost else ' '}] mean cost <= {COST_CAP}pp        (cost={s['mean_cost']:+.3f}pp)")
    _log.console(f"\n  ==> {'PASS — proceed to Rule B implementation' if passes else 'FAIL / PARK — banking not worth the cost'}")

    payload = {
        "meta": {
            "symbols": n_used, "episodes": s["n"],
            "params": {"ATR_PERIOD": ATR_PERIOD, "STOP_MULT": STOP_MULT,
                       "MAX_HOLD": MAX_HOLD, "ENTRY_STRIDE": ENTRY_STRIDE,
                       "TIER_MULTS": TIER_MULTS, "BANK_FRACS": BANK_FRACS},
            "gate": {"pass": passes, "GATE_T": GATE_T, "GATE_N": GATE_N,
                     "COST_CAP": COST_CAP,
                     "checks": {"downside_n": g_n, "downside_t": g_t,
                                "variance_reduced": g_var, "cost_acceptable": g_cost}},
        },
        "stats": s,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved stats to {OUT_FILE}")
    return passes


if __name__ == "__main__":
    run()
