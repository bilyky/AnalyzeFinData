"""
R&D #13 PGR-waiver cohort study — validates the Option-1 gate change.

check_failure_rules() waives the "toxic Bearish PGR" rule for high-conviction names.
  OLD gate:  score >= 10.0            where score = total = S10 + L60
  NEW gate:  is_elite_breakout_candidate(score, s10)
             = (total >= 8.0) AND (s10 >= 2.0)   [s10 = S10, CFG floors 8.0/2.0]

This study reconstructs (pgr, S10, L60, fwd_10) per (symbol, date) from the Chaikin
cache using the SAME blessed scoring path as backtest_ratings.compute_br, restricts to
Bearish-PGR observations (pgr rating in {1,2} == "startswith Be"), and compares the
forward-return of the cohorts the change moves:

  retained  = OLD-waived AND NEW-waived   (total>=10 AND s10>=2)   -> bought both ways
  newly_blk = OLD-waived AND NOT NEW-waived(total>=10 AND s10<2)   -> Option 1 now BLOCKS
  newly_ok  = NEW-waived AND NOT OLD-waived(8<=total<10 AND s10>=2)-> Option 1 now ALLOWS

Validation: the change is supported if newly_blk underperforms retained (blocking weak-
momentum names is right) and newly_ok is not materially worse (loosening the score floor
to 8 for real-momentum names is safe).

Run:  PYTHONIOENCODING=utf-8 python scripts/backtesting/pgr_waiver_cohort_study.py
"""
import os
import sys
import glob
import json
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import scripts.backtesting.backtest_ratings as R

FWD_10_IDX = R.FWD_WINDOWS.index(10)


def obs_for_symbol(symbol, min_year, ohlcv_ts, all_dates):
    """Yield (pgr_corr, s10, l60, fwd_10) per date — mirrors process_symbol but keeps pgr."""
    seasonality_map = R.precompute_seasonality(ohlcv_ts)
    ohlcv_date_set = set(all_dates)

    pattern = os.path.join(R.SYM_DIR, symbol, f"{symbol}_*.json")
    date_data = {}
    for path in sorted(glob.glob(pattern)):
        date_str = os.path.basename(path).replace('.json', '')[len(symbol) + 1:]
        if date_str < str(min_year) or date_str not in ohlcv_date_set:
            continue
        date_data[date_str] = path
    if not date_data:
        return

    prev_data = None
    for date_str in sorted(date_data.keys()):
        try:
            with open(date_data[date_str]) as f:
                data = json.load(f)
        except Exception:
            prev_data = None
            continue
        if data.get('status') == 'invalid symbol':
            prev_data = data
            continue

        meta = (data.get('metaInfo') or [{}])[0]
        cl = data.get('checklist_stocks') or {}
        try:
            price = float(meta.get('Last') or cl.get('lastPrice') or 0)
        except (TypeError, ValueError):
            price = 0
        if price <= 0:
            prev_data = data
            continue

        if date_str in ohlcv_ts:
            ohlcv_close = float(ohlcv_ts[date_str].get('4. close', 0))
            if ohlcv_close > 0 and abs(price - ohlcv_close) / ohlcv_close > 0.1:
                prev_data = data
                continue

        try:
            idx = all_dates.index(date_str)
        except ValueError:
            prev_data = data
            continue
        if idx < max(R.SMA_DAYS, R.TARGET_LOOKBACK, 3):
            prev_data = data
            continue

        (br, short, long, s_nf, l_nf, rsi_div, s_nd, l_nd, cs, cps, ms,
         gann_val) = R.compute_br(data, prev_data, price, idx, all_dates, ohlcv_ts, seasonality_map)
        pgr_corr = R.extract_pgr_corr(data)

        future_idx = idx + 10
        fwd_10 = None
        if future_idx < len(all_dates):
            fwd_close = float(ohlcv_ts[all_dates[future_idx]].get('4. close', 0))
            if fwd_close > 0:
                fwd_10 = (fwd_close - price) / price * 100

        prev_data = data
        if fwd_10 is None:
            continue
        yield (pgr_corr, short, long, fwd_10)


def stats(xs):
    n = len(xs)
    if n == 0:
        return (0, 0.0, 0.0, 0.0)
    mean = sum(xs) / n
    win = sum(1 for x in xs if x > 0) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return (n, mean, win, var)


def welch_t(a, b):
    na, ma, _, va = stats(a)
    nb, mb, _, vb = stats(b)
    if na < 2 or nb < 2:
        return 0.0
    se = math.sqrt(va / na + vb / nb)
    return (ma - mb) / se if se > 0 else 0.0


def main():
    min_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    ohlcv_files = {os.path.basename(f).replace('_daily.json', '')
                   for f in glob.glob(os.path.join(R.OHLCV_DIR, '*_daily.json'))}
    cache_syms = {d for d in os.listdir(R.SYM_DIR)
                  if os.path.isdir(os.path.join(R.SYM_DIR, d))}
    symbols = sorted(ohlcv_files & cache_syms)
    R._log.info(f"Symbols with both Chaikin + OHLCV: {len(symbols)} (min_year={min_year})")

    retained, newly_blk, newly_ok = [], [], []
    bearish_total = 0

    for i, sym in enumerate(symbols, 1):
        ohlcv_ts = R.load_ohlcv(sym)
        if not ohlcv_ts:
            continue
        all_dates = sorted(ohlcv_ts.keys())
        for pgr, s10, l60, fwd10 in obs_for_symbol(sym, str(min_year), ohlcv_ts, all_dates):
            if pgr not in (1, 2):        # Bearish PGR == startswith "Be"
                continue
            bearish_total += 1
            total = s10 + l60
            old_w = total >= 10.0
            new_w = (total >= 8.0) and (s10 >= 2.0)
            if old_w and new_w:
                retained.append(fwd10)
            elif old_w and not new_w:
                newly_blk.append(fwd10)
            elif new_w and not old_w:
                newly_ok.append(fwd10)
        if i % 50 == 0:
            R._log.info(f"  ...{i}/{len(symbols)} symbols")

    R._log.info(f"\nBearish-PGR observations: {bearish_total}")
    R._log.info(f"{'cohort':<28}{'n':>8}{'mean10d%':>12}{'win%':>10}")
    for name, xs in (("retained (buy both ways)", retained),
                     ("newly BLOCKED (Opt1)", newly_blk),
                     ("newly ALLOWED (Opt1)", newly_ok)):
        n, mean, win, _ = stats(xs)
        R._log.info(f"{name:<28}{n:>8}{mean:>12.3f}{win*100:>10.1f}")

    t_blk_ret = welch_t(newly_blk, retained)
    R._log.info(f"\nWelch t (newly_blocked - retained): {t_blk_ret:+.2f}")
    R._log.info("  Negative t => the names Option 1 now blocks underperformed the ones it keeps buying"
          " (change SUPPORTED).")
    t_ok_ret = welch_t(newly_ok, retained)
    R._log.info(f"Welch t (newly_allowed - retained): {t_ok_ret:+.2f}")
    R._log.info("  >= ~0 => loosening the score floor to 8 for real-momentum names is safe.")

    # Persist so the verdict survives stdout buffering under background capture.
    def _c(xs):
        n, mean, win, _ = stats(xs)
        return {"n": n, "mean10d_pct": round(mean, 4), "win_pct": round(win * 100, 2)}
    out = {
        "min_year": min_year,
        "bearish_obs": bearish_total,
        "retained": _c(retained),
        "newly_blocked": _c(newly_blk),
        "newly_allowed": _c(newly_ok),
        "welch_t_blocked_minus_retained": round(t_blk_ret, 3),
        "welch_t_allowed_minus_retained": round(t_ok_ret, 3),
    }
    out_path = os.path.join(os.path.dirname(R.SYM_DIR), "pgr_waiver_cohort_study.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    R._log.info(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
