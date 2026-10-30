"""
Portfolio-level backtest — equity curve, CAGR, max drawdown, Sharpe (OceanView prereq #5).

Every other study in scripts/backtesting/ measures per-observation forward returns; none
answers "what would a portfolio run on these scores have done?". This replays the
historical S10/L60 scores (reconstructed by backtest_ratings.process_symbol from the
Chaikin cache) through a STANDALONE simulator built on AETHER's core rules, and compares
it with buy-and-hold:

  aether_rules   regime profile from SPY L60 (>2 AGGRESSIVE, <-2 DEFENSIVE, else
                 BALANCED, as determine_profile), the profile's max_positions /
                 max_allocation_pct / min_score_threshold / cash_buffer_pct /
                 atr_multiplier (get_strategy_rules) + slot expansion
                 (determine_max_positions); exits via sell_rules.exit_decision (hard ATR
                 stop, S10+L60<0 soft exit, winner protection); each buy trimmed so a
                 stop-out loses at most CFG.system_max_trade_risk_pct of equity (the
                 live per-trade loss cap in _execute_buys).
  aether_no_cap  aether_rules without the per-trade loss cap: what the cap costs or buys.
  signal_only    the same entry ranking with a fixed BALANCED threshold, full equal-weight
                 deployment, no stop and no cash buffer; exits on the soft rule only.
                 aether_rules minus signal_only = what the risk overlay costs or buys.
  spy_buy_hold   SPY bought on the first day and held.
  ew_buy_hold    equal dollars in every eligible universe symbol on the first day, held.

--attribution adds one variant per rule in RULES: aether_rules with only that rule
switched off (simulate(disable={rule})), so each row shows what that one rule adds.

Every run also reports the stop-out tail (R&D #54a): for each variant, how often a stop
filled below the stop price (a gap through it) and the loss per stopped trade as % of
equity, against the per-trade loss cap. Gate (set before the first run): the cap needs
work if the 95th-percentile stop-out loss exceeds STOP_TAIL_P95_GATE (1.5%) of equity or
the worst exceeds STOP_TAIL_WORST_GATE (3%).

--hurst adds the R&D #54b variants (HURST_VARIANTS): aether_rules plus "skip a new entry
when the symbol's rolling Hurst exponent is below X". With --split it also prints the
pre-registered verdict: the variant with the best train Sharpe passes only if its test
Sharpe beats aether_rules and its test max drawdown is no deeper.

--robust N --split DATE (R&D #62) re-runs every rule variant on N perturbed paths: each
draw keeps a random ROBUST_FRAC of the non-SPY symbols and starts 0..ROBUST_MAX_OFFSET
trading days late (seed ROBUST_SEED). It reports median and 10th-90th percentile Sharpe,
CAGR and max drawdown, and each variant's win rate against aether_rules (share of draws
where its Sharpe is higher on the same path). Robustly better = win rate >= ROBUST_WIN on
both halves; robustly worse = <= ROBUST_LOSE on both; else inconclusive.

--split DATE (implies --attribution) is an out-of-sample check: every variant runs again
from fresh cash on the days before DATE (train) and on DATE onward (test). The rule
variant with the best train Sharpe is the one "picked"; its test result and rank among
the rule variants show whether the pick holds up on days it never saw.

Execution: scores dated day t (close data) trigger orders filled at day t+1's OPEN —
no look-ahead. Hard stops fill intraday at min(open, stop). Prices are split-adjusted.
Costs: --cost-bps per side (default 10).

NOT simulated (live-only rules): strategic overrides, bottom-confirmed buys, the SPY-RSP
breadth downgrade, scarcity-core sizing, scale-outs/covered calls, rotation. The score
reconstruction also omits the live digit_sum, setup_ok and vrecovery_score fields.
The universe is TODAY's symbol list, so results carry survivorship bias (optimistic).
Synthetic placeholder bars (provisional / O=H=L=C on zero volume) never price a fill.
Loss-cap differences from live: a buy with no prior ATR gets no stop here, so no cap
(live falls back to an 8% stop and caps against it); a trimmed buy may land under
MIN_TICKET_USD (live rejects it only below 1 share); quantities are fractional.

The score panel (~6s/symbol) is cached in Data/portfolio_backtest_panel.json, keyed by a
fingerprint of the scoring code + calibration studies; --rebuild forces a recompute.
Output: Data/portfolio_backtest.json (config, metrics, equity curves, trades).

Usage:
    python scripts/backtesting/portfolio_backtest.py [--min-year 2023] [--workers 8]
                                                     [--cost-bps 10] [--max-symbols N]
                                                     [--rebuild] [--attribution]
                                                     [--split 2025-01-01] [--hurst]
                                                     [--robust 50]
"""
import argparse
import datetime
import glob
import hashlib
import json
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import backtest_ratings as _br
import instruments
from aether.config import CFG
from aether.risk_utils import risk_capped_cash, split_adjust_ohlcv
from patterns import _wilder_atr
from sell_rules import exit_decision, soft_exit, winner_protected

DATA_DIR = os.path.join(BASE_DIR, "Data")
PANEL_PATH = os.path.join(DATA_DIR, "portfolio_backtest_panel.json")
OUT_PATH = os.path.join(DATA_DIR, "portfolio_backtest.json")

# Mirrors ai_portfolio_game.get_strategy_rules for the regime each profile runs in here
# (profile == regime, so AGGRESSIVE is always the bullish zero-buffer case).
# tests/test_portfolio_backtest.py asserts parity with the live function.
PROFILE_RULES = {
    "AGGRESSIVE": {"max_positions": 6, "max_allocation_pct": 0.15, "atr_multiplier": 3.5,
                   "min_score_threshold": 2.0, "cash_buffer_pct": 0.0},
    "BALANCED":   {"max_positions": 5, "max_allocation_pct": 0.15, "atr_multiplier": 2.5,
                   "min_score_threshold": 5.0, "cash_buffer_pct": 0.20},
    "DEFENSIVE":  {"max_positions": 3, "max_allocation_pct": 0.10, "atr_multiplier": 1.5,
                   "min_score_threshold": 10.0, "cash_buffer_pct": 0.50},
}
REGIME_BAND = 2.0          # determine_profile: SPY L60 > +2 AGGRESSIVE, < -2 DEFENSIVE
SLOT_EXPAND_CASH = 0.15    # determine_max_positions: add slots while cash ratio > 15%
MIN_TICKET_USD = 100.0
INITIAL_EQUITY = 100_000.0
STOP_TAIL_P95_GATE = 1.5     # % of equity (R&D #54a, pre-registered)
STOP_TAIL_WORST_GATE = 3.0
# R&D #54b (pre-registered 2026-10-29): aggregated-variance Hurst over the trailing
# HURST_WINDOW daily log returns; variant -> (bar key, skip entries below this H).
# R&D #62 robustness check (pre-registered 2026-10-30).
ROBUST_SEED = 62
ROBUST_FRAC = 0.8
ROBUST_MAX_OFFSET = 60
ROBUST_WIN = 0.8
ROBUST_LOSE = 0.2
HURST_WINDOW = 120
HURST_LAGS = (1, 2, 4, 8, 16)
HURST_VARIANTS = {
    "aether_hurst_r45": ("hurst", 0.45),
    "aether_hurst_r50": ("hurst", 0.50),
    "aether_hurst_r55": ("hurst", 0.55),
    "aether_hurst_v50": ("hurst_abs", 0.50),
    "aether_hurst_v60": ("hurst_abs", 0.60),
}
# Rules simulate(disable=...) can switch off one at a time (--attribution).
RULES = ("stop", "soft_exit", "winner_protection", "regime", "cash_buffer",
         "slot_expansion", "loss_cap")


def _out(line: str = "") -> None:
    """Report table line — intentional CLI output, unprefixed."""
    sys.stdout.write(line + "\n")


def profile_for(spy_l60) -> str:
    if spy_l60 is None:
        return "BALANCED"
    if spy_l60 > REGIME_BAND:
        return "AGGRESSIVE"
    if spy_l60 < -REGIME_BAND:
        return "DEFENSIVE"
    return "BALANCED"


def expanded_max_positions(cash_ratio: float, num_positions: int, base_max: int) -> int:
    max_positions = base_max
    while cash_ratio > SLOT_EXPAND_CASH and num_positions >= max_positions:
        max_positions += 1
    return max_positions


# ── Data ──────────────────────────────────────────────────────────────────────

def is_synthetic(bar: dict) -> bool:
    """A placeholder bar, not a real session: flagged provisional, or O=H=L=C on zero
    volume (the daily append's close-only fill). These carry no open/range, land on
    weekends, and would blind the intraday stop — so they never price a fill."""
    if bar.get("provisional"):
        return True
    return (float(bar.get("5. volume") or 0) == 0
            and bar["1. open"] == bar["2. high"] == bar["3. low"] == bar["4. close"])


def rolling_hurst(x, window=HURST_WINDOW, lags=HURST_LAGS) -> list:
    """Aggregated-variance Hurst exponent: out[i] = 1/2 x slope of log Var(k-sums) vs log k,
    using only x[i-window+1 .. i] (overlapping k-sums that lie wholly inside it). None until
    `window` values exist, or when a variance is zero. 0.5 = random walk, > 0.5 persistent,
    < 0.5 mean-reverting."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    out = [None] * n
    if n < window:
        return out
    idx = np.arange(window - 1, n)
    logv = []
    for k in lags:
        cs = np.concatenate([[0.0], np.cumsum(x)])
        sk = np.zeros(n)
        sk[k - 1:] = cs[k:] - cs[:-k]                  # sk[i] = x[i-k+1] + ... + x[i]
        m = window - k + 1                             # k-sums ending in [i-m+1, i]
        c1 = np.concatenate([[0.0], np.cumsum(sk)])
        c2 = np.concatenate([[0.0], np.cumsum(sk * sk)])
        s1 = c1[idx + 1] - c1[idx + 1 - m]
        s2 = c2[idx + 1] - c2[idx + 1 - m]
        logv.append((s2 - s1 * s1 / m) / (m - 1))
    v = np.array(logv)
    ok = np.all(v > 1e-18, axis=0)
    lv = np.log(np.where(v > 1e-18, v, 1.0))
    lk = np.log(np.asarray(lags, dtype=float))
    lk = lk - lk.mean()
    slope = (lk[:, None] * (lv - lv.mean(axis=0))).sum(axis=0) / (lk * lk).sum()
    for j, i in enumerate(idx):
        if ok[j]:
            out[i] = float(slope[j] / 2)
    return out


def bars_from_series(ts: dict) -> dict | None:
    """Split-adjusted bars + ATR(14) + SMA50 from an Alpha-Vantage-style daily series
    (synthetic placeholder bars dropped)."""
    dates = sorted(d for d in ts if not is_synthetic(ts[d]))
    if len(dates) < 20:
        return None
    raw = {k: [float(ts[d][k]) for d in dates] for k in ("1. open", "2. high", "3. low", "4. close")}
    highs, lows, closes = split_adjust_ohlcv(raw["1. open"], raw["2. high"], raw["3. low"], raw["4. close"])
    opens = [o * (c / rc if rc else 1.0)
             for o, c, rc in zip(raw["1. open"], closes, raw["4. close"], strict=True)]
    atr = _wilder_atr(np.column_stack([opens, highs, lows, closes]))
    cs = np.cumsum([0.0, *closes])
    sma50 = [float((cs[i + 1] - cs[i - 49]) / 50) if i >= 49 else None for i in range(len(closes))]
    # Bar i's H uses the log returns up to its own close (known when day i's signal fires).
    rets = np.diff(np.log(np.maximum(np.asarray(closes, dtype=float), 1e-9)))
    return {"idx": {d: i for i, d in enumerate(dates)}, "o": opens, "h": list(highs),
            "l": list(lows), "c": list(closes), "atr": [float(a) for a in atr], "sma50": sma50,
            "hurst": [None, *rolling_hurst(rets)], "hurst_abs": [None, *rolling_hurst(np.abs(rets))]}


def _hurst_ok(bars, sym, day, hurst_filter) -> bool:
    """False only when the symbol's H on `day` is known and below the filter's minimum."""
    if not hurst_filter:
        return True
    key, min_h = hurst_filter
    b, i = _bar(bars, sym, day)
    h = b.get(key, [None] * (i + 1))[i] if b else None
    return h is None or h >= min_h


def _symbol_scores(args):
    sym, min_year = args
    ts = _br.load_ohlcv(sym)
    if not ts:
        return sym, {}
    rows = _br.process_symbol(sym, min_year, ts, sorted(ts), keyed=True)
    return sym, {r[0]: [r[2], r[3]] for r in rows}     # date -> [S10, L60]


def _fingerprint(symbols, min_year) -> str:
    h = hashlib.sha256(f"{min_year}|{','.join(symbols)}".encode())
    for rel in ("aether/scoring.py", "aether/patterns.py", "scripts/backtesting/backtest_ratings.py",
                "Data/candlestick_pattern_study.json", "Data/digit_sum_study.json",
                "Data/digit_sum_full_study.json"):
        p = os.path.join(BASE_DIR, rel)
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def build_panel(symbols, min_year, workers, rebuild=False) -> dict:
    fp = _fingerprint(symbols, min_year)
    if not rebuild and os.path.exists(PANEL_PATH):
        with open(PANEL_PATH, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("fingerprint") == fp:
            _out(f"  score panel: cache hit ({len(cached['scores'])} symbols)")
            return cached["scores"]
        _out("  score panel: cache stale (scoring code / studies / universe changed) — rebuilding")
    scores = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for n, (sym, s) in enumerate(ex.map(_symbol_scores, [(s, min_year) for s in symbols]), 1):
            if s:
                scores[sym] = s
            if n % 50 == 0:
                _out(f"  score panel: {n}/{len(symbols)} symbols")
    with open(PANEL_PATH, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fp, "min_year": min_year, "scores": scores}, f)
    return scores


# ── Simulation ────────────────────────────────────────────────────────────────

def _bar(bars, sym, day):
    b = bars.get(sym)
    i = b["idx"].get(day) if b else None
    return (b, i) if i is not None else (None, None)


def simulate(panel, bars, calendar, *, risk_rules=True, cost_bps=10.0,
             initial=INITIAL_EQUITY, excluded=frozenset(), max_trade_risk_pct=None,
             disable=frozenset(), hurst_filter=None) -> dict:
    """Replay `panel` ({sym: {date: [s10, l60]}}) over `calendar` (sorted trading days).

    max_trade_risk_pct: when set (and risk_rules is on), each buy is trimmed so
    (open - stop) * qty <= last close's equity * pct, as the live _execute_buys does.
    disable: rules from RULES to switch off (risk_rules only). "stop" removes every stop
    exit (intraday ATR stop and exit_decision's close floor, including its 8% fallback)
    but the loss cap still sizes against the stop; "regime" runs BALANCED always.
    hurst_filter: (bar key, min H) skips new entries whose H on the signal day is below min."""
    unknown = set(disable) - set(RULES)
    if unknown:
        raise ValueError(f"unknown rules: {sorted(unknown)}")
    if "loss_cap" in disable:
        max_trade_risk_pct = None
    c = cost_bps / 10_000.0
    cash = initial
    pos = {}                                  # sym -> {qty, entry, stop, entry_date, last}
    pending_buys, pending_sells = [], {}      # [(sym, usd, atr_mult)], {sym: reason}
    trades, curve = [], []
    traded_usd = 0.0
    cap_trims = 0
    spy = panel.get("SPY", {})

    def close_position(sym, px, day, reason):
        nonlocal cash, traded_usd
        p = pos.pop(sym)
        cash += p["qty"] * px * (1 - c)
        traded_usd += p["qty"] * px
        pnl = p["qty"] * (px * (1 - c) - p["entry"] * (1 + c))
        trades.append({"symbol": sym, "entry_date": p["entry_date"], "exit_date": day,
                       "entry": round(p["entry"], 4), "exit": round(px, 4), "reason": reason,
                       "ret_pct": round((px * (1 - c) / (p["entry"] * (1 + c)) - 1) * 100, 3),
                       "stop": round(p["stop"], 4) if p["stop"] else None,
                       # P&L as % of the equity the buy was sized on (the loss cap's basis).
                       "pnl_pct_equity": round(pnl / p["equity_basis"] * 100, 4)})

    for t, day in enumerate(calendar):
        # 1. Orders queued at yesterday's close fill at today's open (sells first).
        for sym, reason in pending_sells.items():
            b, i = _bar(bars, sym, day)
            close_position(sym, b["o"][i] if b else pos[sym]["last"], day, reason)
        pending_sells = {}
        for sym, usd, mult in pending_buys:
            b, i = _bar(bars, sym, day)
            if b is None or b["o"][i] <= 0:
                continue
            o = b["o"][i]
            atr_prev = b["atr"][i - 1] if i > 0 else 0.0
            stop = o - mult * atr_prev if (risk_rules and atr_prev > 0) else None
            usd = min(usd, cash)
            if risk_rules and max_trade_risk_pct and stop is not None and curve:
                cap = risk_capped_cash(o, stop, curve[-1][1] * max_trade_risk_pct)
                if cap is not None and usd > cap:
                    usd = cap
                    cap_trims += 1
            qty = usd / (o * (1 + c))
            if qty <= 0:
                continue
            cash -= usd
            traded_usd += qty * o
            pos[sym] = {"qty": qty, "entry": o, "stop": stop, "entry_date": day, "last": o,
                        "equity_basis": curve[-1][1] if curve else initial}
        pending_buys = []

        # 2. Intraday hard stop; 3. mark to the close.
        for sym in list(pos):
            b, i = _bar(bars, sym, day)
            if b is None:
                continue
            p = pos[sym]
            if risk_rules and "stop" not in disable and p["stop"] and b["l"][i] <= p["stop"]:
                close_position(sym, min(b["o"][i], p["stop"]), day, "stop")
                continue
            p["last"] = b["c"][i]
        invested = sum(p["qty"] * p["last"] for p in pos.values())
        equity = cash + invested
        curve.append((day, equity, invested))
        if t + 1 >= len(calendar):
            break

        # 4. Exit signals on today's scores.
        for sym, p in pos.items():
            sc = panel.get(sym, {}).get(day)
            if not sc:
                continue
            s10, l60 = sc
            b, i = _bar(bars, sym, day)
            sma50 = b["sma50"][i] if b else None
            if not risk_rules:
                if soft_exit(s10, l60) and not winner_protected(p["last"] > p["entry"], p["last"], sma50):
                    pending_sells[sym] = "momentum decay"
                continue
            # exit_decision split in two so each rule can be switched off on its own. The
            # floor goes through exit_decision (scores None = no soft signal) to keep its
            # 8%-of-cost fallback for a missing stop; passing stop 0 would not remove it.
            if "stop" not in disable:
                action, _ = exit_decision(p["last"], p["entry"], p["stop"] or 0, None, None, sma50)
                if action == "SELL":
                    pending_sells[sym] = "momentum decay"
                    continue
            if "soft_exit" not in disable and soft_exit(s10, l60) and (
                    "winner_protection" in disable
                    or not winner_protected(p["last"] > p["entry"], p["last"], sma50)):
                pending_sells[sym] = "momentum decay"

        # 5. Entry signals on today's scores, sized against today's equity.
        regime = risk_rules and "regime" not in disable
        rules = PROFILE_RULES[profile_for(spy.get(day, [None, None])[1]) if regime else "BALANCED"]
        remaining = len(pos) - len(pending_sells)
        if risk_rules:
            max_pos = (rules["max_positions"] if "slot_expansion" in disable else
                       expanded_max_positions(cash / equity if equity else 0.0, remaining,
                                              rules["max_positions"]))
            per_cap = rules["max_allocation_pct"] * equity
            buffer = 0.0 if "cash_buffer" in disable else rules["cash_buffer_pct"]
        else:
            max_pos, per_cap, buffer = rules["max_positions"], equity / rules["max_positions"], 0.0
        kept = sum(p["qty"] * p["last"] for s, p in pos.items() if s not in pending_sells)
        investable = equity * (1 - buffer) - kept
        cands = sorted(((sc[0] + sc[1], sym) for sym, by_day in panel.items()
                        if (sc := by_day.get(day)) and sym not in pos and sym not in excluded
                        and sc[0] + sc[1] >= rules["min_score_threshold"]
                        and _hurst_ok(bars, sym, day, hurst_filter)), reverse=True)
        for _score, sym in cands:
            if remaining >= max_pos:
                break
            usd = min(per_cap, investable)
            if usd < MIN_TICKET_USD:
                break
            pending_buys.append((sym, usd, rules["atr_multiplier"]))
            investable -= usd
            remaining += 1

    return {"curve": curve, "trades": trades, "traded_usd": traded_usd,
            "open_positions": sorted(pos), "cap_trims": cap_trims}


def buy_and_hold(bars, calendar, symbols, cost_bps=10.0, initial=INITIAL_EQUITY) -> dict:
    """Equal dollars in each symbol that trades on calendar[0], bought at that open."""
    c = cost_bps / 10_000.0
    day0 = calendar[0]
    held = [s for s in symbols if _bar(bars, s, day0)[0] is not None]
    if not held:
        return {"curve": [], "trades": [], "traded_usd": 0.0, "open_positions": []}
    qty, last = {}, {}
    for s in held:
        b, i = _bar(bars, s, day0)
        qty[s] = initial / len(held) / (b["o"][i] * (1 + c))
        last[s] = b["o"][i]
    curve = []
    for day in calendar:
        for s in held:
            b, i = _bar(bars, s, day)
            if b is not None:
                last[s] = b["c"][i]
        v = sum(qty[s] * last[s] for s in held)
        curve.append((day, v, v))
    return {"curve": curve, "trades": [], "traded_usd": initial, "open_positions": held}


# ── Metrics ───────────────────────────────────────────────────────────────────

def stop_tail(trades: list, cap_pct=None) -> dict:
    """Stop-out tail of one variant (R&D #54a): gap-throughs and loss per stopped trade as %
    of equity. Losses are positive numbers; `cap_pct` is the per-trade cap as a fraction."""
    stops = [t for t in trades if t["reason"] == "stop"]
    if not stops:
        return {"n_stop_exits": 0}
    losses = np.array([-t["pnl_pct_equity"] for t in stops])
    gaps = [t for t in stops if t.get("stop") and t["exit"] < t["stop"]]
    out = {
        "n_stop_exits": len(stops),
        "n_gap_through": len(gaps),
        "gap_through_share": round(len(gaps) / len(stops), 3),
        "loss_median_pct": round(float(np.median(losses)), 3),
        "loss_p95_pct": round(float(np.percentile(losses, 95)), 3),
        "loss_worst_pct": round(float(losses.max()), 3),
        "worst_trade": max(stops, key=lambda t: -t["pnl_pct_equity"]),
    }
    if gaps:
        out["gap_median_pct_below_stop"] = round(float(np.median(
            [(1 - t["exit"] / t["stop"]) * 100 for t in gaps])), 3)
    if cap_pct:
        out["share_over_cap"] = round(float(np.mean(losses > cap_pct * 100)), 3)
        out["gate_fail"] = bool(out["loss_p95_pct"] > STOP_TAIL_P95_GATE
                                or out["loss_worst_pct"] > STOP_TAIL_WORST_GATE)
    return out


def _print_stop_tail(tails: dict) -> None:
    cols = [("n_stop_exits", "Stops"), ("n_gap_through", "Gaps"), ("gap_through_share", "Gap%"),
            ("loss_median_pct", "Med%"), ("loss_p95_pct", "P95%"), ("loss_worst_pct", "Worst%"),
            ("share_over_cap", ">Cap")]
    _out("\n  Stop-out loss, % of equity (R&D #54a)")
    _out("  " + f"{'variant':<20}" + "".join(f"{h:>9}" for _k, h in cols))
    for name, t in tails.items():
        if t.get("n_stop_exits"):
            _out("  " + f"{name:<20}" + "".join(f"{('-' if t.get(k) is None else t.get(k)):>9}" for k, _h in cols))


def metrics(result: dict) -> dict:
    curve = result["curve"]
    if len(curve) < 2:
        return {}
    eq = np.array([e for _d, e, _i in curve])
    d0 = datetime.date.fromisoformat(curve[0][0])
    d1 = datetime.date.fromisoformat(curve[-1][0])
    years = max((d1 - d0).days / 365.25, 1e-9)
    rets = eq[1:] / eq[:-1] - 1
    peak = np.maximum.accumulate(eq)
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    trades = result["trades"]
    wins = [t for t in trades if t["ret_pct"] > 0]
    avg_eq = float(np.mean(eq))
    return {
        "start": curve[0][0], "end": curve[-1][0], "years": round(years, 2),
        "final_equity": round(float(eq[-1]), 2),
        "total_return_pct": round((eq[-1] / eq[0] - 1) * 100, 2),
        "cagr_pct": round(((eq[-1] / eq[0]) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(float(np.min(eq / peak - 1)) * 100, 2),
        "ann_vol_pct": round(sd * math.sqrt(252) * 100, 2),
        "sharpe": round(float(np.mean(rets)) / sd * math.sqrt(252), 2) if sd > 0 else None,
        "avg_exposure_pct": round(float(np.mean([i / e for _d, e, i in curve if e])) * 100, 1),
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "avg_trade_ret_pct": round(float(np.mean([t["ret_pct"] for t in trades])), 3) if trades else None,
        "stop_exits": sum(t["reason"] == "stop" for t in trades),
        "cap_trims": result.get("cap_trims", 0),
        "turnover_x_per_yr": round(result["traded_usd"] / avg_eq / years, 2) if avg_eq else None,
    }


# ── Driver ────────────────────────────────────────────────────────────────────

def run_variants(panel, bars, calendar, cost_bps, excluded, eligible, attribution, hurst=False) -> dict:
    cap = CFG.system_max_trade_risk_pct
    return {
        "aether_rules": simulate(panel, bars, calendar, risk_rules=True, cost_bps=cost_bps, excluded=excluded,
                                 max_trade_risk_pct=cap),
        "aether_no_cap": simulate(panel, bars, calendar, risk_rules=True, cost_bps=cost_bps, excluded=excluded),
        **({f"no_{rule}": simulate(panel, bars, calendar, risk_rules=True, cost_bps=cost_bps,
                                   excluded=excluded, max_trade_risk_pct=cap, disable={rule})
            for rule in RULES if rule != "loss_cap"} if attribution else {}),
        **({name: simulate(panel, bars, calendar, risk_rules=True, cost_bps=cost_bps, excluded=excluded,
                           max_trade_risk_pct=cap, hurst_filter=hf)
            for name, hf in HURST_VARIANTS.items()} if hurst else {}),
        "signal_only": simulate(panel, bars, calendar, risk_rules=False, cost_bps=cost_bps, excluded=excluded),
        "spy_buy_hold": buy_and_hold(bars, calendar, ["SPY"], cost_bps),
        "ew_buy_hold": buy_and_hold(bars, calendar, eligible, cost_bps),
    }


def hurst_verdict(train: dict, test: dict) -> dict:
    """R&D #54b gate: the Hurst variant with the best train Sharpe passes only if its test
    Sharpe beats aether_rules and its test max drawdown is no deeper."""
    picked = max(HURST_VARIANTS, key=lambda k: train[k].get("sharpe") if train[k].get("sharpe") is not None
                 else -math.inf)
    t, base = test[picked], test["aether_rules"]
    sharpe_ok = (t.get("sharpe") or -math.inf) > (base.get("sharpe") or -math.inf)
    dd_ok = t.get("max_drawdown_pct", -math.inf) >= base.get("max_drawdown_pct", -math.inf)
    return {"picked": picked, "train_sharpe": train[picked].get("sharpe"),
            "test_sharpe": t.get("sharpe"), "test_max_dd": t.get("max_drawdown_pct"),
            "base_test_sharpe": base.get("sharpe"), "base_test_max_dd": base.get("max_drawdown_pct"),
            "passed": bool(sharpe_ok and dd_ok)}


def rank_by_sharpe(table: dict) -> list:
    """Rule variants (aether_rules, aether_no_cap, no_*), best Sharpe first; no Sharpe last."""
    rule_variants = [k for k in table if k.startswith(("aether_", "no_"))]
    return sorted(rule_variants, key=lambda k: (table[k].get("sharpe") is None,
                                                -(table[k].get("sharpe") or 0.0)))


def pick_by_sharpe(table: dict) -> str:
    return rank_by_sharpe(table)[0]


def _iso_date(s: str) -> str:
    """argparse type for --split: an ISO date (string order = date order), else an error."""
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {s!r}") from None


def draw_paths(symbols, calendar, n, seed=ROBUST_SEED, frac=ROBUST_FRAC,
               max_offset=ROBUST_MAX_OFFSET, split=None) -> list:
    """n reproducible perturbed paths: a random `frac` of the non-SPY symbols (SPY always kept,
    it sets the regime) and a start 0..max_offset trading days into `calendar`. With `split`,
    the offset is also capped so the half before it keeps at least 2 days."""
    rng = random.Random(seed)
    others = sorted(s for s in symbols if s != "SPY")
    k = round(len(others) * frac)
    top = max(0, min(max_offset, len(calendar) - 2))
    if split:
        top = max(0, min(top, sum(d < split for d in calendar) - 2))
    return [{"symbols": frozenset(rng.sample(others, k)) | {"SPY"}, "start": calendar[rng.randint(0, top)]}
            for _ in range(n)]


def path_universe(panel, eligible, syms):
    """A draw's universe: the score panel and the eligible list cut down to `syms`."""
    return {s: panel[s] for s in syms if s in panel}, [s for s in eligible if s in syms]


def _pct(vals, q):
    return round(float(np.percentile(vals, q)), 2) if vals else None


def robustness_summary(per_draw: list) -> dict:
    """per_draw: [{period: {variant: metrics}}]. Per period and variant: median and 10th-90th
    percentile Sharpe / CAGR / max drawdown, and the win rate against aether_rules (share of
    draws where its Sharpe is higher on the same path). Then the pre-registered verdicts."""
    periods = list(per_draw[0]) if per_draw else []
    out = {}
    for period in periods:
        out[period] = {}
        for v in per_draw[0][period]:
            row = {}
            for key in ("sharpe", "cagr_pct", "max_drawdown_pct"):
                vals = [d[period][v].get(key) for d in per_draw if d[period][v].get(key) is not None]
                row[key] = {"median": _pct(vals, 50), "p10": _pct(vals, 10), "p90": _pct(vals, 90)}
            pairs = [(d[period][v].get("sharpe"), d[period]["aether_rules"].get("sharpe")) for d in per_draw]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            row["win_rate"] = round(sum(a > b for a, b in pairs) / len(pairs), 3) if pairs else None
            row["n"] = len(pairs)
            out[period][v] = row
    verdicts = {}
    halves = [p for p in ("train", "test") if p in out]
    if len(halves) == 2:
        for v in out["test"]:
            if v == "aether_rules":
                continue
            w = [out[h][v]["win_rate"] for h in halves]
            if None in w:
                verdicts[v] = "inconclusive"
            elif all(x >= ROBUST_WIN for x in w):
                verdicts[v] = "robustly better"
            elif all(x <= ROBUST_LOSE for x in w):
                verdicts[v] = "robustly worse"
            else:
                verdicts[v] = "inconclusive"
    return {"periods": out, "verdicts": verdicts}


def run_robustness(paths, calendar, split, run_table) -> dict:
    """run_table(symbols, days) -> {variant: metrics}; each path runs full, train and test."""
    per_draw = []
    for path in paths:
        full = [d for d in calendar if d >= path["start"]]
        per_draw.append({"full": run_table(path["symbols"], full),
                         "train": run_table(path["symbols"], [d for d in full if d < split]),
                         "test": run_table(path["symbols"], [d for d in full if d >= split])})
    summary = robustness_summary(per_draw)
    summary["n_draws"] = len(paths)
    summary["per_draw_sharpe"] = [{p: {v: m.get("sharpe") for v, m in d[p].items()} for p in d}
                                  for d in per_draw]
    return summary


def _print_robustness(summary) -> None:
    _out(f"\n  ROBUSTNESS (R&D #62): {summary['n_draws']} draws, {ROBUST_FRAC:.0%} of symbols, "
         f"start offset 0-{ROBUST_MAX_OFFSET} days, seed {ROBUST_SEED}")
    for period, rows in summary["periods"].items():
        _out(f"\n  {period}: Sharpe median [p10, p90]   MaxDD% median   win vs aether_rules")
        for v, r in rows.items():
            sh, dd = r["sharpe"], r["max_drawdown_pct"]
            win = "-" if v == "aether_rules" or r["win_rate"] is None else f"{r['win_rate']:.0%}"
            _out(f"  {v:<22}{sh['median']!s:>7} [{sh['p10']!s:>6},{sh['p90']!s:>6}]   {dd['median']!s:>8}   {win:>6}")
    _out("")
    for v, verdict in summary["verdicts"].items():
        _out(f"  {v:<22}{verdict}")


def oos_split(calendar, split, run_table) -> dict:
    """Out-of-sample check: run_table(days) -> {variant: metrics} runs on the days before
    `split` (train) and from `split` on (test); the pick is the best train Sharpe, and
    test_rank is its 1-based rank by test Sharpe. `split` is an ISO date (YYYY-MM-DD)."""
    split = datetime.date.fromisoformat(split).isoformat()
    train_cal = [d for d in calendar if d < split]
    test_cal = [d for d in calendar if d >= split]
    if len(train_cal) < 2 or len(test_cal) < 2:
        raise SystemExit(f"--split {split} leaves no train or test days")
    train, test = run_table(train_cal), run_table(test_cal)
    picked = pick_by_sharpe(train)
    return {"date": split, "train": train, "test": test, "picked": picked,
            "test_rank": rank_by_sharpe(test).index(picked) + 1}


def _print_table(calendar, table) -> None:
    cols = [("cagr_pct", "CAGR%"), ("total_return_pct", "Total%"), ("max_drawdown_pct", "MaxDD%"),
            ("sharpe", "Sharpe"), ("ann_vol_pct", "Vol%"), ("avg_exposure_pct", "Expo%"),
            ("n_trades", "Trades"), ("win_rate", "Win"), ("turnover_x_per_yr", "Turn/yr"),
            ("cap_trims", "Trims")]
    _out(f"\n  {calendar[0]} -> {calendar[-1]}  ({table['spy_buy_hold'].get('years')} yr)")
    _out("  " + f"{'variant':<20}" + "".join(f"{h:>9}" for _k, h in cols))
    for name, m in table.items():
        _out("  " + f"{name:<20}" + "".join(f"{('-' if m.get(k) is None else m.get(k)):>9}" for k, _h in cols))


def run(min_year=2023, workers=None, cost_bps=10.0, max_symbols=None, rebuild=False,
        attribution=False, split=None, hurst=False, robust=0) -> dict:
    ohlcv = {os.path.basename(f)[:-len("_daily.json")]
             for f in glob.glob(os.path.join(_br.OHLCV_DIR, "*_daily.json"))}
    cached = {d for d in os.listdir(_br.SYM_DIR) if os.path.isdir(os.path.join(_br.SYM_DIR, d))}
    symbols = sorted(ohlcv & cached)
    if max_symbols:
        symbols = sorted(set(symbols[:max_symbols]) | ({"SPY"} & set(symbols)))
    if "SPY" not in symbols:
        raise SystemExit("SPY needs both a Chaikin cache and OHLCV (regime + benchmark)")
    _out(f"\nPortfolio backtest: {len(symbols)} symbols, {min_year}+, cost {cost_bps} bps/side")

    panel = build_panel(symbols, min_year, workers or max(1, (os.cpu_count() or 2) - 1), rebuild)
    bars = {s: b for s in symbols if (ts := _br.load_ohlcv(s)) and (b := bars_from_series(ts))}
    spy_days = sorted(bars["SPY"]["idx"])
    first_score = min(min(by_day) for by_day in panel.values() if by_day)
    calendar = [d for d in spy_days if d >= first_score]
    excluded = frozenset(s for s in symbols if instruments.is_excluded(s))
    eligible = [s for s in symbols if s not in excluded]

    attribution = attribution or bool(split)
    results = run_variants(panel, bars, calendar, cost_bps, excluded, eligible, attribution, hurst)
    table = {k: metrics(v) for k, v in results.items()}
    _print_table(calendar, table)
    cap = CFG.system_max_trade_risk_pct
    tails = {k: stop_tail(v["trades"], None if k == "aether_no_cap" else cap) for k, v in results.items()}
    _print_stop_tail(tails)
    gate = tails["aether_rules"]
    if gate.get("n_stop_exits"):
        _out(f"  gate (aether_rules): P95 {gate['loss_p95_pct']}% vs {STOP_TAIL_P95_GATE}%, worst "
             f"{gate['loss_worst_pct']}% vs {STOP_TAIL_WORST_GATE}% -> "
             f"{'FAIL: the cap needs work' if gate['gate_fail'] else 'PASS: the cap holds'}")

    split_out = None
    if split:
        split_out = oos_split(calendar, split, lambda cal: {
            k: metrics(v) for k, v in run_variants(panel, bars, cal, cost_bps, excluded, eligible, True,
                                                   hurst).items()})
        train, test, picked = split_out["train"], split_out["test"], split_out["picked"]
        _out("\n  TRAIN (pick on Sharpe)")
        _print_table([d for d in calendar if d < split_out["date"]], train)
        _out("\n  TEST (never seen by the pick)")
        _print_table([d for d in calendar if d >= split_out["date"]], test)
        _out(f"\n  picked on train: {picked}  ->  test Sharpe {test[picked].get('sharpe')}, "
             f"rank {split_out['test_rank']}/{len(rank_by_sharpe(test))} among rule variants")
        if hurst:
            hv = split_out["hurst"] = hurst_verdict(train, test)
            _out(f"\n  R&D #54b Hurst: picked on train {hv['picked']} (Sharpe {hv['train_sharpe']}) -> test "
                 f"Sharpe {hv['test_sharpe']} vs aether_rules {hv['base_test_sharpe']}, MaxDD "
                 f"{hv['test_max_dd']}% vs {hv['base_test_max_dd']}% -> "
                 f"{'PASS' if hv['passed'] else 'FAIL: park the filter'}")

    robust_out = None
    if robust:
        if not split:
            raise SystemExit("--robust needs --split (it reports both halves)")
        split_iso = datetime.date.fromisoformat(split).isoformat()

        def run_table(syms, days):
            sub, elig = path_universe(panel, eligible, syms)
            return {k: metrics(v) for k, v in
                    run_variants(sub, bars, days, cost_bps, excluded, elig, True).items()}

        robust_out = run_robustness(draw_paths(symbols, calendar, robust, split=split_iso), calendar, split_iso, run_table)
        _print_robustness(robust_out)

    payload = {
        "as_of": calendar[-1], "min_year": min_year, "cost_bps": cost_bps,
        "max_trade_risk_pct": CFG.system_max_trade_risk_pct,
        "n_symbols": len(symbols), "n_excluded": len(excluded),
        "caveats": [
            "survivorship bias: universe is today's symbol list",
            "not simulated: overrides, bottom-confirmed buys, breadth downgrade, scarcity sizing, "
            "scale-outs, covered calls, rotation",
            "score reconstruction omits live digit_sum, setup_ok, vrecovery_score",
            "synthetic (provisional / flat zero-volume) bars are dropped from prices and the "
            "calendar, but backtest_ratings' score reconstruction still reads them",
        ],
        "metrics": table,
        "split": split_out,
        "robustness": robust_out,
        "stop_tail": tails,
        "curves": {k: [[d, round(e, 2)] for d, e, _i in v["curve"]] for k, v in results.items()},
        "trades": {k: v["trades"] for k, v in results.items() if v["trades"]},
        "open_positions": {k: v["open_positions"] for k, v in results.items()},
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    _out(f"\n  Wrote {OUT_PATH}")
    return payload


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--min-year", type=int, default=2023)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--attribution", action="store_true",
                    help="add one aether_rules variant per rule switched off")
    ap.add_argument("--hurst", action="store_true",
                    help="add the R&D #54b Hurst entry-filter variants (verdict needs --split)")
    ap.add_argument("--split", default=None, metavar="YYYY-MM-DD",
                    type=_iso_date,
                    help="out-of-sample check: pick a rule variant on days before this date, "
                         "test it on the days from it (implies --attribution)")
    ap.add_argument("--robust", type=int, default=0, metavar="N",
                    help="R&D #62: re-run the rule variants on N perturbed paths (needs --split)")
    a = ap.parse_args()
    run(a.min_year, a.workers, a.cost_bps, a.max_symbols, a.rebuild, a.attribution, a.split, a.hurst,
        a.robust)
