"""
Pure data-reading layer for the AETHER web dashboard.
No HTTP, no FastAPI — only reads from files and calls existing modules.
All functions are safe to call from async FastAPI route handlers.
"""

import json
import os
import subprocess
import time
from datetime import datetime, date
from pathlib import Path

_DIR      = Path(__file__).resolve().parent
_DATA_DIR = _DIR / "Data"
_XLSX     = _DATA_DIR / "state_of_the_day.xlsx"
_GAME     = _DATA_DIR / "ai_portfolio_game.json"
_LOG      = _DATA_DIR / "autonomous_run.log"
_PERF     = _DATA_DIR / "performance_log.json"

# ── Simple in-process TTL cache ───────────────────────────────────────────────

_cache: dict = {}


def _cached(key: str, ttl: float, fn):
    entry = _cache.get(key)
    now = time.monotonic()
    if entry and now - entry["ts"] < ttl:
        return entry["val"]
    val = fn()
    _cache[key] = {"ts": now, "val": val}
    return val


# ── Portfolio ─────────────────────────────────────────────────────────────────

def read_portfolio() -> dict:
    """Read ai_portfolio_game.json and compute position-level P&L."""
    try:
        with open(_GAME, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"balance": 0, "equity": 0, "return_pct": 0, "positions": [],
                "profile": "UNKNOWN", "open_positions": 0, "max_positions": 5,
                "start_date": "", "total_return": 0}

    positions = state.get("positions", {})
    balance   = state.get("balance", 0)
    history   = state.get("history", [])

    # Derive initial balance from history or default
    initial = 10000.0
    pos_list = []
    for sym, pos in positions.items():
        cost  = pos.get("cost", 0)
        qty   = pos.get("qty", 0)
        stop  = pos.get("stop_loss", 0)
        # Compute days held from history
        days_held = 0
        for tx in reversed(history):
            if tx.get("symbol") == sym and tx.get("type") == "BUY":
                try:
                    d = datetime.fromisoformat(tx["date"]).date()
                    days_held = (date.today() - d).days
                except Exception:
                    pass
                break
        pos_list.append({
            "symbol":        sym,
            "qty":           qty,
            "cost":          round(cost, 2),
            "current_price": round(cost, 2),  # filled by /api/prices live refresh
            "pnl":           0.0,
            "pnl_pct":       0.0,
            "stop_loss":     round(stop, 2),
            "days_held":     days_held,
        })

    equity = state.get("equity", balance)
    return_pct = round((equity - initial) / initial * 100, 2) if initial else 0

    return {
        "balance":        round(balance, 2),
        "equity":         round(equity, 2),
        "return_pct":     return_pct,
        "total_return":   round(equity - initial, 2),
        "profile":        state.get("profile", "BALANCED"),
        "positions":      pos_list,
        "open_positions": len(pos_list),
        "max_positions":  5,
        "start_date":     state.get("start_date", ""),
    }


# ── Picks & replacements ──────────────────────────────────────────────────────

def read_picks() -> dict:
    """Cached 60s — reads state_of_the_day.xlsx Research sheet."""
    def _load():
        try:
            import autonomous_pipeline as _ap
            picks = _ap.get_top_5_picks()
            regime, color = _ap.get_market_regime()
            return {"market_regime": regime, "regime_color": color, "picks": picks}
        except Exception as e:
            return {"market_regime": "Unknown", "regime_color": "#7f8c8d",
                    "picks": [], "error": str(e)}
    return _cached("picks", 60.0, _load)


def read_replacements() -> dict:
    """Cached 60s — reads Replacements sheet."""
    def _load():
        try:
            import autonomous_pipeline as _ap
            pairs = _ap.get_replacement_pairs()
            return {"pairs": pairs}
        except Exception as e:
            return {"pairs": [], "error": str(e)}
    return _cached("replacements", 60.0, _load)


def read_reserves() -> dict:
    """Cached 60s — reads A-Reserves from game state + Research sheet scores."""
    def _load():
        try:
            import autonomous_pipeline as _ap
            data = _ap.get_reserves_data()
            return {"reserves": data}
        except Exception as e:
            return {"reserves": [], "error": str(e)}
    return _cached("reserves", 60.0, _load)


# ── Research sheet (full screener output) ──────────────────────────────────────

# Research sheet column layout (0-based tuple index), matching powergauge.py writes.
_RESEARCH = {"sym": 3, "industry": 4, "prev_pgr": 5, "pgr": 6, "ind_strength": 7,
             "stop": 9, "price": 10, "target": 11, "risk_ratio": 12,
             "lt_trend": 17, "money_flow": 18, "obos": 19, "setup": 20,
             "buying_ratio": 21, "seasonality": 22, "winpct": 23,
             "s10": 24, "l60": 25, "patterns": 26}


def read_research() -> dict:
    """All screened symbols from the Research sheet with their full computed fields
    (PGR, S10/L60, setup flag, buying ratio, money flow, OB/OS, win%, patterns …)
    plus a summary block. Cached 60s."""
    def _load():
        import openpyxl
        import sell_rules
        import risk_utils
        if not _XLSX.exists():
            return {"rows": [], "summary": {}, "error": "state_of_the_day.xlsx not found"}
        rows = []
        wb = None
        try:
            wb = openpyxl.load_workbook(_XLSX, read_only=True, data_only=True)
            ws = wb["Research"]
            maxi = max(_RESEARCH.values())
            for r in ws.iter_rows(min_row=2, values_only=True):
                if len(r) <= maxi:
                    continue
                sym = r[_RESEARCH["sym"]]
                if not sym or not isinstance(sym, str) or sym.strip().upper() == "SYMB":
                    continue
                g = lambda k: r[_RESEARCH[k]]
                s10, l60 = _f(g("s10")), _f(g("l60"))
                setup_raw = g("setup")
                win = _f(g("winpct"))
                price = _f(g("price"))
                # Prefer the stop already in the sheet; only detect one (swing-low ->
                # ATR -> 8%) when it's missing/0, so we don't recompute needlessly.
                sheet_stop = _f(g("stop"))
                stop = sheet_stop if (sheet_stop and sheet_stop > 0) \
                    else risk_utils.resolve_stop(price, symbol=sym.strip())
                rows.append({
                    "symbol": sym.strip(),
                    "industry": g("industry"),
                    "pgr": g("pgr"), "prev_pgr": g("prev_pgr"),
                    # These four are categorical text ratings (e.g. Weak/Neutral/Wait),
                    # not numbers — pass through raw.
                    "industry_strength": g("ind_strength"),
                    "lt_trend": g("lt_trend"), "money_flow": g("money_flow"),
                    "obos": g("obos"),
                    "price": price, "stop": stop,
                    "target": _f(g("target")), "risk_ratio": _f(g("risk_ratio")),
                    "setup": str(setup_raw) in ("1", "OK") or setup_raw == 1,
                    "buying_ratio": _f(g("buying_ratio")),
                    "seasonality": _f(g("seasonality")),
                    "win_pct": round(win * 100, 1) if win is not None else None,
                    "s10": s10, "l60": l60,
                    "combined": round((s10 or 0) + (l60 or 0), 1),
                    "status": sell_rules.status_label(l60),
                    "patterns": g("patterns") or "",
                })
        except Exception as e:
            return {"rows": [], "summary": {}, "error": str(e)}
        finally:
            if wb:
                wb.close()

        setups = sum(1 for x in rows if x["setup"])
        bullish = sum(1 for x in rows if x["combined"] > 0)
        combos = [x["combined"] for x in rows]
        summary = {
            "total": len(rows),
            "setups": setups,
            "bullish": bullish,
            "bearish": sum(1 for x in rows if x["combined"] < 0),
            "avg_combined": round(sum(combos) / len(combos), 2) if combos else 0.0,
        }
        try:
            import autonomous_pipeline as _ap
            regime, color = _ap.get_market_regime()
            summary["market_regime"], summary["regime_color"] = regime, color
        except Exception:
            summary["market_regime"], summary["regime_color"] = "Unknown", "#7f8c8d"
        return {"rows": rows, "summary": summary}

    return _cached("research", 60.0, _load)


# ── Accounts (2 real from Short_Long sheet + 1 AI game) ────────────────────────

# Short_Long column layout (0-based), matching excel_output.update_short_long_scores.
_SL = {"sym": 1, "qty": 2, "buy": 3, "top": 4, "target": 5, "stop": 6,
       "s10": 16, "l60": 17, "winpct": 18, "status": 19, "in_profit": 22}
# The two real E*TRADE accounts (last-4 IDs), top table first. Sourced from config
# (PII — never hardcode). Falls back to generic T1/T2 labels if unset.
def _real_acct_ids():
    try:
        from config import CFG
        return CFG.accounts_real or []
    except Exception:
        return []


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_accounts() -> dict:
    """Return the two real accounts (parsed from the Short_Long sheet's two tables)
    plus the AI game account (ai_portfolio_game.json). Cached 30s."""
    def _load():
        accounts = []

        # ── Real accounts: parse the two Short_Long tables ──────────────────
        real_ids = _real_acct_ids()
        try:
            import openpyxl
            wb = openpyxl.load_workbook(_XLSX, data_only=True, read_only=True)
            try:
                rows = list(wb["Short_Long"].iter_rows(values_only=True))
            finally:
                wb.close()

            hdrs = [i for i, r in enumerate(rows)
                    if len(r) > 1 and str(r[1]).strip() == "Symb"]

            for tbl_idx, h in enumerate(hdrs[:2]):
                holdings, started = [], False
                for r in rows[h + 1:]:
                    sym = r[_SL["sym"]] if len(r) > _SL["sym"] else None
                    sym = str(sym).strip() if sym else ""
                    if not sym:
                        if started:
                            break          # blank row after data → end of table
                        continue            # skip leading blanks between header and data
                    started = True
                    buy = _f(r[_SL["buy"]]); top = _f(r[_SL["top"]]); qty = _f(r[_SL["qty"]])
                    s10 = _f(r[_SL["s10"]]); l60 = _f(r[_SL["l60"]])
                    pnl = pnl_pct = None
                    if buy and top and qty:
                        pnl = round((top - buy) * qty, 2)
                        pnl_pct = round((top - buy) / buy * 100, 2)
                    holdings.append({
                        "symbol":    sym,
                        "qty":       qty,
                        "buy":       buy,
                        "current":   top,     # sheet's last price; live-refreshed client-side
                        "target":    _f(r[_SL["target"]]),
                        "stop":      _f(r[_SL["stop"]]),
                        "s10":       s10,
                        "l60":       l60,
                        "total":     round((s10 or 0) + (l60 or 0), 1),
                        "win_pct":   r[_SL["winpct"]],
                        "status":    str(r[_SL["status"]] or ""),
                        "in_profit": str(r[_SL["in_profit"]] or ""),
                        "pnl":       pnl,
                        "pnl_pct":   pnl_pct,
                    })
                acct_id = real_ids[tbl_idx] if tbl_idx < len(real_ids) else f"T{tbl_idx+1}"
                accounts.append({
                    "id":       acct_id,
                    "label":    f"Real · {acct_id}",
                    "type":     "real",
                    "holdings": holdings,
                    "count":    len(holdings),
                })
        except FileNotFoundError:
            pass
        except Exception as e:
            accounts.append({"id": "real", "label": "Real accounts", "type": "real",
                             "holdings": [], "count": 0, "error": str(e)})

        # ── AI game account ─────────────────────────────────────────────────
        pf = read_portfolio()
        accounts.append({
            "id":       "game",
            "label":    "AI Game",
            "type":     "game",
            "balance":  pf["balance"],
            "equity":   pf["equity"],
            "return_pct": pf["return_pct"],
            "profile":  pf["profile"],
            "holdings": pf["positions"],
            "count":    pf["open_positions"],
        })

        return {"accounts": accounts}
    return _cached("accounts", 30.0, _load)


# ── Transaction history ───────────────────────────────────────────────────────

def read_history(limit: int = 50, offset: int = 0) -> dict:
    """Read transaction log from ai_portfolio_game.json history array."""
    try:
        with open(_GAME, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"total": 0, "transactions": [], "win_rate": 0, "total_pnl": 0}

    history = list(reversed(state.get("history", [])))  # newest first
    total   = len(history)
    page    = history[offset: offset + limit]

    sells   = [t for t in history if t.get("type") == "SELL" and t.get("pnl") is not None]
    wins    = [t for t in sells if (t.get("pnl") or 0) > 0]
    win_rate = round(len(wins) / len(sells) * 100, 1) if sells else 0
    total_pnl = round(sum(t.get("pnl") or 0 for t in sells), 2)

    return {
        "total":        total,
        "transactions": page,
        "win_rate":     win_rate,
        "total_pnl":    total_pnl,
    }


def read_equity_curve() -> list[dict]:
    """Reconstruct daily equity snapshots from transaction history."""
    try:
        with open(_GAME, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    history  = sorted(state.get("history", []), key=lambda t: t.get("date", ""))
    balance  = 10000.0
    by_date: dict[str, float] = {}
    for tx in history:
        d = tx.get("date", "")[:10]
        if not d:
            continue
        pnl = tx.get("pnl") or 0
        # Approximate: BUY reduces balance, SELL adds back cost + pnl
        if tx.get("type") == "BUY":
            balance -= (tx.get("price", 0) * tx.get("qty", 0))
        elif tx.get("type") == "SELL":
            balance += (tx.get("price", 0) * tx.get("qty", 0))
        by_date[d] = round(balance, 2)

    return [{"date": d, "balance": v} for d, v in sorted(by_date.items())]


# ── Log tailing ───────────────────────────────────────────────────────────────

def read_log_tail(n_lines: int = 100) -> list[str]:
    """Return last n_lines from autonomous_run.log."""
    if not _LOG.exists():
        return ["[Log file not found]"]
    try:
        with open(_LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n_lines:]]
    except Exception as e:
        return [f"[Error reading log: {e}]"]


# ── System health ─────────────────────────────────────────────────────────────

def get_system_health() -> dict:
    """Check file freshness and last pipeline status."""
    now = datetime.now()
    today = date.today()

    # Workbook freshness
    data_fresh   = False
    last_refresh = None
    if _XLSX.exists():
        mtime = datetime.fromtimestamp(_XLSX.stat().st_mtime)
        data_fresh   = mtime.date() >= today
        last_refresh = mtime.isoformat(timespec="minutes")

    # Last pipeline run time (parse from log)
    last_pipeline_run  = None
    pipeline_status    = "UNKNOWN"
    if _LOG.exists():
        try:
            with open(_LOG, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in reversed(lines):
                if "Starting Daily Trading Pipeline" in line:
                    # [2026-07-01 11:06:53] Starting ...
                    ts = line.strip()[1:20]
                    last_pipeline_run = ts
                    break
            for line in reversed(lines):
                if "Pipeline completed successfully" in line:
                    pipeline_status = "OK"
                    break
                if "Pipeline failed" in line or "ALERT" in line:
                    pipeline_status = "ERROR"
                    break
        except Exception:
            pass

    # Watchdog — check if watchdog.log or similar file was updated today
    watchdog_ok = True  # default optimistic; future: check watchdog log

    return {
        "data_fresh":        data_fresh,
        "last_refresh":      last_refresh,
        "last_pipeline_run": last_pipeline_run,
        "pipeline_status":   pipeline_status,
        "watchdog_ok":       watchdog_ok,
        "server_time":       now.isoformat(timespec="seconds"),
    }


# ── Scheduled tasks ───────────────────────────────────────────────────────────

_KNOWN_TASKS = [
    "AnalyzeFinData_Morning",
    "AnalyzeFinData_AI_Game",
    "AnalyzeFinData_AI_Summary",
    "AnalyzeFinData_Evening",
    "Project_AETHER_Watchdog",
]


def read_scorecard(horizon_days: int = 10) -> dict:
    """Backtracked selector scorecard (rules vs each AI provider) + winner-selling
    misses, from Data/decision_log.jsonl. Cached; empty when no log yet."""
    def _load():
        import decision_eval
        entries = decision_eval.read_log()
        if not entries:
            return {"selectors": {}, "winner_selling_misses": [], "logged": 0}
        sc = decision_eval.score_log(entries, horizon_days=horizon_days)
        sc["logged"] = len(entries)
        return sc
    return _cached(f"scorecard:{horizon_days}", 300.0, _load)


def read_scheduled_tasks() -> list[dict]:
    """Query Windows Task Scheduler for known AETHER tasks."""
    results = []
    try:
        out = subprocess.check_output(
            ["schtasks", "/query", "/fo", "CSV", "/v"],
            encoding="utf-8", errors="replace",
            timeout=10, stderr=subprocess.DEVNULL,
        )
        lines = out.splitlines()
        if not lines:
            return []
        header = [h.strip('"') for h in lines[0].split('","')]

        def col(row_parts, name):
            try:
                return row_parts[header.index(name)].strip('"')
            except (ValueError, IndexError):
                return ""

        for line in lines[1:]:
            parts = line.split('","')
            task_name = col(parts, "TaskName").lstrip("\\")
            if task_name not in _KNOWN_TASKS:
                continue
            results.append({
                "name":     task_name,
                "status":   col(parts, "Status"),
                "last_run": col(parts, "Last Run Time"),
                "next_run": col(parts, "Next Run Time"),
                "last_result": col(parts, "Last Result"),
            })
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # Not on Windows or schtasks unavailable — return stubs
        results = [{"name": t, "status": "N/A", "last_run": "", "next_run": "", "last_result": ""}
                   for t in _KNOWN_TASKS]
    return results
