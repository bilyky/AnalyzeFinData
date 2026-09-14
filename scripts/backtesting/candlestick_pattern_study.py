"""
Per-pattern candlestick calibration study (R&D #25).

The candlestick factor (`patterns.candlestick_score`) sums per-pattern bull/bear
fires into a single [-2, +2] score, weighted by `patterns.CANDLESTICK_WEIGHTS`.
Those weights shipped as HAND-SET reliability priors. This study derives them
empirically: for every (symbol, date) in the OHLCV universe it records which of
the 17 patterns fired (via the single-source `patterns.candlestick_fires`) and the
10-/60-day forward return, then measures each pattern's edge.

For each pattern it compares the forward return of a BULL fire vs NO fire (and BEAR
fire vs NO fire) with a Welch t-test, and it resolves the AGGREGATE SIGN — does a
net-bullish candle tally predict HIGHER (momentum) or LOWER (contrarian) forward
returns? That sign decides whether scoring.short_score/long_score should consume
the factor with a +tive or -tive coefficient (this is the exact question the
`df9b2c2` sign-flip raised — settle it with evidence, not intuition).

Weight-derivation rule (documented, reproducible):
  * aggregate direction `sign_agg` = sign of the unit-weighted (bull_count -
    bear_count) 10d spread over the whole universe.
  * a pattern's `aligned_effect` = sign_agg * (bull_spread) — its 10d edge measured
    in the aggregate's favoured direction.
  * significant (|t| >= T_GATE and both arms >= MIN_N) AND aligned_effect > 0
        -> weight = clamp(aligned_effect / ref, WEIGHT_FLOOR, WEIGHT_CAP)
           where ref = median aligned_effect of the significant-correct patterns
           (so the typical confirmed pattern lands near 1.0).
  * significant but wrong-direction  -> WEIGHT_DEMOTE (evidence says it hurts).
  * not significant                  -> 1.0 (UNIT DEFAULT, per R&D #25).

The `/5.0` saturation divisor in candlestick_score is also re-derived here as the
95th percentile of |raw tally| under the new weights, so ~5% of bars saturate the
rail instead of an arbitrary constant.

Output: Data/candlestick_pattern_study.json (weights + full per-pattern stats +
aggregate sign verdict + saturation divisor). candlestick_score loads the weights
from this file, falling back to unit weight when absent.

REVALIDATION: re-run MONTHLY (like digit_sum_study / pullback_recovery_study). The
per-pattern edge is estimated over 25+ years of bars and does not move week-to-week;
weekly re-derivation only burns compute and overfits. Wire a monthly refresh, not a
per-run one.

Usage:
    python scripts/backtesting/candlestick_pattern_study.py [min_year] [max_symbols]
    python scripts/backtesting/candlestick_pattern_study.py            # 2023+, all symbols
    python scripts/backtesting/candlestick_pattern_study.py 2000 40    # pilot: 40 symbols
"""
import glob
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import instruments
from patterns import CANDLESTICK_WEIGHTS, candlestick_fires

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Data/ is gitignored (local cache) and absent from a fresh worktree checkout, so allow
# AETHER_DATA_DIR to point the study at the primary repo's cache. Defaults to repo-relative.
_DATA_DIR = os.environ.get("AETHER_DATA_DIR") or os.path.join(_ROOT, "Data")
OHLCV_DIR = os.path.join(_DATA_DIR, "Symbol_full")
OUT_PATH = os.path.join(_DATA_DIR, "candlestick_pattern_study.json")

FWD_WINDOWS = [10, 60]      # S10 primary (gate), L60 secondary
MIN_HISTORY = 30            # bars needed before a date (candlestick_fires lookback=30)
T_GATE = 1.96              # |Welch t| significance gate
MIN_N = 100                # minimum obs per arm for a pattern to be calibratable
WEIGHT_FLOOR = 0.50        # min weight for a significant, correct-direction pattern
WEIGHT_CAP = 2.00          # max weight (matches candlestick_score [-2,2] intent)
WEIGHT_DEMOTE = 0.50       # weight for a significant but wrong-direction pattern
UNIT_DEFAULT = 1.00        # non-significant patterns (R&D #25 default)

PATTERN_NAMES = list(CANDLESTICK_WEIGHTS.keys())

_log = logging.getLogger("candlestick_study")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False


class Acc:
    """Running (n, sum, sumsq) accumulator per forward window — avoids holding
    every return in memory across ~300k observations."""
    __slots__ = ("n", "s", "ss")

    def __init__(self):
        self.n = {w: 0 for w in FWD_WINDOWS}
        self.s = {w: 0.0 for w in FWD_WINDOWS}
        self.ss = {w: 0.0 for w in FWD_WINDOWS}

    def add(self, w, x):
        self.n[w] += 1
        self.s[w] += x
        self.ss[w] += x * x

    def mean(self, w):
        return self.s[w] / self.n[w] if self.n[w] else None

    def var(self, w):
        n = self.n[w]
        if n < 2:
            return None
        m = self.s[w] / n
        return max(0.0, (self.ss[w] - n * m * m) / (n - 1))


def welch(a: Acc, b: Acc, w):
    """Welch t for mean(a)-mean(b) at window w. Returns (diff, t, na, nb) or None."""
    na, nb = a.n[w], b.n[w]
    if na < 2 or nb < 2:
        return None
    ma, mb = a.mean(w), b.mean(w)
    va, vb = a.var(w), b.var(w)
    se = (va / na + vb / nb) ** 0.5
    if se <= 0:
        return None
    return (ma - mb, (ma - mb) / se, na, nb)


def load_ohlcv(symbol):
    path = os.path.join(OHLCV_DIR, f"{symbol}_daily.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("Time Series (Daily)") or None


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def run(min_year=2023, max_symbols=None):
    ohlcv_files = sorted(
        os.path.basename(f).replace("_daily.json", "")
        for f in glob.glob(os.path.join(OHLCV_DIR, "*_daily.json"))
    )
    symbols = [s for s in ohlcv_files if not instruments.is_excluded(s)]
    if max_symbols:
        symbols = symbols[:max_symbols]
    _log.info(f"\nCandlestick per-pattern study — {len(symbols)} symbols, >= {min_year}\n")

    # Per pattern: three arms of forward-return accumulators.
    bull = {p: Acc() for p in PATTERN_NAMES}
    bear = {p: Acc() for p in PATTERN_NAMES}
    none = {p: Acc() for p in PATTERN_NAMES}

    # Aggregate (unit-weighted) net tally sign arms.
    agg_netbull = Acc()   # unit bull_count > bear_count
    agg_netbear = Acc()   # unit bear_count > bull_count
    agg_flat = Acc()      # tie / no fire

    raw_abs = []          # |unit-weighted raw| for the saturation divisor (sampled)
    total_obs = 0
    max_date = ""

    for i, sym in enumerate(symbols, 1):
        ts = load_ohlcv(sym)
        if not ts:
            continue
        dates = sorted(ts.keys())
        closes = []
        for d in dates:
            try:
                closes.append(float(ts[d].get("4. close", 0)))
            except (TypeError, ValueError):
                closes.append(0.0)
        n = len(dates)

        for idx in range(MIN_HISTORY, n):
            date_str = dates[idx]
            if date_str < str(min_year):
                continue
            entry = closes[idx]
            if entry <= 0:
                continue
            # forward returns; skip obs lacking the longest window
            fwd = {}
            ok = True
            for w in FWD_WINDOWS:
                j = idx + w
                if j >= n or closes[j] <= 0:
                    ok = False
                    break
                fwd[w] = (closes[j] - entry) / entry * 100.0
            if not ok:
                continue

            fires = candlestick_fires(ts, date_str)
            if not fires:
                continue

            total_obs += 1
            if date_str > max_date:
                max_date = date_str

            bull_ct = 0
            bear_ct = 0
            for p in PATTERN_NAMES:
                b, s = fires.get(p, (False, False))
                if b:
                    bull_ct += 1
                    for w in FWD_WINDOWS:
                        bull[p].add(w, fwd[w])
                if s:
                    bear_ct += 1
                    for w in FWD_WINDOWS:
                        bear[p].add(w, fwd[w])
                if not b and not s:
                    for w in FWD_WINDOWS:
                        none[p].add(w, fwd[w])

            net = bull_ct - bear_ct
            if net > 0:
                for w in FWD_WINDOWS:
                    agg_netbull.add(w, fwd[w])
            elif net < 0:
                for w in FWD_WINDOWS:
                    agg_netbear.add(w, fwd[w])
            else:
                for w in FWD_WINDOWS:
                    agg_flat.add(w, fwd[w])
            # sample |raw| under CURRENT weights for divisor context (every 5th obs)
            if total_obs % 5 == 0:
                rb = sum(CANDLESTICK_WEIGHTS.get(p, 1.0)
                         for p in PATTERN_NAMES if fires.get(p, (0, 0))[0])
                rs = sum(CANDLESTICK_WEIGHTS.get(p, 1.0)
                         for p in PATTERN_NAMES if fires.get(p, (0, 0))[1])
                raw_abs.append(abs(rb - rs))

        if i % 50 == 0:
            _log.info(f"  ... {i}/{len(symbols)} symbols  ({total_obs:,} obs)")

    _log.info(f"\n  Total observations: {total_obs:,}\n")

    # ── Aggregate sign verdict ──────────────────────────────────────────────
    agg = {}
    _log.info("--- AGGREGATE SIGN (unit-weighted net bull vs net bear) ---")
    _log.info(f"  {'window':>6}  {'net-bull avg':>13}  {'net-bear avg':>13}  {'spread':>8}  {'t':>7}  {'N+':>7}  {'N-':>7}")
    sign_agg = 1
    for w in FWD_WINDOWS:
        res = welch(agg_netbull, agg_netbear, w)
        mb = agg_netbull.mean(w)
        ms = agg_netbear.mean(w)
        if res:
            diff, t, na, nb = res
            _log.info(f"  {w:>5}d  {mb:>12.3f}%  {ms:>12.3f}%  {diff:>7.3f}%  {t:>7.2f}  {na:>7}  {nb:>7}")
            agg[w] = {"net_bull_avg": mb, "net_bear_avg": ms, "spread": diff, "t": t, "n_pos": na, "n_neg": nb}
            if w == 10:
                sign_agg = 1 if diff > 0 else -1
        else:
            agg[w] = None
    direction = "MOMENTUM (+)" if sign_agg > 0 else "CONTRARIAN (-)"
    _log.info(f"\n  10d aggregate direction: {direction}")
    _log.info(f"  -> scoring.short_score/long_score should consume candlestick_score with a "
              f"{'+' if sign_agg > 0 else '-'}tive coefficient.\n")

    # ── Per-pattern stats ───────────────────────────────────────────────────
    stats = {}
    _log.info("--- PER-PATTERN (bull fire vs no fire, 10d) ---")
    _log.info(f"  {'pattern':<15}  {'N_bull':>7}  {'N_none':>7}  {'bull10':>8}  {'none10':>8}  {'spread':>8}  {'t':>7}  {'sig':>4}")
    for p in PATTERN_NAMES:
        row = {"n_bull": bull[p].n[10], "n_bear": bear[p].n[10], "n_none": none[p].n[10]}
        for w in FWD_WINDOWS:
            res = welch(bull[p], none[p], w)
            if res:
                diff, t, na, nb = res
                row[f"bull_spread_{w}"] = diff
                row[f"bull_t_{w}"] = t
            else:
                row[f"bull_spread_{w}"] = None
                row[f"bull_t_{w}"] = None
            bres = welch(bear[p], none[p], w)
            row[f"bear_spread_{w}"] = bres[0] if bres else None
            row[f"bear_t_{w}"] = bres[1] if bres else None
        row["bull_avg_10"] = bull[p].mean(10)
        row["none_avg_10"] = none[p].mean(10)
        sp = row.get("bull_spread_10")
        t10 = row.get("bull_t_10")
        significant = (t10 is not None and abs(t10) >= T_GATE
                       and bull[p].n[10] >= MIN_N and none[p].n[10] >= MIN_N)
        row["significant"] = significant
        row["aligned_effect"] = (sign_agg * sp) if sp is not None else None
        stats[p] = row
        sig = "yes" if significant else "-"
        sps = f"{sp:>7.3f}%" if sp is not None else "     N/A"
        ts_ = f"{t10:>7.2f}" if t10 is not None else "    N/A"
        b10 = f"{row['bull_avg_10']:>7.3f}%" if row["bull_avg_10"] is not None else "     N/A"
        n10 = f"{row['none_avg_10']:>7.3f}%" if row["none_avg_10"] is not None else "     N/A"
        _log.info(f"  {p:<15}  {row['n_bull']:>7}  {row['n_none']:>7}  {b10}  {n10}  {sps}  {ts_}  {sig:>4}")

    # ── Derive weights ──────────────────────────────────────────────────────
    sig_correct = [stats[p]["aligned_effect"] for p in PATTERN_NAMES
                   if stats[p]["significant"] and (stats[p]["aligned_effect"] or 0) > 0]
    sig_correct.sort()
    ref = _percentile(sig_correct, 0.5) if sig_correct else 1.0
    if not ref or ref <= 0:
        ref = 1.0

    weights = {}
    for p in PATTERN_NAMES:
        r = stats[p]
        ae = r["aligned_effect"]
        if not r["significant"] or ae is None:
            weights[p] = UNIT_DEFAULT
        elif ae > 0:
            weights[p] = round(max(WEIGHT_FLOOR, min(WEIGHT_CAP, ae / ref)), 2)
        else:
            weights[p] = WEIGHT_DEMOTE
        r["weight"] = weights[p]

    raw_abs.sort()
    divisor = _percentile(raw_abs, 0.95) if raw_abs else 5.0
    if not divisor or divisor <= 0:
        divisor = 5.0
    divisor = round(divisor, 2)

    _log.info("\n--- DERIVED WEIGHTS ---")
    _log.info(f"  {'pattern':<15}  {'old':>5}  {'new':>5}  {'basis':<28}")
    for p in PATTERN_NAMES:
        r = stats[p]
        if not r["significant"]:
            basis = "unit default (not significant)"
        elif (r["aligned_effect"] or 0) > 0:
            basis = f"aligned_effect {r['aligned_effect']:+.3f}%"
        else:
            basis = f"DEMOTE (wrong dir {r['aligned_effect']:+.3f}%)"
        _log.info(f"  {p:<15}  {CANDLESTICK_WEIGHTS[p]:>5.2f}  {weights[p]:>5.2f}  {basis:<28}")
    _log.info(f"\n  saturation divisor (95th pctile |raw|): {divisor}  (was hand-set 5.0)")

    payload = {
        "as_of": max_date,
        "min_year": min_year,
        "n_obs": total_obs,
        "n_symbols": len(symbols),
        "fwd_windows": FWD_WINDOWS,
        "gate": {"t_gate": T_GATE, "min_n": MIN_N},
        "aggregate_sign": sign_agg,
        "aggregate": agg,
        "saturation_divisor": divisor,
        "weights": weights,
        "patterns": stats,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    _log.info(f"\n  Wrote {OUT_PATH}\n")
    return payload


if __name__ == "__main__":
    _min_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    _max_symbols = int(sys.argv[2]) if len(sys.argv) > 2 else None
    run(_min_year, _max_symbols)
