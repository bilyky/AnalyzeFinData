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
     just closed at a loss (LULU: 3 losing round trips in 8 weeks).

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
     (selling them gave up upside) — otherwise the protection has no case.
  B. the chosen N must have NEGATIVE total re-entry P&L (blocking them saves money).

Usage:
    python scripts/backtesting/anti_churn_study.py [--data-dir PATH]
"""
import argparse
import datetime
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SWEEP_DAYS = [5, 10, 14, 21, 30]
FWD_BARS = [10, 20]


def _closes(cache_dir: Path, sym: str) -> list[tuple[str, float]]:
    path = cache_dir / f"{sym}_daily.json"
    if not path.exists():
        return []
    ts = json.loads(path.read_text()).get("Time Series (Daily)", {})
    return [(d, float(ts[d]["4. close"])) for d in sorted(ts)]


def rotation_study(history: list, cache_dir: Path) -> dict:
    rows = []
    for tx in history:
        if tx.get("type") != "SELL" or "MOMENTUM ROTATION" not in str(tx.get("details", "")):
            continue
        sym, day, px = tx["symbol"], tx["date"], float(tx["price"])
        bars = _closes(cache_dir, sym)
        past = [c for d, c in bars if d <= day]
        future = [c for d, c in bars if d > day]
        sma50 = sum(past[-50:]) / 50 if len(past) >= 50 else None
        in_profit = (tx.get("pnl") or 0) > 0
        protected = in_profit and sma50 is not None and px >= sma50
        fwd = {f"fwd{n}": (round((future[n - 1] / px - 1) * 100, 2) if len(future) >= n else None)
               for n in FWD_BARS}
        rows.append({"symbol": sym, "date": day, "pnl": tx.get("pnl"), "price": px,
                     "sma50": round(sma50, 2) if sma50 else None,
                     "in_profit": in_profit, "protected": protected, **fwd})

    def _mean(key, subset):
        vals = [r[key] for r in subset if r[key] is not None]
        return (round(sum(vals) / len(vals), 2), len(vals)) if vals else (None, 0)

    prot = [r for r in rows if r["protected"]]
    unprot = [r for r in rows if not r["protected"]]
    summary = {k: {"protected": _mean(k, prot), "unprotected": _mean(k, unprot)}
               for k in (f"fwd{n}" for n in FWD_BARS)}
    mean10 = summary["fwd10"]["protected"][0]
    return {"rows": rows, "summary": summary,
            "gate_pass": bool(prot) and mean10 is not None and mean10 >= 0}


def reentry_study(history: list) -> dict:
    """Pair each BUY with the most recent prior loss-exit of the same symbol and
    with the SELL that eventually closes it (its realized round-trip P&L)."""
    sweep = {}
    events = [tx for tx in history if tx.get("type") in ("BUY", "SELL")]
    for n_days in SWEEP_DAYS:
        blocked = []
        for i, tx in enumerate(events):
            if tx["type"] != "BUY":
                continue
            sym = tx["symbol"]
            buy_day = datetime.date.fromisoformat(tx["date"])
            last_loss = None
            for prev in reversed(events[:i]):
                if prev["symbol"] == sym and prev["type"] == "SELL":
                    if (prev.get("pnl") or 0) < 0:
                        last_loss = datetime.date.fromisoformat(prev["date"])
                    break  # only the most recent close of this symbol matters
            if last_loss is None or (buy_day - last_loss).days > n_days:
                continue
            exit_tx = next((e for e in events[i + 1:]
                            if e["symbol"] == sym and e["type"] == "SELL"), None)
            blocked.append({"symbol": sym, "buy_date": tx["date"],
                            "gap_days": (buy_day - last_loss).days,
                            "round_trip_pnl": exit_tx.get("pnl") if exit_tx else None,
                            "still_open": exit_tx is None})
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
    print("A. MOMENTUM ROTATION sells")
    for r in rot["rows"]:
        print(f"  {r['date']} {r['symbol']:6s} pnl={r['pnl']:>8} px={r['price']:>8} sma50={r['sma50']} "
              f"protected={r['protected']!s:5s} fwd10={r['fwd10']} fwd20={r['fwd20']}")
    for k, v in rot["summary"].items():
        print(f"  {k}: protected mean {v['protected'][0]}% (n={v['protected'][1]}) | "
              f"unprotected mean {v['unprotected'][0]}% (n={v['unprotected'][1]})")
    print(f"  GATE A: {'PASS' if rot['gate_pass'] else 'FAIL'}")

    print("\nB. RE-ENTRY after a loss exit (counterfactual = skip the re-entry)")
    sweep = reentry_study(history)
    for n, s in sweep.items():
        print(f"  N={n:>2}d: blocked {s['n_blocked']} ({s['n_closed']} closed, {s['losers']} losers) "
              f"re-entry P&L {s['total_pnl']:+.2f}")
    for t in sweep[max(SWEEP_DAYS)]["trades"]:
        print(f"    {t['buy_date']} {t['symbol']:6s} gap={t['gap_days']:>2}d pnl={t['round_trip_pnl']}")


if __name__ == "__main__":
    main()
