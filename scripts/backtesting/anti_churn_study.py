"""
Anti-Churn Study — ledger counterfactual gating two over-trading fixes in the AI game.

Diagnosis (2026-09-29 PROD ledger review): the game turns over fast (median hold
6 days) through two paths no exit rule governs:

  A. MOMENTUM ROTATION (R&D #27, evaluate_momentum_rotation) sells a "mature"
     position whenever its S10+L60 < 8.0 (slots full) — while the real soft exit
     (sell_rules.soft_exit) only fires below 0 and is winner-protected. So the
     rotation path dumps in-profit names trading above their 50-DMA ("flowers")
     that sell_rules itself would have kept.
  B. RE-ENTRY: nothing keyed on the SYMBOL stops the engine re-buying a name it
     just closed at a loss (LULU: 3 losing round trips from Jul 29 to Sep 29).

Method — replay the actual game ledger (Data/ai_portfolio_game.json history):

  A. For every rotation SELL: was it in profit, was price >= its 50-DMA on the
     sell date (from the local OHLCV cache, bars <= sell date only — no
     look-ahead for the classification), and what did the stock do over the
     next 10/20 bars (the opportunity cost of selling). A rotation that the
     winner-protection rule would have blocked is "protected".
  B. For every BUY of a symbol that was closed at a LOSS within N days before,
     the realized P&L of that re-entry round trip. Sweep N; the counterfactual
     of a cooldown is simply not taking those trades (freed cash assumed idle —
     conservative, since the alternative buy is unknowable).

Caveat stated up front: this is ONE portfolio's ledger (tens of trades), not a
universe backtest. It can show direction and magnitude on the real failure, not
statistical significance; the gate below is therefore a sign + magnitude gate.

Gate:
  A. protected rotations must have a NON-NEGATIVE mean forward 10-bar return
     (selling them gave up upside) — otherwise the protection has no case. Also
     reported: the exit had the position been held under its trailing stop
     (held_vs_sell), since a protected position is not held unconditionally.
  B. the chosen N must have NEGATIVE total re-entry P&L (blocking them saves money).

Usage:
    python scripts/backtesting/anti_churn_study.py [--data-dir PATH]
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SWEEP_DAYS = [5, 10, 14, 21, 30]
FWD_BARS = [10, 20]
# Rotation only runs in AGGRESSIVE, whose stop is 3.5x ATR (get_strategy_rules); a
# held (protected) position would have trailed this stop, floored at cost (mature).
TRAIL_ATR_MULT = 3.5
TRAIL_MAX_BARS = 20


def _out(line: str) -> None:
    """Report table line — intentional CLI output, unprefixed."""
    sys.stdout.write(line + "\n")


def _bars(cache_dir: Path, sym: str) -> list[tuple[str, float, float, float]]:
    """(date, high, low, close) from the raw (not split-adjusted) OHLCV cache."""
    path = cache_dir / f"{sym}_daily.json"
    if not path.exists():
        return []
    ts = json.loads(path.read_text()).get("Time Series (Daily)", {})
    return [(d, float(ts[d]["2. high"]), float(ts[d]["3. low"]), float(ts[d]["4. close"]))
            for d in sorted(ts)]


_ADDS = ("BUY", "BUY_SCALE_IN")
_REMOVES = ("SELL", "OPTION_ASSIGNMENT")   # shares leave: exits, scale-outs, called away


def round_trips(history: list) -> list[dict]:
    """Group the ledger into per-symbol round trips by share count: a trip opens on the
    first add and closes when qty returns to 0. P&L sums every realized event in it
    (scale-outs, option premiums, assignment gains), so a partial is never a 'close'."""
    open_trips, trips = {}, []
    for tx in history:
        sym, kind = tx.get("symbol"), tx.get("type")
        trip = open_trips.get(sym)
        if kind in _ADDS:
            if trip is None:
                trip = open_trips[sym] = {"symbol": sym, "open_date": tx["date"], "qty": 0,
                                          "cost_usd": 0.0, "pnl": 0.0, "close_date": None}
            trip["qty"] += tx["qty"]
            trip["cost_usd"] += tx["qty"] * float(tx["price"])
        if trip is None:
            continue
        trip["pnl"] += tx.get("pnl") or 0.0
        if kind in _REMOVES:
            trip["qty"] -= tx["qty"]
            if trip["qty"] <= 0:
                trip["close_date"] = tx["date"]
                trips.append(open_trips.pop(sym))
    return trips + list(open_trips.values())


def _held_with_trail(bars, i, cost, entry_day):
    """Exit price had the position been held from bar i under a chandelier stop
    (highest close since entry - TRAIL_ATR_MULT*ATR14, floored at cost), else the
    close TRAIL_MAX_BARS later. Uses only bars <= i to seed the stop."""
    if i < 14:
        return None
    atr = sum(max(bars[k][1], bars[k - 1][3]) - min(bars[k][2], bars[k - 1][3])
              for k in range(i - 13, i + 1)) / 14
    high = max(c for d, _, _, c in bars[:i + 1] if d >= entry_day)
    for k in range(i + 1, min(i + 1 + TRAIL_MAX_BARS, len(bars))):
        stop = max(cost, high - TRAIL_ATR_MULT * atr)
        if bars[k][2] <= stop:
            return stop
        high = max(high, bars[k][3])
    last = min(i + TRAIL_MAX_BARS, len(bars) - 1)
    return bars[last][3] if last > i else None


def rotation_study(history: list, cache_dir: Path) -> dict:
    trips = round_trips(history)
    rows = []
    for tx in history:
        if tx.get("type") != "SELL" or "MOMENTUM ROTATION" not in str(tx.get("details", "")):
            continue
        sym, day, px = tx["symbol"], tx["date"], float(tx["price"])
        bars = _bars(cache_dir, sym)
        past = [c for d, _, _, c in bars if d <= day]
        future = [c for d, _, _, c in bars if d > day]
        trip = next((t for t in trips if t["symbol"] == sym and t["close_date"] == day), None)
        cost = trip["cost_usd"] / sum(b["qty"] for b in history if b["symbol"] == sym
                                      and b.get("type") in _ADDS
                                      and trip["open_date"] <= b["date"] <= day) if trip else None
        held = (_held_with_trail(bars, len(past) - 1, cost, trip["open_date"])
                if trip and past else None)
        sma50 = sum(past[-50:]) / 50 if len(past) >= 50 else None
        in_profit = (tx.get("pnl") or 0) > 0
        protected = in_profit and sma50 is not None and px >= sma50
        fwd = {f"fwd{n}": (round((future[n - 1] / px - 1) * 100, 2) if len(future) >= n else None)
               for n in FWD_BARS}
        rows.append({"symbol": sym, "date": day, "pnl": tx.get("pnl"), "price": px,
                     "sma50": round(sma50, 2) if sma50 else None,
                     "in_profit": in_profit, "protected": protected, **fwd,
                     "held_vs_sell": round((held / px - 1) * 100, 2) if held else None})

    def _mean(key, subset):
        vals = [r[key] for r in subset if r[key] is not None]
        return (round(sum(vals) / len(vals), 2), len(vals)) if vals else (None, 0)

    prot = [r for r in rows if r["protected"]]
    unprot = [r for r in rows if not r["protected"]]
    summary = {k: {"protected": _mean(k, prot), "unprotected": _mean(k, unprot)}
               for k in [f"fwd{n}" for n in FWD_BARS] + ["held_vs_sell"]}
    mean10 = summary["fwd10"]["protected"][0]
    return {"rows": rows, "summary": summary,
            "gate_pass": bool(prot) and mean10 is not None and mean10 >= 0}


def reentry_study(history: list) -> dict:
    """For each round trip opened within N days of the same symbol's previous trip
    closing at a loss, the re-entry trip's realized P&L — what a cooldown would skip."""
    trips = round_trips(history)
    sweep = {}
    for n_days in SWEEP_DAYS:
        blocked = []
        for i, trip in enumerate(trips):
            prev = [t for t in trips[:i] if t["symbol"] == trip["symbol"] and t["close_date"]
                    and t["close_date"] <= trip["open_date"]]
            if not prev:
                continue
            last = max(prev, key=lambda t: t["close_date"])
            gap = (datetime.date.fromisoformat(trip["open_date"])
                   - datetime.date.fromisoformat(last["close_date"])).days
            if last["pnl"] >= 0 or gap > n_days:
                continue
            blocked.append({"symbol": trip["symbol"], "buy_date": trip["open_date"], "gap_days": gap,
                            "round_trip_pnl": round(trip["pnl"], 2) if trip["close_date"] else None})
        closed = [b["round_trip_pnl"] for b in blocked if b["round_trip_pnl"] is not None]
        sweep[n_days] = {"n_blocked": len(blocked), "n_closed": len(closed),
                         "total_pnl": round(sum(closed), 2),
                         "losers": sum(1 for p in closed if p < 0),
                         "trades": blocked}
    return sweep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(BASE_DIR / "Data"))
    args = ap.parse_args()
    data = Path(args.data_dir)
    history = json.loads((data / "ai_portfolio_game.json").read_text()).get("history", [])

    rot = rotation_study(history, data / "Symbol_full")
    _out("A. MOMENTUM ROTATION sells")
    for r in rot["rows"]:
        _out(f"  {r['date']} {r['symbol']:6s} pnl={r['pnl']:>8} px={r['price']:>8} sma50={r['sma50']} "
             f"protected={r['protected']!s:5s} fwd10={r['fwd10']} fwd20={r['fwd20']} "
             f"held_w_trail_vs_sell={r['held_vs_sell']}")
    for k, v in rot["summary"].items():
        _out(f"  {k}: protected mean {v['protected'][0]}% (n={v['protected'][1]}) | "
             f"unprotected mean {v['unprotected'][0]}% (n={v['unprotected'][1]})")
    _out(f"  GATE A: {'PASS' if rot['gate_pass'] else 'FAIL'}")

    _out("\nB. RE-ENTRY after a loss exit (counterfactual = skip the re-entry)")
    sweep = reentry_study(history)
    for n, s in sweep.items():
        _out(f"  N={n:>2}d: blocked {s['n_blocked']} ({s['n_closed']} closed, {s['losers']} losers) "
             f"re-entry P&L {s['total_pnl']:+.2f}")
    for t in sweep[max(SWEEP_DAYS)]["trades"]:
        _out(f"    {t['buy_date']} {t['symbol']:6s} gap={t['gap_days']:>2}d pnl={t['round_trip_pnl']}")


if __name__ == "__main__":
    main()
