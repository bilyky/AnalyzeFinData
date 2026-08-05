"""
Project AETHER Oracle: real-account advisory engine.
Audits the configured real E*TRADE account (config `oracle.account`, defaults to
the first real account) for stop-loss breaches and technical decay, and surfaces
buy setups toward the doubling goal in config `oracle.target_date`.

Account id, baseline equity, and target date come from config (gitignored) — never
hardcode account numbers or balances in source.
"""
import datetime

import ai_portfolio_game
import data_api
from aether.config import CFG
from aether_logger import get_logger as _get_logger

_oracle_log = _get_logger("oracle")


def get_oracle_account():
    """Return live data for the configured real account, or None if unavailable.

    Zero-Trust: never fabricate balances. When the account cannot be read, callers
    render a "data unavailable" state rather than advising against a made-up equity.
    """
    target = CFG.oracle_account
    if not target:
        _oracle_log.warning("Oracle has no configured account (config oracle.account / accounts.real).")
        return None
    try:
        accts_data = data_api.read_accounts()
        for acct in accts_data.get("accounts", []):
            if acct.get("id") == target:
                return acct
        _oracle_log.warning(f"Oracle account {target} not found in live E*TRADE accounts.")
    except Exception as e:
        _oracle_log.warning(f"Failed to load E*TRADE accounts for Oracle: {e}")
    return None

def audit_oracle_portfolio(acct):
    """
    Audit active holdings for the configured real E*TRADE account.
    Returns: (sells, holds) lists of formatted dicts.
    """
    sells = []
    holds = []
    
    holdings = acct.get("holdings", [])
    for h in holdings:
        sym = h.get("symbol", "").upper()
        # Skip non-tradable proxy/cash entries (CUSIP-like symbols contain digits;
        # real equity tickers are alpha-only).
        if not sym or any(c.isdigit() for c in sym):
            continue
            
        qty = h.get("qty", 0.0)
        cost = h.get("buy", 0.0)
        current = h.get("current", 0.0)
        stop = h.get("stop")
        pnl_pct = h.get("pnl_pct", 0.0)
        s10 = h.get("s10", 0.0) or 0.0
        l60 = h.get("l60", 0.0) or 0.0
        total = s10 + l60
        status = h.get("status", "")
        
        # ── Criteria 1: Stop-Loss Breach (Crucial) ──
        in_breach = False
        if stop is not None and current <= stop:
            in_breach = True
            
        # ── Criteria 2: Technical Momentum Collapse ──
        tech_decay = False
        if total < -2.0 or status == "REDUCE":
            tech_decay = True

        h_data = {
            "symbol": sym,
            "qty": qty,
            "cost": cost,
            "current": current,
            "stop": stop,
            "pnl_pct": pnl_pct,
            "s10": s10,
            "l60": l60,
            "total": total,
            "status": status,
            "reason": ""
        }
        
        if in_breach:
            h_data["reason"] = f"CRITICAL STOP BREACH: Price ${current:.2f} fell below stop floor of ${stop:.2f}."
            sells.append(h_data)
        elif tech_decay:
            h_data["reason"] = f"MOMENTUM COLLAPSE: Technical score is decaying heavily at {total:.1f} (Short10: {s10:.1f}, Long60: {l60:.1f})."
            sells.append(h_data)
        else:
            holds.append(h_data)
            
    return sells, holds

def get_oracle_buy_candidates(acct):
    """
    Query the research rows to find the top active setups that we don't already hold.
    Returns sorted list of top candidates.
    """
    held_syms = {h.get("symbol", "").upper() for h in acct.get("holdings", [])}
    candidates = []

    try:
        research_data = data_api.read_research()
        rows = research_data.get("rows", [])
        
        for r in rows:
            sym = r.get("symbol", "").upper()
            if not sym or sym in held_syms:
                continue
            
            # Check setup criteria
            if r.get("setup") is True:
                # Calculate combined score
                s10 = r.get("s10", 0.0) or 0.0
                l60 = r.get("l60", 0.0) or 0.0
                combined = s10 + l60
                
                # Check momentum floor
                if s10 < 2.5:
                    continue
                    
                # Rejects if bubble guard trips
                z_score = ai_portfolio_game.calculate_bubble_z_score(sym)
                if z_score is not None and z_score >= 2.5:
                    continue  # bubble zone
                    
                # Enforce strict Risk/Reward verification gate (Reward must be >= 1.5x of the Risk)
                price = r.get("price", 0.0) or 0.0
                stop = r.get("stop", 0.0) or 0.0
                target = r.get("target", 0.0) or 0.0
                
                risk = price - stop
                reward = target - price
                rr_ratio = (reward / risk) if risk > 0 else 0.0
                
                if rr_ratio < 1.5:
                    continue  # Fail-safe R:R check: reject unfavorable narrow-upside setups
                    
                candidates.append({
                    "symbol": sym,
                    "price": price,
                    "stop": stop,
                    "target": target,
                    "s10": s10,
                    "l60": l60,
                    "combined": combined,
                    "pgr": r.get("pgr", "N/A"),
                    "patterns": r.get("patterns", "")
                })
                
        # Sort by combined score descending
        candidates.sort(key=lambda x: x["combined"], reverse=True)
    except Exception as e:
        _oracle_log.warning(f"Oracle failed to query buy candidates: {e}")
        
    return candidates

def _target_date() -> datetime.date | None:
    raw = (CFG.oracle_target_date or "").strip()
    try:
        return datetime.date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def generate_oracle_html(acct, sells, holds, buys):
    """Render the AETHER Oracle advisory section as HTML."""
    today = datetime.date.today()
    target_date = _target_date()
    days_left = (target_date - today).days if target_date else None
    target_label = target_date.strftime("%m/%d/%Y") if target_date else "N/A"
    acct_id = (acct.get("id") or CFG.oracle_account or "").strip()

    current_equity = acct.get("equity")
    start_equity = CFG.oracle_start_equity or 0.0
    double_target = start_equity * 2
    if current_equity and double_target > 0:
        progress_pct = min(100.0, max(0.0, (current_equity / double_target) * 100.0))
    else:
        progress_pct = 0.0

    # Render progress bar
    progress_bar = f"""
    <div style="background: #30363d; border-radius: 4px; height: 16px; width: 100%; margin: 10px 0; overflow: hidden; border: 1px solid #444c56;">
        <div style="background: linear-gradient(90deg, #58a6ff 0%, #1f6feb 100%); width: {progress_pct:.1f}%; height: 100%; border-radius: 3px;"></div>
    </div>
    """

    # Factual advisory summary (capital-preservation tone — no hype)
    pitch = ""
    if sells:
        pitch += f"<b>{len(sells)} position(s)</b> in the real portfolio are in stop-loss breach or technical-momentum decay. Reclaiming that capital is the priority. "
        if buys:
            best_buy = buys[0]
            pitch += f"Cutting them frees cash to rotate into <b>{best_buy['symbol']}</b>, a confirmed setup with a combined momentum score of +{best_buy['combined']:.1f} (PGR: {best_buy['pgr']})."
        else:
            pitch += "No qualifying buy setups today — hold the freed capital as a cash cushion until one appears."
    elif buys:
        best_buy = buys[0]
        pitch += f"All positions are above their technical floors. The highest-conviction candidate today is <b>{best_buy['symbol']}</b> at ${best_buy['price']:.2f} (stop ${best_buy['stop'] or 0.0:.2f}, target ${best_buy['target'] or 0.0:.2f}, combined score +{best_buy['combined']:.1f})."
    else:
        pitch += "All active positions are holding above their technical floors and no buy candidate meets the risk-reward threshold today. No action recommended — hold."

    # Sells Table/Cards
    sells_html = ""
    if sells:
        for s in sells:
            pnl_color = "#ff7b72" if s["pnl_pct"] < 0 else "#7ee787"
            sells_html += f"""
            <div style="background-color: #1c1313; border: 1px solid #f85149; border-radius: 6px; padding: 12px; margin-bottom: 10px; border-left: 5px solid #f85149;">
                <div style="font-weight: bold; font-size: 15px; color: #ff7b72;">🚨 REALLOCATE: {s['symbol']} ({s['qty']} shares @ ${s['cost']:.2f})</div>
                <div style="font-size: 13px; color: #c9d1d9; margin: 5px 0;"><b>Rationale:</b> {s['reason']}</div>
                <div style="font-size: 12px; color: #8b949e;">Current: ${s['current']:.2f} | P&L: <span style="color: {pnl_color}; font-weight: bold;">{s['pnl_pct']:.1f}%</span> | Technical Score: {s.get('total', 0.0):.1f}</div>
            </div>
            """
    else:
        sells_html = "<div style='color: #8b949e; font-style: italic; padding: 10px;'>No sells or reallocations recommended today. Portfolio is holding above risk floors.</div>"

    # Holds Table/Cards
    holds_html = ""
    if holds:
        for h in holds:
            pnl_color = "#ff7b72" if h["pnl_pct"] < 0 else "#7ee787"
            pnl_sign = "+" if h["pnl_pct"] >= 0 else ""
            holds_html += f"""
            <tr style="border-bottom: 1px solid #21262d;">
                <td style="padding: 8px; font-weight: bold; color: #58a6ff;">{h['symbol']}</td>
                <td style="padding: 8px; text-align: center; color: #c9d1d9;">{h['qty']}</td>
                <td style="padding: 8px; text-align: right; color: #c9d1d9;">${h['cost']:.2f}</td>
                <td style="padding: 8px; text-align: right; color: #c9d1d9;">${h['current']:.2f}</td>
                <td style="padding: 8px; text-align: right; color: {pnl_color}; font-weight: bold;">{pnl_sign}{h['pnl_pct']:.1f}%</td>
                <td style="padding: 8px; text-align: right; color: #8b949e;">{h['total']:.1f}</td>
            </tr>
            """
    else:
        holds_html = "<tr><td colspan='6' style='color: #8b949e; text-align: center; padding: 15px; font-style: italic;'>No holdings in stable condition.</td></tr>"

    # Buys Table/Cards
    buys_html = ""
    if buys:
        for b in buys[:3]:
            buys_html += f"""
            <div style="background-color: #0f1c13; border: 1px solid #56d364; border-radius: 6px; padding: 12px; margin-bottom: 10px; border-left: 5px solid #56d364;">
                <div style="font-weight: bold; font-size: 15px; color: #7ee787;">🟢 TRIGGER ACTIVE: {b['symbol']} (Entry Price: ${b['price']:.2f})</div>
                <div style="font-size: 13px; color: #c9d1d9; margin: 5px 0;"><b>Advisor Note:</b> Confirmed bottom setup, trend-momentum score +{b['combined']:.1f} (PGR: {b['pgr']}). Patterns: {b['patterns'] or 'Breakout'}.</div>
                <div style="font-size: 12px; color: #8b949e;">Est. Stop-Loss: ${b['stop'] or 0.0:.2f} | Profit Target: ${b['target'] or 0.0:.2f}</div>
            </div>
            """
    else:
        buys_html = "<div style='color: #8b949e; font-style: italic; padding: 10px;'>No new buy setups meet our strict qualification threshold today.</div>"

    acct_mask = f"...{acct_id}" if acct_id else "real account"
    days_str = str(days_left) if days_left is not None else "N/A"
    equity_str = f"${current_equity:,.2f}" if current_equity else "unavailable"
    balance = acct.get("balance", 0.0) or 0.0

    html_content = f"""
    <div style="font-family: monospace; background-color: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 8px; padding: 25px; margin-top: 40px; box-shadow: 0 4px 6px rgba(0,0,0,0.15);">
        <h2 style="color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 12px; margin-top: 0; font-size: 20px; font-weight: bold; letter-spacing: 0.5px;">
            💎 AETHER Oracle Market Advisory (Project Oracle)
        </h2>

        <!-- Objective Progress Card -->
        <table border="0" cellpadding="10" cellspacing="0" style="width: 100%; border-collapse: collapse; background: #161b22; border-radius: 6px; margin-bottom: 20px; border: 1px solid #30363d;">
            <tr>
                <td style="padding: 15px; vertical-align: top; width: 50%;">
                    <div style="font-size: 11px; color: #8b949e; text-transform: uppercase; font-weight: bold;">Advisory Target</div>
                    <div style="font-size: 20px; font-weight: bold; color: #58a6ff; margin: 4px 0;">Double Real Account ({acct_mask})</div>
                    <div style="font-size: 12px; color: #8b949e;">Target date: {target_label} | Days Remaining: <b>{days_str}</b></div>
                </td>
                <td style="padding: 15px; vertical-align: top; width: 50%; border-left: 1px solid #30363d;">
                    <div style="font-size: 11px; color: #8b949e; text-transform: uppercase; font-weight: bold;">Portfolio Standing</div>
                    <div style="font-size: 20px; font-weight: bold; color: #7ee787; margin: 4px 0;">{equity_str} / ${double_target:,.2f}</div>
                    <div style="font-size: 12px; color: #8b949e;">Current Balance: <b>${balance:,.2f}</b> | Progress: <b>{progress_pct:.1f}%</b></div>
                </td>
            </tr>
            <tr>
                <td colspan="2" style="padding: 5px 15px 15px 15px;">
                    {progress_bar}
                </td>
            </tr>
        </table>
        
        <!-- Guru Pitch -->
        <div style="background-color: #1f242c; border: 1px solid #388bfd; border-radius: 6px; padding: 15px; line-height: 1.6; font-size: 14px; margin-bottom: 25px; border-left: 5px solid #388bfd; color: #e6edf3;">
            <b>🔮 Advisory Perspective:</b><br>
            {pitch}
        </div>
        
        <!-- Sell Recommendations -->
        <h3 style="color: #ff7b72; font-size: 15px; border-bottom: 1px solid #30363d; padding-bottom: 5px; margin-top: 25px;">
            🚨 Tactical Action: Capital Reallocations ({len(sells)})
        </h3>
        {sells_html}
        
        <!-- Buy Recommendations -->
        <h3 style="color: #7ee787; font-size: 15px; border-bottom: 1px solid #30363d; padding-bottom: 5px; margin-top: 25px;">
            🟢 Top High-Conviction Breakout Setup Recommendations
        </h3>
        {buys_html}
        
        <!-- Holds List -->
        <h3 style="color: #58a6ff; font-size: 15px; border-bottom: 1px solid #30363d; padding-bottom: 5px; margin-top: 25px;">
            🛡️ Current Holdings Audit in Stable Standing ({len(holds)})
        </h3>
        <table border="0" cellpadding="0" cellspacing="0" style="width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 10px;">
            <thead>
                <tr style="background: #161b22; color: #8b949e; border-bottom: 1px solid #30363d;">
                    <th style="padding: 8px; text-align: left;">Symbol</th>
                    <th style="padding: 8px; text-align: center;">Qty</th>
                    <th style="padding: 8px; text-align: right;">Cost Basis</th>
                    <th style="padding: 8px; text-align: right;">Current</th>
                    <th style="padding: 8px; text-align: right;">P&L</th>
                    <th style="padding: 8px; text-align: right;">Tech Score</th>
                </tr>
            </thead>
            <tbody>
                {holds_html}
            </tbody>
        </table>
        
        <p style="margin-top: 35px; border-top: 1px solid #30363d; padding-top: 15px; font-size: 11px; color: #8b949e; line-height: 1.5;">
            ⚠️ <i><b>Advisor Disclosure:</b> AETHER Oracle is a quantitative, data-driven reasoning framework. All advice is informational. This model adapts to any uncontrolled manual state in account {acct_mask} and recalculates reallocations toward the {target_label} milestone. No automated trading orders have been placed on your real-money brokerage account.</i>
        </p>
    </div>
    """
    return html_content

def _unavailable_html() -> str:
    """Rendered when the real account cannot be read — never advise on fabricated data."""
    return (
        "<div style='font-family: monospace; background:#0d1117; color:#c9d1d9; "
        "border:1px solid #30363d; border-radius:8px; padding:25px; margin-top:40px;'>"
        "<h2 style='color:#58a6ff; margin-top:0;'>💎 AETHER Oracle Market Advisory (Project Oracle)</h2>"
        "<p style='color:#d29922;'>⚠️ Real account data is currently unavailable "
        "(E*TRADE not reachable or account not configured). No advisory is generated — "
        "Zero-Trust policy forbids recommending actions against unverified balances.</p></div>"
    )


def run_oracle_advisory():
    """Main entry point to fetch and compile the Oracle advisory report."""
    try:
        acct = get_oracle_account()
        if acct is None:
            return _unavailable_html()
        sells, holds = audit_oracle_portfolio(acct)
        buys = get_oracle_buy_candidates(acct)
        return generate_oracle_html(acct, sells, holds, buys)
    except Exception as e:
        _oracle_log.error("Fatal failure running AETHER Oracle advisory", exc_info=True)
        return f"<div style='color:#ff7b72; padding:20px; border:1px solid #f85149; background:#1c1313;'>AETHER Oracle Advisory failed to run today: {e}</div>"

if __name__ == "__main__":
    _oracle_log.info("[Oracle] Running standalone advisory audit...")
    html = run_oracle_advisory()
    _oracle_log.info(f"[Oracle] Generated HTML. Sample output len: {len(html)}")
