"""
Part G — decision logging + backtracking scorecard.

Logs each exit decision (deterministic rules action + shadow AI verdicts from every
enabled provider) to Data/decision_log.jsonl, then scores each selector against the
N-day-forward close so provider choice is backed by data, not a single anecdote.

Scores every close, gains included: a SELL of a position in profit that then rose is
a winner-selling miss (the "cutting a flower" error) — outcome is not the same as
decision quality (CLAUDE.md, Jul-25 principle 6).

score_log takes an injectable forward-price function so it is unit-testable without
real data.
"""

import json
from pathlib import Path

from aether import sell_rules

_DIR = Path(__file__).resolve().parent.parent
LOG = _DIR / "Data" / "decision_log.jsonl"
OHLCV_DIR = _DIR / "Data" / "Symbol_full"
_MAX_LOG_LINES = 5000   # bound the append-only log; keep the most recent decisions


# ── Logging ────────────────────────────────────────────────────────────────────

def build_entry(symbol, price, cost, stop_loss, s10, l60, sma50=None,
                pgr=None, patterns=None, date=None, run_shadow=None):
    """Compute the deterministic action (the single source of truth for the caller)
    and, when it's an exit candidate, shadow AI verdicts from every enabled provider.
    Returns one loggable decision record.

    run_shadow: None = auto (shadow only when the action isn't HOLD), or force with
    True/False. The reference price is the live decision-time quote — the same price
    the sim would transact at — measured later against the forward daily close."""
    action, reason = sell_rules.exit_decision(
        price=price, cost=cost, stop_loss=stop_loss, s10=s10, l60=l60, sma50=sma50)
    if run_shadow is None:
        run_shadow = (action != "HOLD")
    pnl_pct = ((price - cost) / cost * 100) if (price and cost) else None
    entry = {
        "date": date, "symbol": symbol, "price": price, "cost": cost,
        "s10": s10, "l60": l60, "pnl_pct": pnl_pct,
        "rules_action": action, "rules_reason": reason, "verdicts": {},
    }
    if run_shadow:
        import ai_client
        import sell_eval
        ctx = {"symbol": symbol, "action": action, "reason": reason, "cost": cost,
               "price": price, "pnl_pct": pnl_pct, "s10": s10, "l60": l60,
               "stop": stop_loss, "sma50": sma50, "pgr": pgr, "patterns": patterns}
        for prov in ai_client.enabled_providers():
            v = sell_eval.evaluate_exit(ctx, provider=prov)
            if v:
                entry["verdicts"][prov] = {"verdict": v["verdict"], "note": v.get("note", "")}
    return entry


def log_decisions(entries, path=LOG):
    """Append decision records as JSON lines (best-effort; never raises)."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        _trim_log(path)
    except Exception as e:
        print(f"[decision_eval] log write failed: {e}")


def _trim_log(path, max_lines=_MAX_LOG_LINES):
    """Keep the append-only log bounded to the most recent max_lines so it can't
    grow without limit. No-op while under the cap."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) <= max_lines:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines[-max_lines:])


def read_log(path=LOG):
    """Read the append-only log, skipping blank or malformed lines (a crash can
    leave a half-written final line — one bad row must not sink the scorecard)."""
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return entries


# ── Forward price lookup ───────────────────────────────────────────────────────

def ohlcv_forward_close(symbol, from_date, horizon_days):
    """Close exactly horizon trading days after from_date from the OHLCV cache.
    Returns None until the full horizon of forward data exists, so a decision is
    never scored on an immature window (matches the dashboard's "needs ~N days")."""
    if not symbol or not from_date:
        return None
    path = OHLCV_DIR / f"{symbol}_daily.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except Exception:
        return None
    fut = [d for d in sorted(ts.keys()) if d > from_date]
    if len(fut) < horizon_days:
        return None
    try:
        return float(ts[fut[horizon_days - 1]]["4. close"])
    except (KeyError, ValueError, TypeError):
        return None


# ── Scoring ────────────────────────────────────────────────────────────────────

_SELL_ACTIONS = {"SELL"}
_HOLD_ACTIONS = {"HOLD", "REVIEW"}


def _rules_correct(action, ret):
    """A SELL is correct if the name fell (ret<0); a HOLD/REVIEW is correct if it
    held or rose (ret>=0)."""
    if action in _SELL_ACTIONS:
        return ret < 0
    if action in _HOLD_ACTIONS:
        return ret >= 0
    return None


def score_log(entries, horizon_days=10, fwd_price_fn=ohlcv_forward_close):
    """Score every decision that has forward data. Returns a per-selector scorecard
    plus the winner-selling miss list. `fwd_price_fn(symbol, date, horizon)->price`
    is injectable for testing."""
    selectors = {}  # name -> counters

    def bump(name, **kw):
        s = selectors.setdefault(name, {"scored": 0, "correct": 0,
                                        "winner_sell_miss": 0,
                                        "missed_upside": 0.0, "avoided_loss": 0.0})
        for k, v in kw.items():
            s[k] += v

    misses = []  # notable winner-selling misses across all selectors (rules)

    # Collapse intraday/manual re-runs: the pipeline can log a symbol several times
    # on the same day (scheduled run + a manual "Run Pipeline"), so keep only the
    # last (final) decision per symbol per day — the raw log stays as an audit trail.
    deduped = {}
    for e in entries:
        sym, day = e.get("symbol"), e.get("date")
        if not sym or not day:
            continue  # a row with no symbol/date can't be scored against forward data
        deduped[(sym, day)] = e

    for e in deduped.values():
        price = e.get("price"); cost = e.get("cost")
        if not price:
            continue
        fwd = fwd_price_fn(e.get("symbol"), e.get("date"), horizon_days)
        if fwd is None:
            continue
        ret = (fwd - price) / price
        action = e.get("rules_action")

        # ── rules selector ──
        rc = _rules_correct(action, ret)
        if rc is not None:
            bump("rules", scored=1, correct=1 if rc else 0)
            if action in _SELL_ACTIONS:
                if ret > 0:
                    bump("rules", missed_upside=ret)
                    # winner-selling miss: sold something in profit that then rose
                    if cost and price > cost:
                        bump("rules", winner_sell_miss=1)
                        misses.append({"symbol": e.get("symbol"), "date": e.get("date"),
                                       "reason": e.get("rules_reason"),
                                       "fwd_return_pct": round(ret * 100, 1)})
                else:
                    bump("rules", avoided_loss=-ret)

        # ── each AI provider verdict ──
        # A verdict is "useful/correct" when it aligns with reality:
        #   FLAG-FOR-REVIEW is right when the rules action was ultimately wrong;
        #   AGREE is right when the rules action was ultimately right.
        for prov, v in (e.get("verdicts") or {}).items():
            verdict = v.get("verdict")
            if verdict not in ("AGREE", "FLAG-FOR-REVIEW") or rc is None:
                continue
            useful = (verdict == "FLAG-FOR-REVIEW" and not rc) or \
                     (verdict == "AGREE" and rc)
            bump(prov, scored=1, correct=1 if useful else 0)

    # finalize hit rates
    out = {}
    for name, s in selectors.items():
        hit = round(s["correct"] / s["scored"] * 100, 1) if s["scored"] else 0.0
        out[name] = {
            "scored": s["scored"], "correct": s["correct"], "hit_rate": hit,
            "winner_sell_miss": s["winner_sell_miss"],
            "missed_upside_pct": round(s["missed_upside"] * 100, 1),
            "avoided_loss_pct": round(s["avoided_loss"] * 100, 1),
        }
    return {"selectors": out,
            "winner_selling_misses": sorted(misses, key=lambda m: m["fwd_return_pct"], reverse=True)}


def reflection(scorecard) -> str:
    """Human-readable reflection over every close (gains included)."""
    lines = ["=== Decision Scorecard (backtracked) ==="]
    for name, s in scorecard["selectors"].items():
        lines.append(
            f"  {name:12} scored={s['scored']:>4} hit={s['hit_rate']:>5}%  "
            f"winner-sell misses={s['winner_sell_miss']}  "
            f"missed-upside={s['missed_upside_pct']}%  avoided-loss={s['avoided_loss_pct']}%")
    misses = scorecard["winner_selling_misses"]
    if misses:
        lines.append("  Top winner-selling misses (sold in profit, then rose):")
        for m in misses[:5]:
            lines.append(f"    {m['symbol']}: +{m['fwd_return_pct']}% after exit ({m['date']})")
    else:
        lines.append("  No winner-selling misses in the scored window.")
    return "\n".join(lines)


if __name__ == "__main__":
    sc = score_log(read_log())
    print(reflection(sc))
