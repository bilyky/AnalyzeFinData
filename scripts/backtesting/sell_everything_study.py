"""R&D #123: the sell-everything detector. When stocks, gold and bitcoin fall together, does SPY keep falling
or bounce?

Raised 2026-11-20 (owner). SPY closed 11-20 about 10.9% under its 10-06 peak after seven down sessions. The VIX
was 34, and gold, gold miners and bitcoin fell with stocks. Our macro battery (`macro_signal_scan.py`, R&D
carry-unwind family) only fires when a safe haven RISES while SPY falls, so it stays silent on a day like this. #90
looks at single-stock washouts on #89's stretch events. This study asks a market-level question about SPY.

RULES (fixed before the first run; any later change is disclosed with the result)
Data, our own first (owner rule 2026-11-20: fall back to Yahoo or Google only for data we are missing and cannot
repopulate):
- SPY and GLD from our OHLCV cache (Symbol_full/<SYM>_daily.json under aether.paths.cache_dir(), field "4. close").
  GLD was not cached; it was filled on 2026-11-20 with our own fetcher (rapidapi._fetch_and_merge, Alpha Vantage
  TIME_SERIES_DAILY, full history), before this study ran.
- Days after our cache ends (the bar of the run day itself) come from Yahoo. On the last 250 shared days our
  closes and Yahoo's must agree within 0.5% on every day, or the run aborts.
- ^VIX and BTC-USD from Yahoo: our fetcher's TIME_SERIES_DAILY serves neither the VIX index nor crypto pairs.
Yahoo series are cached in Data/sell_everything_cache.json. The result records the run date, the data range and
the number of Yahoo-filled days per symbol. Days are SPY trading days.

Flag on day t, closes up to t only:
  1. SPY 5-session return <= -3%;
  2. SPY closed down on at least 4 of the last 5 sessions;
  3. VIX_t / VIX_(t-5) >= 1.25;
  4. GLD 5-session return < 0 (gold falls too, so no haven is working);
  5. (bitcoin version only) BTC-USD 5-session return < 0.
Comparison group ("stress, haven working"): days with 1-3 true and 4 false (gold up). In the bitcoin version, the
comparison group is days with 1-3 true and gold or bitcoin up.
Events: the first qualifying day after at least 20 sessions with no qualifying day of the same group, so the
20-session outcome windows inside a group don't overlap.

Outcome, from the close of day t: SPY 20-session return (primary). 5- and 10-session returns and the worst close
in the next 20 sessions (further drawdown) are reported as context, as is the mean 20-session return over all
days.

Samples: PRIMARY = the gold version, GLD history from 2004-11-18. SECONDARY = the bitcoin version, from
2014-09-17; it must agree in sign with the primary for a verdict to count as confirmed.

Statistic and verdict (primary sample): diff = mean(flag ex20) - mean(comparison ex20), Welch t.
  INSUFFICIENT             fewer than 8 events in either group;
  BOUNCE_AFTER_FLAG        t >= 2 and the flag group's mean 20-session return is above 0;
  FALLS_FURTHER_AFTER_FLAG t <= -2;
  INCONCLUSIVE             otherwise.
What a verdict would do: BOUNCE -> propose (own PR, own gates) a "buy in steps after the flag" note for the
OceanView plan and a G-A card candidate; FALLS_FURTHER -> propose a de-risk overlay candidate; INCONCLUSIVE or
INSUFFICIENT -> nothing is wired.

Caveats written in advance:
- Few independent events: a market-wide flag fires a handful of times a decade. Expect INSUFFICIENT or INCONCLUSIVE.
- Prior: 0 of 14 earlier pre-registered studies beat SPY. Risk and timing filters did better than alpha ideas.
- The named episodes (spring 2025, Nov 2025, spring 2026, Nov 2026) are reported for reading only; three of them
  ended in a rising market, which is survivorship in time, not evidence.
- GLD stands for gold; GDX (miners) is not used because it is a leveraged, noisy proxy.
"""
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import requests  # noqa: E402

from aether import paths  # noqa: E402

SYMBOLS = ("SPY", "^VIX", "GLD", "BTC-USD")
SPY_5D_MAX = -0.03
DOWN_DAYS_MIN = 4
VIX_RATIO_MIN = 1.25
LOOKBACK = 5
GAP = 20
HORIZON = 20
MIN_EVENTS = 8
T_BAR = 2.0
GOLD_START = dt.date(2004, 11, 18)
BTC_START = dt.date(2014, 9, 17)
EPISODES = {"spring 2025": (dt.date(2025, 3, 1), dt.date(2025, 4, 30)),
            "Nov 2025": (dt.date(2025, 11, 1), dt.date(2025, 11, 30)),
            "spring 2026": (dt.date(2026, 3, 1), dt.date(2026, 4, 15)),
            "Nov 2026": (dt.date(2026, 11, 1), dt.date(2026, 11, 30))}


# ── pure parts (unit-tested) ─────────────────────────────────────────────────────────────────────
def ret(xs, i, n):
    """Return from close i-n to close i (n > 0) or from i to i+n (n < 0 means forward |n|)."""
    if n > 0:
        return xs[i] / xs[i - n] - 1 if i - n >= 0 else None
    j = i - n
    return xs[j] / xs[i] - 1 if j < len(xs) else None


def classify(spy, vix, gold, btc, i):
    """'flag', 'stress' (haven working) or None for day i. btc=None runs the gold version."""
    if i < LOOKBACK:
        return None
    s5 = ret(spy, i, LOOKBACK)
    downs = sum(1 for k in range(i - LOOKBACK + 1, i + 1) if spy[k] < spy[k - 1])
    vr = vix[i] / vix[i - LOOKBACK] if vix[i - LOOKBACK] else 0
    if not (s5 is not None and s5 <= SPY_5D_MAX and downs >= DOWN_DAYS_MIN and vr >= VIX_RATIO_MIN):
        return None
    havens_down = ret(gold, i, LOOKBACK) < 0 and (btc is None or ret(btc, i, LOOKBACK) < 0)
    return "flag" if havens_down else "stress"


def decluster(days, gap=GAP):
    """First day of each run: keep a day only if the previous kept day is at least `gap` sessions earlier."""
    out = []
    for d in sorted(days):
        if not out or d - out[-1] >= gap:
            out.append(d)
    return out


def welch_t(a, b):
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / se if se > 0 else None


def verdict(flag_r, stress_r):
    """The pre-registered decision on the primary sample's 20-session returns."""
    if len(flag_r) < MIN_EVENTS or len(stress_r) < MIN_EVENTS:
        return "INSUFFICIENT", None
    t = welch_t(flag_r, stress_r)
    if t is None:
        return "INCONCLUSIVE", None
    if t >= T_BAR and sum(flag_r) / len(flag_r) > 0:
        return "BOUNCE_AFTER_FLAG", t
    if t <= -T_BAR:
        return "FALLS_FURTHER_AFTER_FLAG", t
    return "INCONCLUSIVE", t


def outcomes(spy, i):
    fwd = [spy[k] for k in range(i + 1, min(i + 1 + HORIZON, len(spy)))]
    return {"r5": ret(spy, i, -5), "r10": ret(spy, i, -10), "r20": ret(spy, i, -HORIZON),
            "worst20": (min(fwd) / spy[i] - 1) if fwd else None}


def run_sample(dates, spy, vix, gold, btc):
    groups = {"flag": [], "stress": []}
    for i in range(len(dates)):
        g = classify(spy, vix, gold, btc, i)
        if g:
            groups[g].append(i)
    res = {}
    for g, days in groups.items():
        ev = decluster(days)
        rows = [{"date": str(dates[i]), **outcomes(spy, i)} for i in ev]
        res[g] = {"qualifying_days": len(days), "events": rows}
    fr = [r["r20"] for r in res["flag"]["events"] if r["r20"] is not None]
    sr = [r["r20"] for r in res["stress"]["events"] if r["r20"] is not None]
    allr = [ret(spy, i, -HORIZON) for i in range(len(spy) - HORIZON)]
    v, t = verdict(fr, sr)
    res.update({"verdict": v, "welch_t": t, "n_flag": len(fr), "n_stress": len(sr),
                "mean_r20_flag": sum(fr) / len(fr) if fr else None,
                "mean_r20_stress": sum(sr) / len(sr) if sr else None,
                "mean_r20_all_days": sum(allr) / len(allr) if allr else None,
                "flag_today": classify(spy, vix, gold, btc, len(dates) - 1)})
    return res


# ── data (Yahoo, cached) ─────────────────────────────────────────────────────────────────────────
def _cache_path():
    return os.path.join(paths.data_dir(), "sell_everything_cache.json")


def load(refresh=False):
    path = _cache_path()
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    out = {}
    for s in SYMBOLS:
        # range=max silently returns MONTHLY bars; an explicit period keeps them daily (found on the first run).
        p2 = int(dt.datetime.now(dt.timezone.utc).timestamp())
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=946684800&period2={p2}&interval=1d",
                         headers={"User-Agent": "Mozilla/5.0"}, proxies={"https": px} if px else None, timeout=30)
        j = r.json()["chart"]["result"][0]
        gran = j["meta"].get("dataGranularity")
        if gran != "1d":
            raise ValueError(f"Yahoo returned {gran} bars for {s}, not daily")
        closes = j["indicators"]["quote"][0]["close"]
        out[s] = {str(dt.datetime.fromtimestamp(t, dt.timezone.utc).date()): c for t, c in zip(j["timestamp"], closes) if c}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def our_closes(symbol):
    """{date: close} from our OHLCV cache (same file and field as carry_unwind_study._closes_by_date, but through
    aether.paths.cache_dir(), so a worktree run with AETHER_CACHE_DIR reads the real cache)."""
    try:
        with open(os.path.join(paths.cache_dir(), "Symbol_full", f"{symbol}_daily.json"), encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)", {})
    except (OSError, ValueError):
        return None
    out = {}
    for d, b in ts.items():
        try:
            out[d] = float(b["4. close"])
        except (KeyError, ValueError, TypeError):
            continue
    return out or None


RECON_DAYS = 250
RECON_TOL = 0.005


def merge_ours(ours, yahoo, recon_days=RECON_DAYS, tol=RECON_TOL):
    """Our closes, extended with Yahoo's for the dates after our cache ends. Raises if the two disagree."""
    shared = sorted(set(ours) & set(yahoo))[-recon_days:]
    if not shared:
        raise ValueError("no shared dates between our cache and Yahoo")
    worst = max(abs(ours[d] / yahoo[d] - 1) for d in shared)
    if worst > tol:
        raise ValueError(f"our closes and Yahoo's disagree by {worst:.2%} (> {tol:.1%}) on the last {len(shared)} days")
    last = max(ours)
    tail = {d: c for d, c in yahoo.items() if d > last}
    return {**ours, **tail}, len(tail), worst


def align(data, start, use_btc):
    need = ["SPY", "^VIX", "GLD"] + (["BTC-USD"] if use_btc else [])
    dates = sorted(d for d in data["SPY"] if dt.date.fromisoformat(d) >= start and all(d in data[s] for s in need))
    col = {s: [data[s][d] for d in dates] for s in need}
    return [dt.date.fromisoformat(d) for d in dates], col


def main():
    data = load(refresh="--refresh" in sys.argv)
    sources = {}
    for sym in ("SPY", "GLD"):
        ours = our_closes(sym)
        if not ours:
            sys.exit(f"ABORT: no {sym} in our OHLCV cache")
        data[sym], filled, worst = merge_ours(ours, data[sym])
        sources[sym] = {"ours": [min(ours), max(ours)], "yahoo_filled_days": filled, "recon_worst_gap": worst}
    sources["^VIX"] = sources["BTC-USD"] = "Yahoo (not served by our fetcher)"
    report = {"run_date": str(dt.date.today()), "rules": "see module docstring", "samples": {}, "sources": sources}
    for name, start, use_btc in (("gold_2004", GOLD_START, False), ("bitcoin_2014", BTC_START, True)):
        dates, c = align(data, start, use_btc)
        res = run_sample(dates, c["SPY"], c["^VIX"], c["GLD"], c.get("BTC-USD"))
        res["range"] = [str(dates[0]), str(dates[-1])]
        res["episodes"] = {ep: [e for g in ("flag", "stress") for e in res[g]["events"]
                                if a <= dt.date.fromisoformat(e["date"]) <= b]
                           for ep, (a, b) in EPISODES.items()}
        report["samples"][name] = res
        sys.stdout.write(f"\n== {name} {res['range']} ==\n verdict {res['verdict']} t={res['welch_t']}"
                         f" n_flag={res['n_flag']} n_stress={res['n_stress']}\n"
                         f" mean r20: flag {res['mean_r20_flag']} stress {res['mean_r20_stress']}"
                         f" all days {res['mean_r20_all_days']}\n today: {res['flag_today']}\n")
        for g in ("flag", "stress"):
            for e in res[g]["events"]:
                sys.stdout.write(f"   {g:6} {e['date']} r5={e['r5']} r20={e['r20']} worst20={e['worst20']}\n")
    p, s = report["samples"]["gold_2004"], report["samples"]["bitcoin_2014"]
    same_sign = (p["mean_r20_flag"] is not None and s["mean_r20_flag"] is not None
                 and p["mean_r20_stress"] is not None and s["mean_r20_stress"] is not None
                 and (p["mean_r20_flag"] - p["mean_r20_stress"]) * (s["mean_r20_flag"] - s["mean_r20_stress"]) > 0)
    report["confirmed"] = p["verdict"] in ("BOUNCE_AFTER_FLAG", "FALLS_FURTHER_AFTER_FLAG") and same_sign
    with open(os.path.join(paths.data_dir(), "sell_everything_study.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    sys.stdout.write(f"\nPRIMARY VERDICT: {p['verdict']}  confirmed by the bitcoin sample: {report['confirmed']}\n")


if __name__ == "__main__":
    main()
