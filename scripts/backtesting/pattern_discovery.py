"""
AETHER Pattern Discovery — Historical Replay + Missed Winners Analysis.

Usage:
    python scripts/backtesting/pattern_discovery.py --date 2026-03-01
    python scripts/backtesting/pattern_discovery.py --date 2026-03-01 --top 15
    python scripts/backtesting/pattern_discovery.py --date 2026-03-01 --validate --date-range 2025-06-01:2026-02-28

What it does:
    1. Reconstructs every symbol's AETHER scores as they were on --date
       (reads from the Chaikin per-symbol cache — no live API needed)
    2. Computes actual 10-day AND 60-day forward returns from OHLCV
    3. Identifies the top N actual winners and classifies each as:
       "bought" (system would have selected it), "missed" (was in universe but
       score/filter rejected it), or "not_in_universe" (no Chaikin cache that day)
    4. For each missed winner: records which factor(s) caused the rejection
       and how anomalous those factors were vs the universe median
    5. Extracts candidate new patterns (factor combos that predicted wins
       but are currently absent or penalized in the scoring formula)
    6. Optionally validates candidate patterns across a date range
    7. Writes Data/pattern_discovery_{DATE}.json for the agent skill to reason over

Run weekly on Saturday, targeting a date ~3-4 weeks back so both the 10-day and
60-day windows have fully settled OHLCV data.
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import powergauge
import instruments
from aether.scoring import short_score as _short_score, long_score as _long_score

BASE_DIR  = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = BASE_DIR / "Data" / "Symbol"
OHLCV_DIR = BASE_DIR / "Data" / "Symbol_full"
OUT_DIR   = BASE_DIR / "Data"

# Screener thresholds mirroring ai_portfolio_game.py BALANCED profile
SCORE_THRESHOLD_S10 = 10.0   # min S10+L60 for a BUY in BALANCED
SCORE_THRESHOLD_DEF =  9.5   # min S10+L60 for DEFENSIVE
PGR_REQUIRED        = {4, 5}  # Bu / Bu+  (pgr_corrected_value)
HORIZONS            = {"s10": 10, "l60": 60}


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_ohlcv(sym: str) -> tuple[dict, list[str]]:
    path = OHLCV_DIR / f"{sym}_daily.json"
    if not path.exists():
        return {}, []
    with open(path) as f:
        ts = json.load(f).get("Time Series (Daily)", {})
    return ts, sorted(ts.keys())


def _fwd_return(ts: dict, dates: list[str], replay_date: str, horizon: int) -> float | None:
    try:
        idx = dates.index(replay_date)
    except ValueError:
        # replay_date not a trading day — find the next available
        candidates = [d for d in dates if d >= replay_date]
        if not candidates:
            return None
        idx = dates.index(candidates[0])
    future_idx = idx + horizon
    if future_idx >= len(dates):
        return None
    entry_close = float(ts[dates[idx]].get("4. close", 0))
    fwd_close   = float(ts[dates[future_idx]].get("4. close", 0))
    if entry_close <= 0 or fwd_close <= 0:
        return None
    return round((fwd_close - entry_close) / entry_close * 100, 3)


def _get_cached_dates(sym: str) -> list[str]:
    sym_dir = CACHE_DIR / sym
    if not sym_dir.exists():
        return []
    return sorted(
        f.stem.replace(f"{sym}_", "")
        for f in sym_dir.glob(f"{sym}_*.json")
    )


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(v for v in vals if v is not None)
    n = len(s)
    return s[n // 2] if n else 0.0


def _z(value: float, median: float, stdev: float) -> float:
    if stdev < 1e-9:
        return 0.0
    return (value - median) / stdev


def _stdev(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


# ── Phase 1: reconstruct scores ───────────────────────────────────────────────

def reconstruct_scores(replay_date: str, symbols: list[str] | None = None) -> list[dict]:
    """Reconstruct every symbol's AETHER scores as of replay_date."""
    if symbols is None:
        symbols = [d.name for d in CACHE_DIR.iterdir() if d.is_dir()]

    powergauge._build_cache_index()

    results = []
    for sym in symbols:
        if instruments.is_excluded(sym):
            continue
        cached = _get_cached_dates(sym)
        if not cached:
            continue
        # Find closest cached date on or before replay_date
        available = [d for d in cached if d <= replay_date]
        if not available:
            continue
        use_date = available[-1]
        # Skip if cached snapshot is more than 5 trading days stale
        replay_dt  = date.fromisoformat(replay_date)
        use_dt     = date.fromisoformat(use_date)
        if (replay_dt - use_dt).days > 7:
            continue

        try:
            pg = powergauge.get_symbol_data(sym, date.fromisoformat(use_date),
                                             prefer_cache=True, session_id={})
            if not pg or pg.price <= 0:
                continue
            ts, all_dates = _load_ohlcv(sym)
            ohlcv_ts = ts
            f = powergauge._compute_pgr_fields(pg, ohlcv_ts=ohlcv_ts)
        except Exception:
            continue

        s10 = f.get("short_score", 0.0)
        l60 = f.get("long_score",  0.0)
        results.append({
            "symbol":           sym,
            "cache_date":       use_date,
            "price":            pg.price,
            "pgr":              f.get("pgr", "N"),
            "pgr_val":          pg.pgr_corrected_value,
            "s10":              round(s10, 2),
            "l60":              round(l60, 2),
            "combined":         round(s10 + l60, 2),
            "buying_ratio":     round(f.get("buying_ratio", 0.0), 2),
            "money_flow":       pg.money_flow   or "Neutral",
            "ob_os":            pg.over_bt_sl   or "Neutral",
            "lt_trend":         pg.lt_trend     or "Neutral",
            "industry":         pg.industry_name or "",
            "industry_strength":pg.industry_strength or "Neutral",
            "rel_vol":          f.get("rel_vol"),
            "fibonacci":        round(f.get("fibonacci", 0.0), 3),
            "rsi_divergence":   round(f.get("rsi_divergence", 0.0), 3),
            "candlestick":      round(f.get("candlestick_score", 0.0), 2),
            "chart_score":      round(f.get("chart_score", 0.0), 2),
            "momentum_score":   round(f.get("momentum_score", 0.0), 2),
            "pattern_text":     f.get("pattern_text", ""),
            "digit_sum":        round(f.get("digit_sum", 0.0), 2),
            "setup_ok":         f.get("setup_ok"),
        })

    print(f"  Reconstructed {len(results)} symbols with Chaikin cache on {replay_date}")
    return results


# ── Phase 2: forward returns ──────────────────────────────────────────────────

def compute_forward_returns(replay_date: str, symbols: list[str]) -> dict[str, dict]:
    """Compute 10d and 60d forward returns from OHLCV for each symbol."""
    returns = {}
    for sym in symbols:
        ts, dates = _load_ohlcv(sym)
        if not dates:
            continue
        r10 = _fwd_return(ts, dates, replay_date, HORIZONS["s10"])
        r60 = _fwd_return(ts, dates, replay_date, HORIZONS["l60"])
        if r10 is not None or r60 is not None:
            returns[sym] = {"r10": r10, "r60": r60}
    print(f"  Forward returns: {sum(1 for v in returns.values() if v['r10'] is not None)} symbols with 10d, "
          f"{sum(1 for v in returns.values() if v['r60'] is not None)} with 60d")
    return returns


# ── Phase 3: missed winners ───────────────────────────────────────────────────

def _would_have_bought(row: dict, threshold: float = SCORE_THRESHOLD_DEF) -> bool:
    return (row["combined"] >= threshold
            and row["pgr_val"] in PGR_REQUIRED
            and row.get("setup_ok") is not False)


def _rejection_reasons(row: dict, threshold: float = SCORE_THRESHOLD_DEF) -> list[str]:
    reasons = []
    if row["combined"] < threshold:
        reasons.append(f"score={row['combined']:.1f} < {threshold}")
    if row["pgr_val"] not in PGR_REQUIRED:
        reasons.append(f"pgr={row['pgr']} not Bu/Bu+")
    if row.get("setup_ok") is False:
        reasons.append("setup=False")
    return reasons or ["passed_all_filters"]


def find_missed_winners(scores: list[dict], returns: dict[str, dict],
                        top_n: int = 10, horizon: str = "s10") -> dict:
    """Classify top-N actual winners as bought/missed/not_in_universe."""
    r_key = "r10" if horizon == "s10" else "r60"
    threshold = SCORE_THRESHOLD_DEF

    score_map = {r["symbol"]: r for r in scores}

    # Rank all symbols with return data
    ranked = sorted(
        [(sym, v[r_key]) for sym, v in returns.items() if v.get(r_key) is not None],
        key=lambda x: x[1], reverse=True
    )

    universe_returns = [v[r_key] for v in returns.values() if v.get(r_key) is not None]
    universe_avg = sum(universe_returns) / len(universe_returns) if universe_returns else 0.0

    winners = []
    for sym, ret in ranked[:top_n]:
        row = score_map.get(sym)
        if row is None:
            status = "not_in_universe"
            reasons = ["no_chaikin_cache"]
            entry = {}
        elif _would_have_bought(row, threshold):
            status = "bought"
            reasons = ["passed_all_filters"]
            entry = row
        else:
            status = "missed"
            reasons = _rejection_reasons(row, threshold)
            entry = row

        winners.append({
            "symbol":   sym,
            f"fwd_{r_key}": round(ret, 2),
            "lift_vs_universe": round(ret - universe_avg, 2),
            "status":   status,
            "reasons":  reasons,
            "score":    row.get("combined") if row else None,
            "pgr":      row.get("pgr")      if row else None,
            "factors":  entry,
        })

    missed = [w for w in winners if w["status"] == "missed"]
    bought = [w for w in winners if w["status"] == "bought"]

    print(f"  [{horizon.upper()}] top-{top_n} winners: {len(bought)} would-have-bought, "
          f"{len(missed)} missed, "
          f"{len(winners)-len(bought)-len(missed)} not in universe")
    print(f"  Universe avg {r_key}: {universe_avg:+.2f}%")

    # False positives: symbols the system would have bought that lost money
    false_positives = []
    for row in scores:
        sym = row["symbol"]
        ret = returns.get(sym, {}).get(r_key)
        if ret is None:
            continue
        if _would_have_bought(row, threshold) and ret < 0:
            false_positives.append({
                "symbol":  sym,
                f"fwd_{r_key}": round(ret, 2),
                "score":   row.get("combined"),
                "pgr":     row.get("pgr"),
                "factors": row,
            })
    false_positives.sort(key=lambda x: x.get(f"fwd_{r_key}", 0))

    if false_positives:
        print(f"  [{horizon.upper()}] false positives (bought but lost): {len(false_positives)} "
              f"(worst: {false_positives[0]['symbol']} {false_positives[0].get(f'fwd_{r_key}', 0):+.1f}%)")

    return {
        "horizon":         horizon,
        "r_key":           r_key,
        "universe_avg":    round(universe_avg, 3),
        "winners":         winners,
        "missed":          missed,
        "bought":          bought,
        "false_positives": false_positives[:10],
    }


def extract_false_positive_patterns(false_positives: list[dict], all_scores: list[dict],
                                    returns: dict[str, dict], r_key: str) -> list[dict]:
    """Find factor signatures common to false positives — candidate new guard signals."""
    if not false_positives:
        return []

    candidates = []
    # Common factor values across false positives
    factor_counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for fp in false_positives:
        f = fp.get("factors", {})
        for k in ["money_flow", "ob_os", "lt_trend", "industry_strength", "rel_vol"]:
            v = f.get(k)
            if v:
                factor_counts[k][v] += 1

    n_fp = len(false_positives)
    for factor, counts in factor_counts.items():
        for level, cnt in counts.items():
            if cnt / n_fp >= 0.4 and cnt >= 2:
                # This level appeared in >= 40% of false positives
                # Check if the same level in the broader universe has poor returns
                universe_with_level = [
                    returns.get(r["symbol"], {}).get(r_key)
                    for r in all_scores
                    if r.get(factor) == level and returns.get(r["symbol"], {}).get(r_key) is not None
                ]
                if len(universe_with_level) >= 5:
                    avg_ret = sum(universe_with_level) / len(universe_with_level)
                    candidates.append({
                        "type":       "false_positive_guard",
                        "factor":     factor,
                        "value":      level,
                        "fp_count":   cnt,
                        "fp_rate":    round(cnt / n_fp, 2),
                        "universe_avg_with_signal": round(avg_ret, 2),
                        "hypothesis": (
                            f"{factor}={level} appeared in {cnt}/{n_fp} false positives "
                            f"and universe avg return with this signal is {avg_ret:+.1f}% — "
                            f"consider adding as a soft rejection filter"
                        ),
                    })

    return sorted(candidates, key=lambda x: -x["fp_rate"])


# ── Phase 4: candidate patterns ───────────────────────────────────────────────

# Factors currently penalized as contrarian (value=high → system scores it lower)
CONTRARIAN_FACTORS = {
    "lt_trend":        {"Strong": -1.5, "Weak": +1.5},
    "industry_strength": {"Strong": -2.0, "Weak": +2.0},
    "candlestick":     "negative_weight",
    "chart_score":     "negative_weight",
    "momentum_score":  "negative_weight",
}

# Factors with known directional signal
DIRECTIONAL_FACTORS = {
    "money_flow":      {"Strong": +3.0},
    "ob_os":           {"Optimal": +3.0, "Wait": -2.0},
    "rel_vol":         {"High": +2.5, "Very High": +0.5, "Low": -2.0},
    "fibonacci":       "positive",
    "rsi_divergence":  "positive",
    "digit_sum":       "positive",
}


def extract_candidate_patterns(missed: list[dict], all_scores: list[dict],
                                returns: dict[str, dict], r_key: str) -> list[dict]:
    """Find factor combinations that predicted wins but are absent/penalized."""
    if not missed:
        return []

    # Universe medians for anomaly detection
    factor_vals: dict[str, list] = defaultdict(list)
    for row in all_scores:
        for k in ["s10", "l60", "combined", "buying_ratio", "fibonacci",
                  "rsi_divergence", "candlestick", "chart_score", "momentum_score", "digit_sum"]:
            v = row.get(k)
            if v is not None:
                factor_vals[k].append(v)

    medians = {k: _median(v) for k, v in factor_vals.items()}
    stdevs  = {k: _stdev(v)  for k, v in factor_vals.items()}

    candidates = []

    # — Pattern 1: contrarian factor was actually bullish ———————————————————
    for w in missed:
        f = w.get("factors", {})
        if not f:
            continue
        ret = w.get(f"fwd_{r_key}", 0.0)
        for factor, cfg in CONTRARIAN_FACTORS.items():
            val = f.get(factor)
            if val is None:
                continue
            if isinstance(cfg, dict):
                # categorical: check if the winner had the "penalized" value
                for level, weight in cfg.items():
                    if val == level and weight < 0:
                        candidates.append({
                            "type":         "contrarian_reversal",
                            "factor":       factor,
                            "value":        level,
                            "current_weight": weight,
                            "winner":       w["symbol"],
                            "return":       ret,
                            "hypothesis":   f"{factor}={level} is penalized ({weight:+.1f}) but this winner had it — may be sector/regime conditional",
                        })
            elif cfg == "negative_weight":
                z = _z(float(val), medians.get(factor, 0), stdevs.get(factor, 1))
                if z > 1.0 and ret > 5.0:
                    candidates.append({
                        "type":       "high_contrarian_factor",
                        "factor":     factor,
                        "value":      round(float(val), 2),
                        "z_vs_universe": round(z, 2),
                        "winner":     w["symbol"],
                        "return":     ret,
                        "hypothesis": f"High {factor} (z={z:.1f}) currently penalized but winner had strong return — may need sector-conditional weighting",
                    })

    # — Pattern 2: combination of moderate signals that together predict ——————
    # Test: money_flow=Strong + chart_score>0 (currently both penalized indirectly)
    combo_hits = []
    for row in all_scores:
        sym = row["symbol"]
        if row.get("money_flow") == "Strong" and row.get("chart_score", 0) > 0:
            ret = returns.get(sym, {}).get(r_key)
            if ret is not None:
                combo_hits.append((sym, ret, row.get("combined", 0)))
    if len(combo_hits) >= 5:
        avg_ret = sum(r for _, r, _ in combo_hits) / len(combo_hits)
        candidates.append({
            "type":           "combo_signal",
            "name":           "StrongMF+PositiveChart",
            "condition":      {"money_flow": "Strong", "chart_score": ">0"},
            "n":              len(combo_hits),
            "avg_return":     round(avg_ret, 2),
            "hypothesis":     "Strong money flow combined with positive chart pattern score — the contrarian chart weight may be too aggressive when MF confirms",
        })

    # — Pattern 3: high buying_ratio but low combined score (momentum override) —
    br_hits = []
    for row in all_scores:
        sym = row["symbol"]
        if row.get("buying_ratio", 0) >= 6.0 and row.get("combined", 0) < SCORE_THRESHOLD_DEF:
            ret = returns.get(sym, {}).get(r_key)
            if ret is not None:
                br_hits.append((sym, ret, row.get("combined", 0), row.get("buying_ratio", 0)))
    if len(br_hits) >= 5:
        avg_ret = sum(r for _, r, _, _ in br_hits) / len(br_hits)
        candidates.append({
            "type":       "br_override",
            "name":       "HighBR_LowScore",
            "condition":  {"buying_ratio": ">=6.0", "combined": f"<{SCORE_THRESHOLD_DEF}"},
            "n":          len(br_hits),
            "avg_return": round(avg_ret, 2),
            "hypothesis": "High buying ratio but composite score below threshold — BR alone may have predictive power; consider a BR-override gate",
        })

    # — Pattern 4: digit_sum signal firing for missed winners ——————————————
    ds_hits = []
    for w in missed:
        f = w.get("factors", {})
        ds = f.get("digit_sum", 0.0)
        if abs(ds) >= 0.3:
            ds_hits.append((w["symbol"], w.get(f"fwd_{r_key}", 0.0), ds))
    if ds_hits:
        avg_ret = sum(r for _, r, _ in ds_hits) / len(ds_hits)
        candidates.append({
            "type":       "digit_sum_miss",
            "name":       "DigitSumSignalOnMissedWinner",
            "n":          len(ds_hits),
            "avg_return": round(avg_ret, 2),
            "symbols":    [s for s, _, _ in ds_hits[:5]],
            "hypothesis": "Digit-sum signal was firing on missed winners — may be underweighted at ±1.0 in S10",
        })

    return candidates


# ── Phase 5: validate patterns across date range ──────────────────────────────

def validate_patterns(candidates: list[dict], date_range: tuple[str, str],
                      all_symbols: list[str], horizon: str = "s10") -> list[dict]:
    """Roll candidate patterns across multiple historical dates."""
    r_key = "r10" if horizon == "s10" else "r60"
    start, end = date_range
    # Sample ~15 dates evenly across range
    start_d = date.fromisoformat(start)
    end_d   = date.fromisoformat(end)
    total_days = (end_d - start_d).days
    step = max(total_days // 15, 7)
    sample_dates = []
    cur = start_d
    while cur <= end_d:
        sample_dates.append(cur.isoformat())
        cur += timedelta(days=step)

    validated = []
    for cand in candidates:
        name = cand.get("name", cand.get("factor", "unknown"))
        print(f"  Validating '{name}' across {len(sample_dates)} dates...")
        hits_ret = []
        baseline_ret = []

        for d_str in sample_dates:
            # Use only OHLCV-covered dates
            horizon_days = HORIZONS[horizon]
            replay_dt = date.fromisoformat(d_str)
            if (date(2026, 4, 24) - replay_dt).days < horizon_days:
                continue
            scores = reconstruct_scores(d_str, all_symbols)
            fwd    = compute_forward_returns(d_str, [r["symbol"] for r in scores])

            for row in scores:
                sym = row["symbol"]
                ret = fwd.get(sym, {}).get(r_key)
                if ret is None:
                    continue
                baseline_ret.append(ret)
                triggered = False
                t = cand.get("type", "")
                if t == "contrarian_reversal":
                    triggered = row.get(cand["factor"]) == cand["value"]
                elif t == "high_contrarian_factor":
                    v = row.get(cand["factor"], 0.0)
                    try:
                        triggered = float(v) > medians_global.get(cand["factor"], 0)
                    except (TypeError, ValueError):
                        pass
                elif t == "combo_signal" and cand.get("name") == "StrongMF+PositiveChart":
                    triggered = row.get("money_flow") == "Strong" and row.get("chart_score", 0) > 0
                elif t == "br_override":
                    triggered = row.get("buying_ratio", 0) >= 6.0 and row.get("combined", 0) < SCORE_THRESHOLD_DEF
                elif t == "digit_sum_miss":
                    triggered = abs(row.get("digit_sum", 0.0)) >= 0.3
                if triggered:
                    hits_ret.append(ret)

        if not hits_ret or not baseline_ret:
            continue

        n          = len(hits_ret)
        avg_hit    = sum(hits_ret) / n
        avg_base   = sum(baseline_ret) / len(baseline_ret)
        lift       = avg_hit - avg_base
        stdev_base = _stdev(baseline_ret) or 1.0
        z          = lift / (stdev_base / math.sqrt(n)) if n > 0 else 0.0

        validated.append({
            **cand,
            "validation": {
                "n":          n,
                "avg_return": round(avg_hit, 2),
                "baseline":   round(avg_base, 2),
                "lift":       round(lift, 2),
                "z":          round(z, 2),
                "confident":  abs(z) >= 1.96 and n >= 20,
            }
        })
        print(f"    → n={n}, avg={avg_hit:+.2f}%, lift={lift:+.2f}%, z={z:+.2f}")

    return sorted(validated, key=lambda x: abs(x.get("validation", {}).get("z", 0)), reverse=True)

medians_global: dict = {}


# ── Phase 6: report ───────────────────────────────────────────────────────────

def generate_report(replay_date: str, s10_result: dict, l60_result: dict,
                    candidates_s10: list, candidates_l60: list,
                    fp_guards_s10: list, fp_guards_l60: list,
                    validated: list, universe_size: int) -> dict:
    report = {
        "replay_date":        replay_date,
        "universe_size":      universe_size,
        "s10_analysis":       s10_result,
        "l60_analysis":       l60_result,
        "candidates_s10":     candidates_s10,
        "candidates_l60":     candidates_l60,
        "fp_guards_s10":      fp_guards_s10,
        "fp_guards_l60":      fp_guards_l60,
        "validated_patterns": validated,
    }
    out_path = OUT_DIR / f"pattern_discovery_{replay_date}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to {out_path}")

    # Human summary
    print("\n" + "="*60)
    print(f"PATTERN DISCOVERY REPORT — {replay_date}")
    print("="*60)
    for horizon, result in [("S10 (10-day)", s10_result), ("L60 (60-day)", l60_result)]:
        winners = result.get("winners", [])
        missed  = result.get("missed", [])
        bought  = result.get("bought", [])
        u_avg   = result.get("universe_avg", 0)
        print(f"\n{horizon} — universe avg: {u_avg:+.2f}%")
        print(f"  Top-{len(winners)} winners: {len(bought)} bought, {len(missed)} missed")
        for w in winners[:5]:
            r_key = result.get("r_key", "r10")
            print(f"  {w['symbol']:8s} {w.get(f'fwd_{r_key}', 0):+6.1f}%  [{w['status']:20s}]  {', '.join(w['reasons'][:2])}")

    all_candidates = candidates_s10 + candidates_l60
    if all_candidates:
        print(f"\nMissed winner patterns: {len(all_candidates)}")
        for c in all_candidates[:5]:
            name = c.get("name", c.get("factor", "?"))
            print(f"  + {name}: {c.get('hypothesis','')[:80]}")

    all_guards = fp_guards_s10 + fp_guards_l60
    if all_guards:
        print(f"\nFalse positive guard signals: {len(all_guards)}")
        for g in all_guards[:5]:
            print(f"  - {g['factor']}={g['value']} (fp_rate={g['fp_rate']:.0%}): {g['hypothesis'][:70]}")

    if validated:
        print(f"\nValidated patterns (|z|>=1.96): {sum(1 for v in validated if v.get('validation',{}).get('confident'))}")

    return report


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AETHER Pattern Discovery — Historical Replay")
    parser.add_argument("--date",       required=True, help="Replay date YYYY-MM-DD")
    parser.add_argument("--top",        type=int, default=10, help="Top-N winners to analyze")
    parser.add_argument("--symbols",    help="Comma-separated symbol list (default: all cached)")
    parser.add_argument("--validate",   action="store_true", help="Validate candidates across date range")
    parser.add_argument("--date-range", default="2025-06-01:2026-02-28",
                        help="Validation range YYYY-MM-DD:YYYY-MM-DD")
    args = parser.parse_args()

    replay_date = args.date
    symbols = args.symbols.split(",") if args.symbols else None
    dr = tuple(args.date_range.split(":"))

    print(f"\n{'='*60}")
    print(f"AETHER Pattern Discovery — Replay Date: {replay_date}")
    print(f"{'='*60}\n")

    print("Phase 1: Reconstructing scores...")
    scores = reconstruct_scores(replay_date, symbols)
    if not scores:
        print("ERROR: No scores reconstructed. Check Chaikin cache for this date.")
        sys.exit(1)

    # Build global medians for validation
    global medians_global
    from collections import defaultdict as _dd
    fv = _dd(list)
    for row in scores:
        for k in ["buying_ratio", "fibonacci", "rsi_divergence",
                  "candlestick", "chart_score", "momentum_score", "digit_sum"]:
            v = row.get(k)
            if v is not None:
                fv[k].append(float(v))
    medians_global = {k: _median(v) for k, v in fv.items()}

    print("\nPhase 2: Computing forward returns...")
    all_syms = [r["symbol"] for r in scores]
    returns  = compute_forward_returns(replay_date, all_syms)

    print("\nPhase 3a: Missed winners (S10 / 10-day)...")
    s10_result = find_missed_winners(scores, returns, top_n=args.top, horizon="s10")

    print("\nPhase 3b: Missed winners (L60 / 60-day)...")
    l60_result = find_missed_winners(scores, returns, top_n=args.top, horizon="l60")

    print("\nPhase 4a: Extracting missed-winner patterns...")
    cands_s10 = extract_candidate_patterns(s10_result["missed"], scores, returns, "r10")
    cands_l60 = extract_candidate_patterns(l60_result["missed"], scores, returns, "r60")
    print(f"  Found {len(cands_s10)} S10 missed-winner patterns, {len(cands_l60)} L60")

    print("\nPhase 4b: Extracting false-positive guard signals...")
    fp_guards_s10 = extract_false_positive_patterns(s10_result["false_positives"], scores, returns, "r10")
    fp_guards_l60 = extract_false_positive_patterns(l60_result["false_positives"], scores, returns, "r60")
    print(f"  Found {len(fp_guards_s10)} S10 guard signals, {len(fp_guards_l60)} L60")

    validated = []
    if args.validate and (cands_s10 or cands_l60):
        print(f"\nPhase 5: Validating patterns across {dr[0]} → {dr[1]}...")
        validated = validate_patterns(cands_s10 + cands_l60, dr, all_syms, horizon="s10")

    print("\nPhase 6: Generating report...")
    generate_report(replay_date, s10_result, l60_result, cands_s10, cands_l60,
                    fp_guards_s10, fp_guards_l60, validated, len(scores))


if __name__ == "__main__":
    main()
