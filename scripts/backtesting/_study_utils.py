"""Shared utilities for digit-sum study scripts."""
import math

MIN_WIN_N = 30

WINDOWS = [
    ("14-16", "2014-01-01", "2015-12-31"),
    ("16-18", "2016-01-01", "2017-12-31"),
    ("18-20", "2018-01-01", "2019-12-31"),
    ("20-22", "2020-01-01", "2021-12-31"),
    ("22-24", "2022-01-01", "2023-12-31"),
    ("24-26", "2024-01-01", "2026-12-31"),
]


def z_score(ups: int, n: int, base: float) -> float | None:
    if n < MIN_WIN_N:
        return None
    se = math.sqrt(base * (1 - base) / n)
    return ((ups / n) - base) / se if se > 0 else 0.0


def window_analysis(dg: int, overall_z: float, ts: dict,
                    digit_fn, signal_type: str = "OPEN") -> dict:
    """Classify a digit-sum signal across rolling 2-year windows.

    signal_type: "OPEN"  — digit_fn applied to open price, same-day direction
                 "CLOSE" — digit_fn applied to close price, next-day direction

    Returns a dict with keys: temporal, coverage, has_flip, is_sparse.
    """
    dates = sorted(ts.keys())

    # Base rate: fraction of days where close > open (same-day up-day fraction).
    # For CLOSE signals, the theoretically correct base would use next-day direction,
    # but same-day approximation introduces negligible bias in z-score comparisons
    # since both numerator and denominator shift by the same amount.
    all_up = all_n = 0
    for date in dates:
        d = ts[date]
        try:
            op = float(d["1. open"])
            cl = float(d["4. close"])
        except (KeyError, ValueError):
            continue
        all_n += 1
        all_up += 1 if cl > op else 0
    base = all_up / all_n if all_n else 0.5
    sign = 1 if overall_z > 0 else -1

    wzs = []
    for _, start, end in WINDOWS:
        ups = n = 0
        for i, date in enumerate(dates):
            if date < start or date > end:
                continue
            d = ts[date]
            try:
                op = float(d["1. open"])
                cl = float(d["4. close"])
            except (KeyError, ValueError):
                continue

            if signal_type == "OPEN":
                if digit_fn(op) == dg:
                    n += 1
                    ups += 1 if cl > op else 0
            else:  # CLOSE -> next-day direction
                if digit_fn(cl) == dg and i + 1 < len(dates):
                    dn = ts[dates[i + 1]]
                    try:
                        on = float(dn["1. open"])
                        cn = float(dn["4. close"])
                        n += 1
                        ups += 1 if cn > on else 0
                    except (KeyError, ValueError):
                        pass

        wzs.append(z_score(ups, n, base))

    valid = [z for z in wzs if z is not None]
    coverage = len(valid) / len(WINDOWS)
    cons = sum(1 for z in valid if abs(z) >= 1.0 and (z > 0) == (sign > 0))
    sign_vals = [1 if z > 0 else -1 for z in valid if abs(z) >= 0.5]
    has_flip = len(set(sign_vals)) > 1 if len(sign_vals) >= 2 else False

    if cons >= 4:
        temporal = "consistent"
    elif cons >= 2:
        temporal = "partial"
    else:
        temporal = "stale"

    return {
        "temporal":  temporal,
        "coverage":  round(coverage, 2),
        "has_flip":  has_flip,
        "is_sparse": coverage < 0.5,
    }
