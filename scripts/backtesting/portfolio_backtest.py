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
                 stop, S10+L60<0 soft exit, winner protection).
  signal_only    the same entry ranking with a fixed BALANCED threshold, full equal-weight
                 deployment, no stop and no cash buffer; exits on the soft rule only.
                 aether_rules minus signal_only = what the risk overlay costs or buys.
  spy_buy_hold   SPY bought on the first day and held.
  ew_buy_hold    equal dollars in every eligible universe symbol on the first day, held.

Execution: scores dated day t (close data) trigger orders filled at day t+1's OPEN —
no look-ahead. Hard stops fill intraday at min(open, stop). Prices are split-adjusted.
Costs: --cost-bps per side (default 10).

NOT simulated (live-only rules): strategic overrides, bottom-confirmed buys, the SPY-RSP
breadth downgrade, scarcity-core sizing, scale-outs/covered calls, rotation. The score
reconstruction also omits the live digit_sum, setup_ok and vrecovery_score fields.
The universe is TODAY's symbol list, so results carry survivorship bias (optimistic).
Synthetic placeholder bars (provisional / O=H=L=C on zero volume) never price a fill.

The score panel (~6s/symbol) is cached in Data/portfolio_backtest_panel.json, keyed by a
fingerprint of the scoring code + calibration studies; --rebuild forces a recompute.
Output: Data/portfolio_backtest.json (config, metrics, equity curves, trades).

Usage:
    python scripts/backtesting/portfolio_backtest.py [--min-year 2023] [--workers 8]
                                                     [--cost-bps 10] [--max-symbols N]
                                                     [--rebuild]
"""
import argparse
import datetime
import glob
import hashlib
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import backtest_ratings as _br
import instruments
from aether.risk_utils import split_adjust_ohlcv
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
    return {"idx": {d: i for i, d in enumerate(dates)}, "o": opens, "h": list(highs),
            "l": list(lows), "c": list(closes), "atr": [float(a) for a in atr], "sma50": sma50}


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
             initial=INITIAL_EQUITY, excluded=frozenset()) -> dict:
    """Replay `panel` ({sym: {date: [s10, l60]}}) over `calendar` (sorted trading days)."""
    c = cost_bps / 10_000.0
    cash = initial
    pos = {}                                  # sym -> {qty, entry, stop, entry_date, last}
    pending_buys, pending_sells = [], {}      # [(sym, usd, atr_mult)], {sym: reason}
    trades, curve = [], []
    traded_usd = 0.0
    spy = panel.get("SPY", {})

    def close_position(sym, px, day, reason):
        nonlocal cash, traded_usd
        p = pos.pop(sym)
        cash += p["qty"] * px * (1 - c)
        traded_usd += p["qty"] * px
        trades.append({"symbol": sym, "entry_date": p["entry_date"], "exit_date": day,
                       "entry": round(p["entry"], 4), "exit": round(px, 4), "reason": reason,
                       "ret_pct": round((px * (1 - c) / (p["entry"] * (1 + c)) - 1) * 100, 3)})

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
            usd = min(usd, cash)
            qty = usd / (o * (1 + c))
            if qty <= 0:
                continue
            cash -= usd
            traded_usd += qty * o
            atr_prev = b["atr"][i - 1] if i > 0 else 0.0
            stop = o - mult * atr_prev if (risk_rules and atr_prev > 0) else None
            pos[sym] = {"qty": qty, "entry": o, "stop": stop, "entry_date": day, "last": o}
        pending_buys = []

        # 2. Intraday hard stop; 3. mark to the close.
        for sym in list(pos):
            b, i = _bar(bars, sym, day)
            if b is None:
                continue
            p = pos[sym]
            if risk_rules and p["stop"] and b["l"][i] <= p["stop"]:
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
            if risk_rules:
                action, _ = exit_decision(p["last"], p["entry"], p["stop"] or 0, s10, l60, sma50)
                if action == "SELL":
                    pending_sells[sym] = "momentum decay"
            elif soft_exit(s10, l60) and not winner_protected(p["last"] > p["entry"], p["last"], sma50):
                pending_sells[sym] = "momentum decay"

        # 5. Entry signals on today's scores, sized against today's equity.
        rules = PROFILE_RULES[profile_for(spy.get(day, [None, None])[1]) if risk_rules else "BALANCED"]
        remaining = len(pos) - len(pending_sells)
        if risk_rules:
            max_pos = expanded_max_positions(cash / equity if equity else 0.0, remaining,
                                             rules["max_positions"])
            per_cap, buffer = rules["max_allocation_pct"] * equity, rules["cash_buffer_pct"]
        else:
            max_pos, per_cap, buffer = rules["max_positions"], equity / rules["max_positions"], 0.0
        kept = sum(p["qty"] * p["last"] for s, p in pos.items() if s not in pending_sells)
        investable = equity * (1 - buffer) - kept
        cands = sorted(((sc[0] + sc[1], sym) for sym, by_day in panel.items()
                        if (sc := by_day.get(day)) and sym not in pos and sym not in excluded
                        and sc[0] + sc[1] >= rules["min_score_threshold"]), reverse=True)
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
            "open_positions": sorted(pos)}


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
        "turnover_x_per_yr": round(result["traded_usd"] / avg_eq / years, 2) if avg_eq else None,
    }


# ── Driver ────────────────────────────────────────────────────────────────────

def run(min_year=2023, workers=None, cost_bps=10.0, max_symbols=None, rebuild=False) -> dict:
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

    results = {
        "aether_rules": simulate(panel, bars, calendar, risk_rules=True, cost_bps=cost_bps, excluded=excluded),
        "signal_only": simulate(panel, bars, calendar, risk_rules=False, cost_bps=cost_bps, excluded=excluded),
        "spy_buy_hold": buy_and_hold(bars, calendar, ["SPY"], cost_bps),
        "ew_buy_hold": buy_and_hold(bars, calendar, eligible, cost_bps),
    }
    table = {k: metrics(v) for k, v in results.items()}

    cols = [("cagr_pct", "CAGR%"), ("total_return_pct", "Total%"), ("max_drawdown_pct", "MaxDD%"),
            ("sharpe", "Sharpe"), ("ann_vol_pct", "Vol%"), ("avg_exposure_pct", "Expo%"),
            ("n_trades", "Trades"), ("win_rate", "Win"), ("turnover_x_per_yr", "Turn/yr")]
    _out(f"\n  {calendar[0]} -> {calendar[-1]}  ({table['spy_buy_hold'].get('years')} yr)")
    _out("  " + f"{'variant':<14}" + "".join(f"{h:>9}" for _k, h in cols))
    for name, m in table.items():
        _out("  " + f"{name:<14}" + "".join(f"{('-' if m.get(k) is None else m.get(k)):>9}" for k, _h in cols))

    payload = {
        "as_of": calendar[-1], "min_year": min_year, "cost_bps": cost_bps,
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
    a = ap.parse_args()
    run(a.min_year, a.workers, a.cost_bps, a.max_symbols, a.rebuild)
