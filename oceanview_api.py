"""
oceanview_api.py — the data behind the dashboard's OceanView tab (read-only).

One call, summary(symbol), returns everything the tab shows for a concentrated holding
(default INTC):
  - today: position-watch's report (journal=False, so the page never writes the journal);
  - retro: the journal (calls, prices, lessons) and the RETRO scoring;
  - lots: each lot's gain at today's price, long/short-term, days until long-term, and the
    lots a TRIM would sell today with the estimated tax;
  - calendar: vest/ESPP dates with their wash-sale windows (+/-30 days) and whether today is
    inside one;
  - research: the latest results of the studies that bear on the plan;
  - plan: the dated investment plan's daily check (invest_plan.py), when the private plan
    file exists.

Private inputs (Data/position_watch/: config, lots, journal) stay on the machine. The lots'
"account" field is never returned, neither in `lots`, a TRIM day's `today.lots`, nor the
plan's fills. The server route that calls this requires the login.
"""
import datetime
import glob
import json
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "monitoring"))

import invest_plan as ip  # noqa: E402
import position_watch as pw  # noqa: E402
from aether import paths  # noqa: E402

LOT_FIELDS = ("acquired", "qty", "price")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def lots_table(lots: list, price: float, today: datetime.date) -> list:
    """Each lot (account field dropped) with its gain at `price`, term and days to long-term."""
    out = []
    for lot in lots:
        acq = datetime.date.fromisoformat(lot["acquired"])
        lt_from = pw.anniversary(acq) + datetime.timedelta(days=1)
        qty = float(lot["qty"])
        out.append({**{k: lot[k] for k in LOT_FIELDS},
                    "gain": round(qty * (price - float(lot["price"])), 2),
                    "term": "LT" if today >= lt_from else "ST",
                    "days_to_lt": max(0, (lt_from - today).days)})
    return sorted(out, key=lambda x: x["acquired"])


def calendar(acquisition_dates: list, today: datetime.date) -> list:
    """Vest/ESPP dates with their wash-sale windows."""
    out = []
    for d in sorted(acquisition_dates):
        day = datetime.date.fromisoformat(d)
        lo, hi = day - datetime.timedelta(days=pw.WASH_DAYS), day + datetime.timedelta(days=pw.WASH_DAYS)
        out.append({"date": d, "window_from": lo.isoformat(), "window_to": hi.isoformat(),
                    "status": "past" if hi < today else ("now" if lo <= today <= hi else "upcoming")})
    return out


def _x60(result: dict, group: str) -> dict:
    return (result.get(group) or {}).get("x60") or {}


def research() -> list:
    """Latest result of each study that bears on the plan (missing files are skipped)."""
    out = []
    files = sorted(glob.glob(os.path.join(paths.data_dir(), "insider_buy_study_*.json")))
    r = _read_json(files[-1]) if files else None
    if r:
        # The monthly mean: the verdict and its t-stat are computed on it (#240 review).
        out.append({"id": "R&D #80", "title": "Insider open-market buys vs trims", "run": r.get("run"),
                    "verdict": r.get("verdict"), "verdict_2": r.get("verdict_drop"), "mean_basis": "monthly mean",
                    "rows": [{"group": k, "n": _x60(r, k).get("n"),
                              "mean": _x60(r, k).get("month_mean", _x60(r, k).get("mean")),
                              "median": _x60(r, k).get("median"), "t": _x60(r, k).get("t_nw")}
                             for k in ("all", "trim_setup", "opp_after_drop", "opp_after_shock") if _x60(r, k)]})
    g = _read_json(os.path.join(paths.data_dir(), "gap_drift_study.json"))
    if g:
        out.append({"id": "gap drift", "title": "After a 15%+ gap-down: sell at once, or wait?", "run": None,
                    "verdict": "no clear edge either way", "verdict_2": None, "mean_basis": "per-event mean",
                    "rows": [{"group": k, "n": _x60(g, k).get("n"), "mean": _x60(g, k).get("mean"),
                              "median": _x60(g, k).get("median"), "t": _x60(g, k).get("t_month")}
                             for k in ("all", "large") if _x60(g, k)]})
    return out


def plan(today: datetime.date) -> dict | None:
    """invest_plan.py's check of Data/position_watch/invest_plan.json on the cached bars, or None
    when there is no plan (or no SPY bars to check it with). Fills lose their account field."""
    p = _read_json(os.path.join(pw.watch_dir(), "invest_plan.json"))
    if not p:
        return None
    syms = {"SPY", "RSP"} | {f["symbol"] for f in p.get("fills", [])}
    bars = {s: pw.load_bars(s) for s in syms}
    if not bars.get("SPY"):
        return None
    r = ip.check(p, today, bars)
    r["fills"] = [{k: v for k, v in f.items() if k != "account"} for f in r["fills"]]
    return {**r, "name": p.get("name"), "total": p.get("total"), "return_by": p.get("return_by")}


def summary(symbol: str | None = None) -> dict:
    cfg = pw.load_config()
    symbol = (symbol or cfg["symbol"]).upper()
    today = datetime.date.today()
    rep = pw.run(symbol, live=False, journal=False)
    if rep.get("lots") and rep["lots"].get("lots"):          # a TRIM day's picked lots carry the account
        rep = {**rep, "lots": {**rep["lots"],
                               "lots": [{k: v for k, v in x.items() if k != "account"} for x in rep["lots"]["lots"]]}}
    lots_raw = _read_json(os.path.join(pw.watch_dir(), f"{symbol}_lots.json")) or []
    if isinstance(lots_raw, dict):
        lots_raw = lots_raw.get("lots") or []
    journal = pw.read_journal(os.path.join(pw.watch_dir(), f"{symbol}_journal.jsonl"))
    return {"symbol": symbol, "as_of": today.isoformat(), "floor_pct": cfg["floor_pct"],
            "tranche_pct": cfg["tranche_pct"], "today": rep,
            "journal": journal, "lots": lots_table(lots_raw, rep["price"], today),
            "calendar": calendar(cfg.get("acquisition_dates") or [], today),
            "research": research(), "plan": plan(today)}
