"""Tax-loss harvest list from the live lots (plan stage O2a, R&D #81). Recommend-only, read-only.

Before this, the harvest list lived in hand notes (names, losses, short/long term), so a lot bought last month or a
lot turning long-term next week was easy to miss. This computes it from E*TRADE's tax lots:

  * per lot: shares, cost, value, gain, and the term on the sale date (long-term once the sale date is past the
    one-year anniversary of the purchase, the rule position-watch uses);
  * per symbol and account: the short- and long-term loss, and the tax value of selling at the owner's rates
    (``st_rate`` / ``lt_rate`` from position-watch's private config);
  * wash-sale flags: a buy of the same symbol in any listed account within the last 30 days. Selling only the old
    lots at a loss would then be (partly) a wash sale, because the new shares stay; selling every lot, the new ones
    too, avoids that. Also the first clean buy-back date (sale date + 31 days);
  * scheduled buys of the watched symbol (vest / ESPP dates from position-watch's config, plan O2b): a loss sale
    within 30 days of one is a wash sale, so the list shows the conflicting dates and the next clean day;
  * the watched symbol's loss lots even when the whole position is up (specific-lot sales);
  * the list ranked by tax value.

It never places an order and writes nothing. Accounts the API doesn't list (the stock plan, a 401(k), an IRA at
another broker) are not checked for wash sales; the report says so. Tax figures are estimates for planning, not
advice: a CPA confirms anything that files.

    python -m aether.harvest              # every losing position, all listed accounts
    python -m aether.harvest --json       # the same as JSON (private: account digits and amounts)
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

from aether import position_watch_store as store
from aether.etrade.client import ETradeClient
from aether.lots_refresh import et_date

WASH_DAYS = 30
DEFAULT_RATES = {"st_rate": 0.371, "lt_rate": 0.281}    # position-watch's defaults; the private config overrides
NOT_CHECKED = "accounts the API doesn't list (stock plan, 401(k), other brokers) are not checked for wash sales"


def anniversary(day: datetime.date) -> datetime.date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:                       # Feb 29 -> Mar 1 of the next year
        return datetime.date(day.year + 1, 3, 1)


def is_long_term(acquired: datetime.date, sale: datetime.date) -> bool:
    """Held more than one year: the sale date is after the one-year anniversary."""
    return sale > anniversary(acquired)


def scheduled_near(sale: datetime.date, dates: list) -> list:
    """Scheduled buys (ISO dates) within 30 days either side of ``sale``: a loss sale then is a wash sale."""
    return sorted(d for d in dates if abs((sale - datetime.date.fromisoformat(d)).days) <= WASH_DAYS)


def next_clean_day(sale: datetime.date, dates: list, horizon_days: int = 400) -> str | None:
    """The first day on or after ``sale`` more than 30 days from every scheduled buy (calendar days; the
    caller picks a trading session on or after it). None when there is none within the horizon."""
    for k in range(horizon_days + 1):
        day = sale + datetime.timedelta(days=k)
        if not scheduled_near(day, dates):
            return day.isoformat()
    return None


def lot_row(raw: dict, symbol: str, account: str) -> dict | None:
    """One E*TRADE PositionLot as a plain row; None when it has no shares or no date."""
    acquired = et_date(raw.get("acquiredDate"))
    qty = float(raw.get("remainingQty") or 0)
    if not acquired or qty <= 0:
        return None
    cost = float(raw.get("totalCost") or 0) or float(raw.get("price") or 0) * qty
    value = float(raw.get("marketValue") or 0)
    return {"symbol": symbol, "account": account, "acquired": acquired, "qty": qty, "cost": cost, "value": value,
            "gain": value - cost}


def summarize(rows: list, sale: datetime.date, rates: dict, scheduled: list | None = None) -> dict:
    """One symbol across its accounts: losses by term, tax value, wash flags, buy-back date, scheduled buys."""
    st_loss = sum(min(r["gain"], 0) for r in rows if not is_long_term(datetime.date.fromisoformat(r["acquired"]), sale))
    lt_loss = sum(min(r["gain"], 0) for r in rows if is_long_term(datetime.date.fromisoformat(r["acquired"]), sale))
    gains = sum(max(r["gain"], 0) for r in rows)
    recent = sorted({r["acquired"] for r in rows
                     if 0 <= (sale - datetime.date.fromisoformat(r["acquired"])).days <= WASH_DAYS})
    turning = sorted({anniversary(datetime.date.fromisoformat(r["acquired"])).isoformat() for r in rows
                      if r["gain"] < 0 and not is_long_term(datetime.date.fromisoformat(r["acquired"]), sale)
                      and (anniversary(datetime.date.fromisoformat(r["acquired"])) - sale).days <= WASH_DAYS})
    return {"symbol": rows[0]["symbol"], "accounts": sorted({r["account"] for r in rows}),
            "qty": sum(r["qty"] for r in rows), "value": sum(r["value"] for r in rows),
            "cost": sum(r["cost"] for r in rows), "lots": len(rows),
            "st_loss": st_loss, "lt_loss": lt_loss, "gain_lots": gains,
            "tax_value": -(st_loss * rates["st_rate"] + lt_loss * rates["lt_rate"]),
            "wash_risk_buys": recent,                       # buys within 30 days: a loss sale now is (partly) a wash
            "st_lots_turning_lt": turning,                  # a short-term loss lot turning long-term within 30 days
            "buy_back_after": (sale + datetime.timedelta(days=WASH_DAYS + 1)).isoformat(),
            "scheduled_buys_near": scheduled_near(sale, scheduled or []),
            "next_clean_day": next_clean_day(sale, scheduled) if scheduled else sale.isoformat(),
            "lots_only": False}


def harvest_list(rows: list, sale: datetime.date, rates: dict | None = None, scheduled: dict | None = None,
                 lot_level: tuple = ()) -> list:
    """Symbols with a net loss, ranked by the tax value of selling them on ``sale``. ``scheduled``: {symbol:
    [ISO dates]} of planned buys (vest / ESPP). ``lot_level``: symbols whose loss lots are listed even when the
    whole position is up (sold by specific lot; the entry has ``lots_only`` True)."""
    rates = {**DEFAULT_RATES, **(rates or {})}
    scheduled = scheduled or {}
    by = {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r)
    out = []
    for sym, v in by.items():
        s = summarize(v, sale, rates, scheduled.get(sym))
        if s["value"] < s["cost"]:
            out.append(s)
        elif sym in lot_level and any(r["gain"] < 0 for r in v):
            losers = [r for r in v if r["gain"] < 0]
            s = summarize(losers, sale, rates, scheduled.get(sym))
            s["wash_risk_buys"] = summarize(v, sale, rates)["wash_risk_buys"]   # recent buys of any lot count
            s["lots_only"] = True
            out.append(s)
    return sorted(out, key=lambda s: -s["tax_value"])


# ── live read (thin adapter; read-only) ─────────────────────────────────────────────────────────
def live_rows(client=None, always: tuple = ()) -> list:
    """Every lot of every position that is below cost in some account (and of the ``always`` symbols, whose loss
    lots can be sold on their own), through the ETradeClient facade."""
    client = client or ETradeClient("production", role="data")
    keys = client.accounts.account_keys()
    positions = client.accounts.positions()
    losers = {str(p["symbol"]).upper() for p in positions
              if float(p.get("mval") or 0) < float(p.get("cost") or 0) * float(p.get("qty") or 0)}
    rows = []
    for p in positions:
        sym, last4 = str(p["symbol"]).upper(), str(p.get("account_last4") or "")
        if (sym not in losers and sym not in always) or last4 not in keys:
            continue
        for raw in client.accounts.position_lots(sym, keys[last4]):
            row = lot_row(raw, sym, last4)
            if row:
                rows.append(row)
    return rows


def _config() -> dict:
    try:
        return store.read_config() or {}
    except (OSError, ValueError):
        return {}


def _rates(cfg: dict) -> dict:
    return {k: float(cfg.get(k, v)) for k, v in DEFAULT_RATES.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tax-loss harvest list from the live lots (read-only)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sale = datetime.date.today()
    cfg = _config()
    rates = _rates(cfg)
    watched = str(cfg.get("symbol") or "").upper()
    scheduled = {watched: list(cfg.get("acquisition_dates") or [])} if watched else {}
    lst = harvest_list(live_rows(always=(watched,) if watched else ()), sale, rates, scheduled,
                       lot_level=(watched,) if watched else ())
    if a.json:
        sys.stdout.write(json.dumps({"sale_date": sale.isoformat(), "rates": rates, "note": NOT_CHECKED,
                                     "list": lst}, indent=1) + "\n")
        return 0
    sys.stdout.write(f"Harvest list for a sale on {sale} (rates ST {rates['st_rate']:.1%}, LT {rates['lt_rate']:.1%};"
                     f" estimates, not advice)\n")
    for s in lst:
        flags = []
        if s["wash_risk_buys"]:
            flags.append(f"WASH RISK: bought {', '.join(s['wash_risk_buys'])}; sell every lot or wait")
        if s["scheduled_buys_near"]:
            flags.append(f"VEST/ESPP {', '.join(s['scheduled_buys_near'])}: loss sale is a wash until {s['next_clean_day']}")
        if s["lots_only"]:
            flags.append("loss lots only: the position is up overall, sell these lots by specific ID")
        if s["st_lots_turning_lt"]:
            flags.append(f"short-term loss lot turns long-term {', '.join(s['st_lots_turning_lt'])}")
        sys.stdout.write(f"  {s['symbol']:6} {'/'.join('...' + x for x in s['accounts']):14} {s['qty']:>8.0f} sh"
                         f"  value ${s['value']:>9,.0f}  ST loss ${s['st_loss']:>9,.0f}  LT loss ${s['lt_loss']:>9,.0f}"
                         f"  tax value ${s['tax_value']:>7,.0f}  buy back after {s['buy_back_after']}"
                         f"{'  [' + '; '.join(flags) + ']' if flags else ''}\n")
    tot = sum(s["tax_value"] for s in lst)
    sys.stdout.write(f"  total tax value ${tot:,.0f} across {len(lst)} symbols. Note: {NOT_CHECKED}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
