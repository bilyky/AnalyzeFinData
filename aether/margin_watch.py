"""
margin_watch.py — margin health of each real account, for the Oracle's daily advisory (R&D #39).

The Oracle audits one configured account, so a margined real account it doesn't trade could
reach a call unseen. This module computes, per account, from the broker's live figures only:

  - debt = holdings value - net account value, which matched the broker's "Margin Balance" when
    checked. The cash field is left out: it can hold a small positive amount while the broker shows
    no cash, which would overstate the debt. An account with idle cash comes out <= 0 and is
    skipped (no loan); when no holding has a price, the requirement can't be computed and the view
    says so ("unpriced") instead of guessing;
  - the maintenance requirement: each holding's value times its requirement. Requirements come
    from a private, owner-maintained file (Data/margin_requirements.json, copied from the broker's
    margin page: {"<last4>": {"SYMBOL": 0.30, ...}}); a holding missing from it is assumed at
    DEFAULT_REQ and the report says how much of the requirement is assumed;
  - excess (equity - requirement), and the uniform fall of the holdings that reaches the call;
  - interest per month at the owner's rate (config oracle.margin_rate_pct), when it is set;
  - the broker's own figures when the account carries them (data_api's "margin": marginBalance,
    regtEquity, OpenCalls.*): they replace the computed debt / equity, and an open call (a negative
    houseCall, fedCall, minEquityCall or cashCall) is shown as the broker states it;
  - the pay-down order: sales that free the most requirement per dollar first (a 100% name frees
    $1 per $1 sold, a 30% name $0.30), losses before gains (they also harvest a tax loss).

Recommend-only. It never places an order. With no live broker data it returns nothing: the
Oracle never advises against unverified balances.
"""
import json
import os

from aether import paths

DEFAULT_REQ = 0.30


def load_requirements(path: str | None = None) -> dict:
    """{last4: {SYMBOL: requirement}} from the private file, or {} when it is missing or broken."""
    path = path or os.path.join(paths.data_dir(), "margin_requirements.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    return {str(k): {str(s).upper(): float(r) for s, r in v.items()} for k, v in raw.items() if isinstance(v, dict)}


def _value(h: dict) -> float:
    return float(h.get("qty") or 0) * float(h.get("current") or 0)


def account_view(acct: dict, reqs: dict, rate_pct: float | None = None) -> dict | None:
    """Margin view of one account dict from data_api.read_accounts(), or None without debt."""
    holds = [h for h in acct.get("holdings", []) if _value(h) > 0]
    value = sum(_value(h) for h in holds)
    m = acct.get("margin") or {}
    if "margin" in acct and m.get("margin_balance") is None:
        return None      # the broker answered and reports no margin loan (a cash account): nothing to watch.
        #                  Computing holdings - net value there only measures the dashboard's price overrides.
    equity = m["margin_equity"] if m.get("margin_equity") is not None else float(acct.get("equity") or 0)
    debt = -m["margin_balance"] if m.get("margin_balance") is not None else value - equity
    if debt <= 0.5:                          # no margin loan: nothing to watch
        return None
    table = reqs.get(str(acct.get("id")), {})
    rows, assumed = [], 0.0
    for h in holds:
        sym = str(h.get("symbol", "")).upper()
        r = table.get(sym)
        if r is None:
            r = DEFAULT_REQ
            assumed += r * _value(h)
        v = _value(h)
        rows.append({"symbol": sym, "value": v, "req": r, "freed": r * v, "pnl": float(h.get("pnl") or 0)})
    requirement = sum(x["freed"] for x in rows)
    excess = equity - requirement
    denom = value - requirement
    call_drop = (1 - debt / denom) if denom > 0 else None   # <= 0: already at or under the requirement
    order = sorted(rows, key=lambda x: (-x["req"], x["pnl"]))
    monthly = debt * rate_pct / 100 / 12 if rate_pct else None
    for x in order:
        x["interest_saved_month"] = x["value"] * rate_pct / 100 / 12 if rate_pct else None
    calls = {k: m.get(k) for k in ("house_call", "fed_call", "min_equity_call", "cash_call")}
    open_calls = {k: -v for k, v in calls.items() if v is not None and v < 0}
    return {"id": acct.get("id"), "holdings_value": value, "debt": debt, "equity": equity,
            "unpriced": not holds and bool(acct.get("holdings")),
            "source": "broker" if m.get("margin_equity") is not None else "computed", "open_calls": open_calls,
            "equity_pct": equity / value if value else None, "requirement": requirement,
            "requirement_assumed": assumed, "excess": excess, "call_drop": call_drop,
            "interest_month": monthly, "pay_down": order[:5]}


def views(accounts_data: dict, real_ids: list, reqs: dict, rate_pct: float | None = None) -> list:
    """Views for the real accounts, only when the broker data is live."""
    if (accounts_data or {}).get("broker_status") != "live":
        return []
    out = []
    for a in accounts_data.get("accounts", []):
        if str(a.get("id")) in {str(x) for x in real_ids}:
            v = account_view(a, reqs, rate_pct)
            if v:
                out.append(v)
    return out


def _money(x):
    return "—" if x is None else f"${x:,.0f}"


def render_html(vs: list) -> str:
    """The Oracle's margin section (empty string when no account carries margin)."""
    if not vs:
        return ""
    cards = []
    for v in vs:
        if v.get("unpriced"):
            cards.append(
                f"<div style='border:1px solid #30363d; border-radius:6px; padding:12px; margin:10px 0;'>"
                f"<b>Account ...{str(v['id'])[-4:]}</b>: debt {_money(v['debt'])}; "
                f"<span style='color:#d29922;'>holdings unpriced, so the requirement can't be computed.</span>"
                f"{' Open call per the broker: ' + ', '.join(f'{k} {_money(a)}' for k, a in v['open_calls'].items()) if v.get('open_calls') else ''}"
                f"</div>")
            continue
        if v.get("open_calls"):
            due = ", ".join(f"{k.replace('_', ' ')} {_money(a)}" for k, a in v["open_calls"].items())
            status = f"<span style='color:#f85149;'>OPEN CALL per the broker: {due}</span>"
        elif v["call_drop"] is None or v["call_drop"] <= 0:
            status = f"<span style='color:#f85149;'>AT/UNDER the requirement (excess {_money(v['excess'])})</span>"
        elif v["call_drop"] < 0.10:
            status = f"<span style='color:#d29922;'>thin: a {v['call_drop']:.1%} fall reaches the call</span>"
        else:
            status = f"<span style='color:#3fb950;'>a {v['call_drop']:.1%} fall reaches the call</span>"
        note = ""
        if v["requirement_assumed"] > 0:
            note = (f"<br><i>{_money(v['requirement_assumed'])} of the requirement is assumed at "
                    f"{DEFAULT_REQ:.0%}: add the broker's figures to Data/margin_requirements.json.</i>")
        rows = "".join(
            f"<tr><td>{x['symbol']}</td><td>{_money(x['value'])}</td><td>{x['req']:.0%}</td>"
            f"<td>{_money(x['freed'])}</td><td>{_money(x['pnl'])}</td>"
            f"<td>{_money(x['interest_saved_month'])}</td></tr>" for x in v["pay_down"])
        cards.append(
            f"<div style='border:1px solid #30363d; border-radius:6px; padding:12px; margin:10px 0;'>"
            f"<b>Account ...{str(v['id'])[-4:]}</b>: debt {_money(v['debt'])}, equity {_money(v['equity'])} "
            f"({v['equity_pct']:.0%} of holdings), requirement {_money(v['requirement'])}; {status}."
            f"{' Interest ~' + _money(v['interest_month']) + '/month.' if v['interest_month'] else ''}{note}"
            f"<table style='margin-top:8px; font-size:12px;'><tr><th align='left'>Pay down first</th><th>value</th>"
            f"<th>req</th><th>frees</th><th>P/L</th><th>interest saved/mo</th></tr>{rows}</table></div>")
    return ("<div style='font-family: monospace; color:#c9d1d9; margin-top:20px;'>"
            "<h3 style='color:#58a6ff;'>🧮 Margin watch (R&amp;D #39)</h3>"
            "<p style='font-size:11px; color:#8b949e;'>From the broker's live figures; recommend-only. The broker's own "
            "call notice and deadline are the authority.</p>" + "".join(cards) + "</div>")
