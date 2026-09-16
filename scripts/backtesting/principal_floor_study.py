"""
Principal-Floor / Cushion-Ratchet Study — Rule A of the Defensive Risk-Management Overlay.

Backtest-first discovery for a PORTFOLIO-level, account-agnostic capital-preservation
governor. As cumulative *realized* gains accrue, ratchet the enforced cash-cushion floor
UP — locking a growing share of the account into cash so booked profit cannot be fully
given back into a later drawdown. It generalizes the per-position breakeven-lock to the
whole portfolio and reinforces CLAUDE.md's >=50%-cash mandate.

WHY THIS DESIGN SURVIVES THE PR-49 CRITIQUE (reviews/PR-49-barbell-protocol.md, finding
D2 — "'cap loss at profit margin' is dynamically false ... mental accounting, not a
mechanism"):
  1. It keys STRICTLY off REALIZED gains — cash actually in hand (literal house money),
     never paper/mark-to-market gains, so the protected capital genuinely exists.
  2. It is stated in % OF EQUITY, not a fixed $ target, so it reads identically on a $10k
     or a $10M account (the generalization bar PR-49 sets).
  3. It is a MONOTONE ratchet on the high-water of realized gains — a negative-feedback
     governor that raises the cash floor as you win and NEVER pushes more into risk as you
     lose (the opposite of the martingale spiral PR-49 D1 flags).
  4. It is DOWNSTREAM of Rule B: Rule B is what realizes (banks) the gains this floor then
     protects. Before any gain is booked the floor == the base cushion, so it costs nothing
     until the account is already ahead — the cost is asymmetric, borne only on house money.

Because a drifting-up market has positive expectancy, holding extra cash MUST cost some
terminal upside — so, exactly like the Rule B scale-out gate, a naive mean-return test
would wrongly reject a rule whose whole purpose is reliability. The gate is therefore
asymmetric: on the paths that GOT AHEAD (where the ratchet actually engages), does the
floor cut the give-back / max drawdown with |t| >= 1.96, at an ACCEPTABLE upside cost?
If it does not, Rule A PARKS with a documented null (like project_gann_sq9) — a valid,
honest outcome the plan explicitly anticipates.

Method — a PAIRED portfolio Monte-Carlo, account-agnostic (E0 normalized to 1.0):
  * Build an empirical single-trade return pool once: ATR-stopped forward returns sampled
    with NO LOOK-AHEAD across the full universe (real OHLCV, split-adjusted upstream), so
    the return distribution is the system's own, not a Gaussian assumption.
  * For each simulated account, draw one sequence of K trades from the pool and run TWO
    cushion policies on the SAME draws (paired):
        BASE  — constant cushion floor = BASE_CUSHION (today's flat >=50%-cash mandate).
        FLOOR — cushion floor = min(CAP, BASE_CUSHION + RATE * peak_realized_gain_pct),
                the ratchet under test.
    Each round stakes DEPLOY_FRAC of the deployable (non-cushion) equity into the next
    drawn trade; realized P&L updates equity, cash, and the realized-gain high-water.
  * Record per path: terminal equity, max drawdown (peak-to-trough), max give-back from the
    equity peak, and ruin (equity <= RUIN_LEVEL).

Gate (house |t| >= 1.96, n >= 20 discipline, applied to the ahead-cohort downside):
  1. n_ahead >= GATE_N paths where the ratchet engaged (peak gain >= ENGAGE_PCT),
  2. paired t on (drawdown_BASE - drawdown_FLOOR) over the ahead cohort >= GATE_T
     (the floor demonstrably cuts drawdown where it applies),
  3. ruin probability not increased (floor <= base),
  4. acceptable upside cost — mean terminal equity given up <= COST_CAP (as a fraction).

Usage:
    python scripts/backtesting/principal_floor_study.py

Writes Data/principal_floor_study.json. Recalibrate MONTHLY after the OHLCV top-up.
"""

import json
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import instruments
from aether_logger import get_logger as _get_logger

_log = _get_logger("principal_floor")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# OHLCV cache is a large gitignored dir that lives in the primary working tree; a _wt_
# worktree won't have it, so allow an env override to point at the shared cache.
OHLCV    = Path(os.environ.get("AETHER_OHLCV_DIR") or (BASE_DIR / "Data" / "Symbol_full"))
OUT_FILE = BASE_DIR / "Data" / "principal_floor_study.json"

# ── Trade-return pool (single-trade, ATR-stopped, no look-ahead) ──
ATR_PERIOD   = 14
STOP_MULT    = 2.5     # ATR stop multiple (rules.atr_multiplier default)
TRADE_HOLD   = 20      # bars a sampled "trade" is held before marking out
ENTRY_STRIDE = 5       # sample every Nth bar as a trade entry
MIN_ATR_PCT  = 0.005   # ignore degenerate/illiquid entries (ATR < 0.5% of price)
WARMUP       = ATR_PERIOD + 2

# ── Portfolio Monte-Carlo ──
N_PATHS     = 6000     # simulated accounts
K_TRADES    = 120      # trades per account
DEPLOY_FRAC = 0.34     # fraction of deployable (non-cushion) equity staked per trade
RUIN_LEVEL  = 0.50     # equity <= 50% of start = ruin
SEED        = 20260911

# ── The cushion ratchet under test ──
BASE_CUSHION = 0.50    # baseline enforced cash floor (the >=50% mandate)
RATCHET_RATE = 0.50    # +0.50 of cushion per 1.0 of realized-gain/E0 (i.e. +5pp per +10% booked)
CUSHION_CAP  = 0.80    # never lock more than 80% into cash (always keep some risk-on capacity)
ENGAGE_PCT   = 0.10    # "ahead cohort" = paths whose peak realized gain reached >= +10% of E0

# ── Gate ──
GATE_T   = 1.96
GATE_N   = 20
COST_CAP = 0.05        # acceptable mean terminal-equity given up, as a fraction of E0


def _bars(path):
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    dates = sorted(ts.keys())
    if len(dates) < WARMUP + TRADE_HOLD + 5:
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
    """Simple-average True Range over the `period` bars ending at i (no look-ahead)."""
    lo = max(1, i - period + 1)
    trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1]))
           for j in range(lo, i + 1)]
    return sum(trs) / len(trs) if trs else None


def _trade_return(o, h, l, c, i, atr):
    """One ATR-stopped long trade opened at close[i]; return realized fraction.
    Fixed stop at entry - STOP_MULT*ATR (gap-aware fill); else mark out at the horizon
    close. Pure price, no look-ahead beyond the held window."""
    entry = c[i]
    if entry <= 0:
        return None
    stop = entry - STOP_MULT * atr
    end = min(i + TRADE_HOLD, len(c) - 1)
    for j in range(i + 1, end + 1):
        if l[j] <= stop:
            exit_px = min(o[j], stop) if o[j] < stop else stop
            return (exit_px - entry) / entry
    return (c[end] - entry) / entry


def build_pool():
    files = sorted(OHLCV.glob("*_daily.json"))
    _log.console(f"Building trade-return pool from {len(files)} symbols...\n")
    pool = []
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
        for i in range(WARMUP, n - TRADE_HOLD, ENTRY_STRIDE):
            atr = _atr_at(h, l, c, i)
            if not atr or atr <= 0 or atr < MIN_ATR_PCT * c[i]:
                continue
            r = _trade_return(o, h, l, c, i, atr)
            if r is not None:
                pool.append(r)
    return pool, n_used


def _cushion_floor(peak_gain_pct):
    """The ratchet: base cushion + RATE * high-water realized gain, capped."""
    return min(CUSHION_CAP, BASE_CUSHION + RATCHET_RATE * max(0.0, peak_gain_pct))


def _run_account(draws, ratchet):
    """Run one account over a fixed trade sequence. Returns (terminal_eq, max_dd,
    max_giveback, ruined, peak_gain_pct). max_dd = worst peak-to-trough on the equity
    curve; max_giveback = the same but only measured after the account first went ahead."""
    eq = 1.0                 # E0 normalized to 1.0 (account-agnostic)
    realized_gain = 0.0      # cumulative realized P&L / E0
    peak_gain = 0.0          # high-water of realized_gain (drives the ratchet)
    peak_eq = 1.0
    max_dd = 0.0
    max_giveback = 0.0
    ruined = False
    for r in draws:
        floor = _cushion_floor(peak_gain) if ratchet else BASE_CUSHION
        stake = eq * (1.0 - floor) * DEPLOY_FRAC
        pnl = stake * r
        eq += pnl
        realized_gain += pnl              # every trade is realized (cash settles each round)
        peak_gain = max(peak_gain, realized_gain)
        peak_eq = max(peak_eq, eq)
        dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0.0
        max_dd = max(max_dd, dd)
        if peak_gain >= ENGAGE_PCT:       # give-back only counts once ahead
            max_giveback = max(max_giveback, dd)
        if eq <= RUIN_LEVEL:
            ruined = True
    return eq, max_dd, max_giveback, ruined, peak_gain


def _t_paired(diffs):
    n = len(diffs)
    if n < 2:
        return 0.0, 0.0
    m = sum(diffs) / n
    v = sum((x - m) ** 2 for x in diffs) / (n - 1)
    sd = math.sqrt(v)
    t = m / (sd / math.sqrt(n)) if sd > 0 else 0.0
    return m, t


def run():
    pool, n_used = build_pool()
    if len(pool) < 1000:
        _log.console(f"Trade pool too small ({len(pool)}) — aborting.")
        return False
    pool_mean = sum(pool) / len(pool)
    _log.console(f"  pool: {len(pool):,} trades from {n_used} symbols | mean {pool_mean*100:+.3f}%/trade\n")

    rng = random.Random(SEED)
    base_terms, floor_terms = [], []
    dd_diffs_all, gb_diffs_ahead = [], []
    base_ruin = floor_ruin = 0
    n_ahead = 0

    for _ in range(N_PATHS):
        draws = [pool[rng.randrange(len(pool))] for _ in range(K_TRADES)]
        b_eq, b_dd, b_gb, b_ruin, _pk = _run_account(draws, ratchet=False)
        f_eq, f_dd, f_gb, f_ruin, f_pk = _run_account(draws, ratchet=True)
        base_terms.append(b_eq); floor_terms.append(f_eq)
        dd_diffs_all.append(b_dd - f_dd)          # >0 when the floor cut drawdown
        base_ruin += b_ruin; floor_ruin += f_ruin
        if f_pk >= ENGAGE_PCT:                     # ratchet actually engaged on this path
            n_ahead += 1
            gb_diffs_ahead.append(b_gb - f_gb)     # >0 when the floor cut give-back of gains

    mean_base_term = sum(base_terms) / N_PATHS
    mean_floor_term = sum(floor_terms) / N_PATHS
    mean_dd_cut, t_dd = _t_paired(dd_diffs_all)
    mean_gb_cut, t_gb = _t_paired(gb_diffs_ahead)
    upside_cost = mean_base_term - mean_floor_term      # terminal equity given up (fraction of E0)

    s = {
        "pool_trades": len(pool), "pool_mean_pct": round(pool_mean * 100, 4),
        "n_paths": N_PATHS, "k_trades": K_TRADES, "n_ahead": n_ahead,
        "mean_terminal_base": round(mean_base_term, 4),
        "mean_terminal_floor": round(mean_floor_term, 4),
        "upside_cost": round(upside_cost, 4),
        "mean_dd_cut": round(mean_dd_cut, 5), "t_dd": round(t_dd, 2),
        "mean_giveback_cut_ahead": round(mean_gb_cut, 5), "t_giveback_ahead": round(t_gb, 2),
        "ruin_base": base_ruin, "ruin_floor": floor_ruin,
    }

    _log.console("=" * 74)
    _log.console("PRINCIPAL-FLOOR vs FLAT-CUSHION  (paired portfolio Monte-Carlo)")
    _log.console("=" * 74)
    _log.console(f"  ratchet: cushion = min({CUSHION_CAP}, {BASE_CUSHION} + {RATCHET_RATE}*peak_realized_gain)")
    _log.console(f"  {N_PATHS:,} accounts x {K_TRADES} trades | deploy {DEPLOY_FRAC} of free equity/trade\n")
    _log.console(f"  {'':<26}{'BASE':>12}{'FLOOR':>12}")
    _log.console(f"  {'mean terminal equity':<26}{s['mean_terminal_base']:>12.4f}{s['mean_terminal_floor']:>12.4f}")
    _log.console(f"  {'ruin count (<=0.5xE0)':<26}{s['ruin_base']:>12}{s['ruin_floor']:>12}")
    _log.console(f"\n  upside cost (terminal given up): {s['upside_cost']:+.4f}  (cap {COST_CAP})")
    _log.console(f"  all-paths drawdown cut:          {s['mean_dd_cut']:+.5f}  t={s['t_dd']:.2f}")
    _log.console(f"\n  AHEAD cohort (peak gain >= {ENGAGE_PCT:.0%}): n={s['n_ahead']}")
    _log.console(f"    give-back cut by the floor:    {s['mean_giveback_cut_ahead']:+.5f}  t={s['t_giveback_ahead']:.2f}  (need >= {GATE_T})")

    # ── Gate ──
    g_n    = n_ahead >= GATE_N
    g_t    = t_gb >= GATE_T and mean_gb_cut > 0
    g_ruin = floor_ruin <= base_ruin
    g_cost = upside_cost <= COST_CAP
    passes = g_n and g_t and g_ruin and g_cost

    _log.console("\n" + "=" * 74)
    _log.console("ALPHA GATE — cut give-back/drawdown of BANKED gains at acceptable upside cost")
    _log.console("=" * 74)
    _log.console(f"  [{'x' if g_n else ' '}] ahead-cohort n >= {GATE_N}     (n={n_ahead})")
    _log.console(f"  [{'x' if g_t else ' '}] give-back t >= {GATE_T}      (t={s['t_giveback_ahead']}, cut={s['mean_giveback_cut_ahead']:+.5f})")
    _log.console(f"  [{'x' if g_ruin else ' '}] ruin not increased       (base={base_ruin}, floor={floor_ruin})")
    _log.console(f"  [{'x' if g_cost else ' '}] upside cost <= {COST_CAP}       (cost={s['upside_cost']:+.4f})")
    _log.console(f"\n  ==> {'PASS — implement Rule A principal-floor' if passes else 'FAIL / PARK — floor not worth the upside cost'}")

    payload = {
        "meta": {
            "symbols": n_used, "pool_trades": len(pool),
            "params": {"BASE_CUSHION": BASE_CUSHION, "RATCHET_RATE": RATCHET_RATE,
                       "CUSHION_CAP": CUSHION_CAP, "ENGAGE_PCT": ENGAGE_PCT,
                       "N_PATHS": N_PATHS, "K_TRADES": K_TRADES,
                       "DEPLOY_FRAC": DEPLOY_FRAC, "STOP_MULT": STOP_MULT,
                       "TRADE_HOLD": TRADE_HOLD, "SEED": SEED},
            "gate": {"pass": passes, "GATE_T": GATE_T, "GATE_N": GATE_N,
                     "COST_CAP": COST_CAP,
                     "checks": {"ahead_n": g_n, "giveback_t": g_t,
                                "ruin_not_increased": g_ruin, "cost_acceptable": g_cost}},
        },
        "stats": s,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved stats to {OUT_FILE}")
    return passes


if __name__ == "__main__":
    run()
