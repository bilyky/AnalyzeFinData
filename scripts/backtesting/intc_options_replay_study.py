"""INTC Options-Adviser historical replay + MODELED-premium P&L backtest.

Answers the user's "can we test the adviser on history data?" in two modes, both reading only
the local OHLCV cache (Data/Symbol_full/<SYM>_daily.json):

    --show-menu YYYY-MM-DD   TASK 1 — reconstruct that day's spot + AETHER stop/target from the
                             cache, synthesize a ~35-DTE option chain, and print the exact
                             four-strategy adviser menu it WOULD have produced (a visual replay).

    (default)                TASK 2 — walk historical entry dates, build the menu at each, then
                             SETTLE every strategy at option expiry on the realized price path and
                             aggregate P&L vs buy-and-hold. Highlights the two roadmap lessons:
                             how often protection (collar/put) PRESERVED CAPITAL on down moves,
                             and how often the covered call CAPPED A WINNER (opportunity cost).

NOTE: there is no historical option chain anywhere (E*TRADE serves only current chains; bulk history
    is a paid product). So every premium here is **MODELED** — Black-Scholes on the underlying's own
    realized volatility (aether/option_pricing.py). Real implied vol carries a vol-risk-premium, so
    these premiums UNDERSTATE true prices: protective-put costs are floors (real is worse), covered-
    call income is a floor (real is better). Treat outcomes as directional, not exact.

NO LOOK-AHEAD: spot, AETHER levels, and realized vol at each entry use ONLY bars <= that date;
future bars are read solely to settle P&L (outcome measurement, never signal).

Usage:
    PYTHONIOENCODING=utf-8 python scripts/backtesting/intc_options_replay_study.py
    PYTHONIOENCODING=utf-8 python scripts/backtesting/intc_options_replay_study.py --show-menu 2026-07-24
    PYTHONIOENCODING=utf-8 python scripts/backtesting/intc_options_replay_study.py --symbol AMD
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import instruments
from aether import option_pricing as op
from aether import options_adviser as oa
from aether import risk_utils
from aether.logger import get_logger as _get_logger


_log = _get_logger("intc_options_replay")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OHLCV = BASE_DIR / "Data" / "Symbol_full"
OUT_FILE = BASE_DIR / "Data" / "intc_options_replay_study.json"

# ── Replay / backtest parameters ──
VOL_WINDOW = op.DEFAULT_VOL_WINDOW   # trailing days for realized vol (entry-anchored)
DTE_TARGET = 35                      # aim for the ~35-DTE expiry the adviser prefers
DTE_MIN = 30                         # ... but accept the first bar >= this many calendar days out
WARMUP = VOL_WINDOW + 5              # bars needed before the first entry (vol lookback)
SAMPLE_EVERY = 5                     # sample an entry every N trading days (weekly; limits overlap)
LOT_SHARES = 100                     # illustrative covered lot (1 contract)

MODELED_CAVEAT = ("MODELED premiums (Black-Scholes on realized vol) — NOT live option data; "
                  "real IV carries a vol-risk-premium so these understate true prices.")

# Strategies that hold the underlying lot (their P&L includes stock; CSP does not).
_STOCK_KINDS = {"collar", "protective_put", "covered_call"}

# Down-move protection is NOT the same across structures:
#   hard floor      — a long put caps the loss (collar, protective_put)
#   premium cushion — only the credit softens the loss; the stock still rides all the way down
#                     (covered_call). Reporting this as a "floor" would overstate protection.
_PROTECTION = {"collar": "hard floor", "protective_put": "hard floor",
               "covered_call": "premium cushion", "cash_secured_put": None}


def _load(path):
    """Return (dates, highs, lows, closes) as parallel lists, split-safe for INTC.

    Skips the provisional vol=0 synthetic append (flat OHLC) so it never becomes an entry or a
    settlement price. Raw (unadjusted) bars — valid for INTC (no split in the modern range); for a
    split-prone symbol switch to risk_utils._load_ohlcv_series (which back-adjusts).
    """
    try:
        with open(path, encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    dates, highs, lows, closes = [], [], [], []
    for d in sorted(ts.keys()):
        b = ts[d]
        if b.get("provisional"):
            continue
        try:
            hi, lo, cl = float(b["2. high"]), float(b["3. low"]), float(b["4. close"])
        except (KeyError, ValueError):
            continue
        dates.append(d)
        highs.append(hi)
        lows.append(lo)
        closes.append(cl)
    return (dates, highs, lows, closes) if len(closes) > WARMUP + 5 else None


def _levels_at(i, highs, lows, closes):
    """AETHER stop/target from bars <= i only (entry-anchored, no staleness gate)."""
    spot = closes[i]
    hh, ll, cc = highs[:i + 1], lows[:i + 1], closes[:i + 1]
    stop = risk_utils.resolve_stop_detailed(spot, highs=hh, lows=ll, closes=cc)["stop"]
    target = risk_utils.resolve_target_detailed(spot, highs=hh, lows=ll, closes=cc)["target"]
    return spot, oa.Levels(stop=stop, target=target)


def _expiry_index(i, dates):
    """First bar index j > i whose date is >= i's date + DTE_MIN calendar days, else None."""
    d0 = datetime.date.fromisoformat(dates[i])
    floor = d0 + datetime.timedelta(days=DTE_MIN)
    for j in range(i + 1, len(dates)):
        if datetime.date.fromisoformat(dates[j]) >= floor:
            return j
    return None


def _build_menu(i, dates, highs, lows, closes, *, select="level", require_settle=True):
    """Reconstruct the adviser menu at entry bar i (or None if it can't be built).

    ``require_settle`` (backtest) demands a real forward bar >= DTE_MIN days out so the strategy can
    be settled on the realized path; the settlement index is ``j``. When False (menu-display replay)
    a synthetic ~DTE_TARGET expiry is used if the cache doesn't extend that far, and ``j`` is None.
    """
    spot, levels = _levels_at(i, highs, lows, closes)
    sigma = op.realized_vol(closes[:i + 1], window=VOL_WINDOW)
    if sigma is None or spot <= 0:
        return None
    d0 = datetime.date.fromisoformat(dates[i])
    j = _expiry_index(i, dates)
    if j is not None:
        expiry = datetime.date.fromisoformat(dates[j])
    elif require_settle:
        return None                                        # no settleable bar -> skip for backtest
    else:
        expiry = d0 + datetime.timedelta(days=DTE_TARGET)  # synthetic (cache ends before expiry)
    quotes = op.synthesize_chain(spot, expiry, d0, sigma)
    if not quotes:
        return None
    pos = oa.Position(symbol="INTC", qty=LOT_SHARES, cost_basis=spot, price=spot,
                      date_acquired=d0 - datetime.timedelta(days=400))  # long-term; P&L-neutral
    report = oa.build_report(pos, levels, quotes, spot=spot, expiry=expiry,
                             today=d0, data_source="offline (modeled)", select=select)
    return {"i": i, "j": j, "d0": d0, "expiry": expiry, "spot": spot, "sigma": sigma,
            "levels": levels, "report": report}


def _leg_option_pnl(leg, s_t):
    """Per-share option P&L at expiry for one leg (+ credit to the holder)."""
    intrinsic = max(s_t - leg.strike, 0.0) if leg.option_type == "CALL" else max(leg.strike - s_t, 0.0)
    return (intrinsic - leg.price) if leg.action == "buy" else (leg.price - intrinsic)


def _settle(strategy, s_0, s_t):
    """Total P&L ($) of a strategy at expiry, and its buy-hold baseline.

    The baseline is buy-and-hold of the 100-share lot for the stock-holding structures; the
    cash-secured put has no stock leg, so its baseline is ``None`` — a "vs buy-hold" number there
    would compare option income against a zero that isn't a real alternative (misleading, not just
    uninformative).
    """
    shares = LOT_SHARES  # 1 contract lot throughout
    holds_stock = strategy.kind in _STOCK_KINDS
    stock = (s_t - s_0) * shares if holds_stock else 0.0
    options = sum(_leg_option_pnl(leg, s_t) * shares for leg in strategy.legs)
    return stock + options, (stock if holds_stock else None)


def _short_call_strike(strategy):
    """Strike of the short call (covered-call / collar upside cap), else None."""
    return next((leg.strike for leg in strategy.legs
                 if leg.option_type == "CALL" and leg.action == "sell"), None)


def _agg(records, kind):
    """Aggregate per-entry settlement records for one strategy kind."""
    rows = [r for r in records if kind in r["pnl"]]
    n = len(rows)
    if not n:
        return None
    pnl = [r["pnl"][kind] for r in rows]
    has_bh = all(r["buyhold"].get(kind) is not None for r in rows)  # CSP has no shares baseline
    diff = ([p - r["buyhold"][kind] for p, r in zip(pnl, rows, strict=True)] if has_bh else [])
    down = [r for r in rows if r["s_t"] < r["s_0"]]          # buy-hold losing entries
    protect = ([r["pnl"][kind] - r["buyhold"][kind] for r in down] if has_bh else [])
    protect = [x for x in protect if x > 0]                  # entries where the option beat buy-hold
    # "Capped winner" only applies to structures with a SHORT CALL (covered call, collar); the
    # protective put and CSP have no upside cap. Read each strategy's OWN cap (caps[kind]) — never
    # borrow another structure's short-call strike.
    capped = [r for r in rows if r.get("caps", {}).get(kind) is not None
              and r["s_t"] > r["caps"][kind]]
    cap_cost = [r["buyhold"][kind] - r["pnl"][kind] for r in capped]

    def _mean(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "n": n,
        "mean_pnl": _mean(pnl),
        "win_rate": round(sum(1 for p in pnl if p > 0) / n, 4),
        "mean_vs_buyhold": _mean(diff),
        "down_moves": len(down),
        "protection_type": _PROTECTION.get(kind),   # "hard floor" | "premium cushion" | None
        "beat_bh_down_n": len(protect),             # # down-move entries the option beat buy-hold
        "mean_downside_benefit": _mean(protect),
        "capped_winner_n": len(capped),
        "mean_cap_opportunity_cost": _mean(cap_cost),
    }


def _fmt(x):
    return f"{x:+,.0f}" if isinstance(x, (int, float)) else "  -  "


def show_menu(dates, highs, lows, closes, target_date, select="level"):
    """TASK 1: print the reconstructed adviser menu for one historical date."""
    # nearest trading bar on or before the requested date
    i = None
    for k in range(len(dates) - 1, -1, -1):
        if dates[k] <= target_date:
            i = k
            break
    if i is None or i < WARMUP:
        _log.console(f"No usable bar on/before {target_date} (need >= {WARMUP} bars of history).")
        return 1
    menu = _build_menu(i, dates, highs, lows, closes, select=select, require_settle=False)
    if menu is None:
        _log.console(f"Could not reconstruct a menu at {dates[i]} (insufficient vol history).")
        return 1
    synthetic = " [synthetic expiry — cache ends earlier]" if menu["j"] is None else ""
    _log.console("=" * 78)
    _log.console(f"ADVISER REPLAY — INTC as of {dates[i]}  (strike anchoring: {select})")
    _log.console(f"  spot ${menu['spot']:.2f} | realized vol {menu['sigma']*100:.1f}% | "
                 f"expiry {menu['expiry']} (~{(menu['expiry']-menu['d0']).days}d){synthetic}")
    _log.console(f"  {MODELED_CAVEAT}")
    _log.console("=" * 78)
    _log.console(oa.render_terminal(menu["report"]))
    return 0


def run(symbol="INTC"):
    """TASK 2: modeled-premium P&L backtest across sampled historical entries."""
    path = OHLCV / f"{symbol}_daily.json"
    if instruments.is_excluded(symbol):
        _log.console(f"{symbol} is excluded (leveraged/inverse/crypto) — long-option replay N/A.")
        return None
    data = _load(path)
    if data is None:
        _log.console(f"No usable OHLCV cache at {path}. Populate Data/Symbol_full/{symbol}_daily.json.")
        return None
    dates, highs, lows, closes = data

    records = []
    for i in range(WARMUP, len(dates), SAMPLE_EVERY):
        menu = _build_menu(i, dates, highs, lows, closes)
        if menu is None:
            continue
        s_0, s_t = menu["spot"], closes[menu["j"]]
        pnl, buyhold, caps = {}, {}, {}
        for s in menu["report"].strategies:
            total, bh = _settle(s, s_0, s_t)
            pnl[s.kind] = round(total, 2)
            buyhold[s.kind] = round(bh, 2) if bh is not None else None
            caps[s.kind] = _short_call_strike(s)   # each strategy's OWN upside cap (None if no short call)
        records.append({
            "date": dates[i], "expiry": str(menu["expiry"]), "s_0": s_0, "s_t": s_t,
            "sigma": round(menu["sigma"], 4),
            "caps": caps, "pnl": pnl, "buyhold": buyhold,
        })

    if not records:
        _log.console("No entries could be built (check history depth / forward runway).")
        return None

    # Buy-and-hold baseline over the same entries (uses the covered-lot share count).
    bh_pnl = [r["buyhold"].get("covered_call") for r in records
              if r["buyhold"].get("covered_call") is not None]
    bh_mean = round(sum(bh_pnl) / len(bh_pnl), 2) if bh_pnl else None
    bh_win = round(sum(1 for x in bh_pnl if x > 0) / len(bh_pnl), 4) if bh_pnl else None

    kinds = ("collar", "protective_put", "covered_call", "cash_secured_put")
    stats = {k: _agg(records, k) for k in kinds}

    # ── Report ──
    _log.console("=" * 92)
    _log.console(f"INTC OPTIONS-ADVISER P&L BACKTEST — {len(records)} entries "
                 f"({dates[WARMUP]} … {records[-1]['date']}), 1-contract (100-sh) lot")
    _log.console(f"  {MODELED_CAVEAT}")
    _log.console(f"  Buy-and-hold baseline: mean P&L {_fmt(bh_mean)} | win-rate "
                 f"{bh_win if bh_win is not None else '-'} (per {LOT_SHARES}-sh lot, entry→expiry)")
    _log.console("=" * 92)
    _log.console(f"{'strategy':<18}{'n':>5}{'mean P&L':>11}{'win':>7}{'vs B&H':>10}"
                 f"{'beat B&H(down)':>20}{'cap cost':>11}")
    _log.console("-" * 92)
    for k in kinds:
        s = stats[k]
        if not s:
            continue
        ptag = {"hard floor": "flr", "premium cushion": "cush"}.get(s.get("protection_type"), "")
        beat = (f"{s['beat_bh_down_n']}/{s['down_moves']} {_fmt(s['mean_downside_benefit'])} {ptag}"
                if s["down_moves"] and s.get("protection_type") else "  -  ")
        cap = (f"{s['capped_winner_n']}x {_fmt(s['mean_cap_opportunity_cost'])}"
               if s["capped_winner_n"] else "  -  ")
        _log.console(f"{k:<18}{s['n']:>5}{_fmt(s['mean_pnl']):>11}{s['win_rate']:>7.3f}"
                     f"{_fmt(s['mean_vs_buyhold']):>10}{beat:>20}{cap:>11}")
    _log.console("-" * 92)
    _log.console("beat B&H(down) = # down-move entries the option outcome beat buy-hold, & mean $ benefit;")
    _log.console("  flr = HARD FLOOR (put/collar cap the loss); cush = PREMIUM CUSHION (covered call only")
    _log.console("  softens the loss by the credit -- the stock still rides all the way down, NOT a floor).")
    _log.console("cap cost = # entries the strategy's OWN short call capped a winner, & mean $ upside given up")

    payload = {
        "meta": {
            "symbol": symbol, "entries": len(records),
            "first_date": dates[WARMUP], "last_date": records[-1]["date"],
            "sample_every": SAMPLE_EVERY, "dte_target": DTE_TARGET, "vol_window": VOL_WINDOW,
            "modeled": True, "caveat": MODELED_CAVEAT,
            "pricing": {"r": op.DEFAULT_RATE, "q": op.DEFAULT_DIV_YIELD,
                        "strike_step": op.DEFAULT_STRIKE_STEP, "vrp_mult": 1.0},
            "overlap_note": "weekly-sampled entries still overlap in holding window; treat "
                            "significance qualitatively, not as independent trials.",
            "buyhold": {"mean_pnl": bh_mean, "win_rate": bh_win},
        },
        "stats": stats,
        "entries_detail": records,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    _log.console(f"\nSaved {len(records)} entries + stats to {OUT_FILE}")
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description="INTC options-adviser historical replay + P&L backtest")
    ap.add_argument("--symbol", default="INTC")
    ap.add_argument("--show-menu", dest="show_menu", metavar="YYYY-MM-DD",
                    help="TASK 1: print the reconstructed adviser menu for one historical date")
    ap.add_argument("--select", choices=("level", "delta"), default="level",
                    help="strike anchoring for --show-menu (default level)")
    args = ap.parse_args(argv)

    data = _load(OHLCV / f"{args.symbol}_daily.json")
    if args.show_menu:
        if data is None:
            _log.console(f"No usable OHLCV cache for {args.symbol}.")
            return 1
        return show_menu(*data, args.show_menu, select=args.select)
    run(symbol=args.symbol)
    return 0


if __name__ == "__main__":
    sys.exit(main())
