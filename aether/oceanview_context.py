"""OceanView context assembler — the one Context Pack any agent boots warm from.

build_oceanview_context() returns a manifest of five parts (plan: "BUILD SPEC — the context
assembler"): `meta` (source / staleness / health / warnings), `knowledge` (pointers to the
durable brain — memory index, roadmap, mandates — never copies), `state` (broker accounts
and positions, the paper-game summary, study gates, data health), `capabilities` (the
recommend / verify / detect / report modules, all recommend-only) and `guardrails`.

Fail-safe contract — there is no silent success path:
  - The broker read is ban-safe or nothing: etrade.keep_alive() renews a same-day token over
    plain HTTP and returns None otherwise. It NEVER opens a browser. (etrade.get_tokens() is
    deliberately NOT used: when silent renewal fails it falls through to a headless
    Playwright login.)
  - Live success  -> source "live", broker snapshot cached to Data/oceanview_context.json.
  - Live failure  -> broker snapshot from that cache: age <= max_stale_hours -> "degraded",
                     older or no cache -> "failed". Local sections are always re-read fresh.
  - Data health   -> a held symbol with no OHLCV file, or whose recent OHLCV is mostly
                     Chaikin placeholder bars (bar_provenance.is_provisional), makes its ATR
                     stop unavailable / unreliable -> "degraded".
The account -> sleeve map (e.g. overlay-anchor, active-margin) comes from config.json
(`oceanview.sleeves`, keyed by account last-4): account digits never live in source.
`failed` means: do not advise on numbers. Recommend-only — nothing here can place an order.
"""
import datetime
import glob
import json
import os

from aether import etrade
from aether.config import CFG
from aether import paths
from aether.paths import data_dir as _default_data_dir
from bar_provenance import is_provisional

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_NAME = "oceanview_context.json"
# Verdict of the last SCHEDULED build (ok / degraded / failed). Written only when the caller
# passes record_status=True (daily_task does), so an interactive read (e.g. the adviser skill,
# live=False) can never overwrite the pipeline's verdict. The cache above is only written on a
# live success, so without this a failed build would leave no trace. Read by
# watchdog.check_context_pack_health. Holds only meta (no accounts, positions or sleeves).
STATUS_NAME = "oceanview_context_status.json"
RECENT_BARS = 30
PLACEHOLDER_SHARE_LIMIT = 0.20   # above this, a symbol's ATR stop is not trustworthy
_HEALTH_RANK = {"ok": 0, "degraded": 1, "failed": 2}

GUARDRAILS = [
    "recommend-only: the agent recommends, verifies and flags; a human executes. No order path.",
    "ban-safe: automation never opens a browser; broker reads go through etrade.keep_alive only.",
    "backup-before-write: core state files are cloned to Data/Backup/ before any write.",
    "backtest gate: |z| or |t| >= 1.96 and n >= 20 before a factor or rule is wired.",
    "capital preservation first: ATR stops; >= 50% cash when SPY L60 < -2.",
    "health 'failed' = do not advise on numbers; 'degraded' = every number carries its as-of stamp.",
]

# role -> [(name, repo-relative path)]; existence is resolved at build time.
_CAPABILITIES = {
    "recommend": [("oceanview-adviser skill", ".claude/commands/oceanview-adviser.md"),
                  ("analyze skill", ".claude/commands/analyze.md")],
    "verify":    [("sell_eval rubric", "aether/sell_eval.py"),
                  ("decision_eval scorecard", "aether/decision_eval.py")],
    "detect":    [("watchdog", "watchdog.py"),
                  ("circuit_breaker (systemic risk gate)", "aether/circuit_breaker.py")],
    "report":    [("real_copilot (real-account audit ticket)", "real_copilot.py")],
}


class LiveUnavailable(RuntimeError):
    """The ban-safe broker read could not run (no same-day token) or failed."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _main_checkout(repo: str = _REPO) -> str:
    """The main checkout's root, also from inside a git worktree (whose .git is a file
    reading 'gitdir: <main>/.git/worktrees/<name>')."""
    git = os.path.join(repo, ".git")
    if os.path.isfile(git):
        with open(git, encoding="utf-8") as f:
            line = f.read().strip()
        if line.startswith("gitdir:"):
            gitdir = os.path.normpath(line.split(":", 1)[1].strip())
            marker = os.sep + ".git" + os.sep
            if marker in gitdir:
                return gitdir.split(marker, 1)[0]
    return repo


def _memory_index() -> str:
    """Claude Code's per-project memory index: ~/.claude/projects/<slug>/memory/MEMORY.md
    (slug = the main checkout's path with every non-alphanumeric char replaced by '-')."""
    slug = "".join(ch if ch.isalnum() else "-" for ch in _main_checkout())
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug, "memory", "MEMORY.md")


def _pointer(path: str) -> dict:
    return {"path": path, "exists": os.path.exists(path)}


def _knowledge() -> dict:
    return {
        "memory_index": _pointer(_memory_index()),
        "roadmap": _pointer(os.path.join(_REPO, "plans", "roadmap.md")),
        "mandates": [_pointer(os.path.join(_REPO, n)) for n in ("CLAUDE.md", "AGENT.md")],
    }


def _capabilities() -> dict:
    return {role: [{"name": n, **_pointer(os.path.join(_REPO, p)), "recommend_only": True}
                   for n, p in items]
            for role, items in _CAPABILITIES.items()}


# ── Broker (live, ban-safe) ─────────────────────────────────────────────────────

def _cash(comp: dict) -> float:
    """netCash when the broker reports it (0 is a real value), else cashBalance."""
    raw = comp.get("netCash")
    if raw in (None, ""):
        raw = comp.get("cashBalance")
    return float(raw or 0)


def read_broker(env: str = "production") -> dict:
    """Accounts + positions via the renew-only token path. Raises LiveUnavailable."""
    tokens = etrade.keep_alive(env)
    if not tokens:
        raise LiveUnavailable("no valid same-day E*TRADE token (keep_alive returned None) — "
                              "a human must re-auth; automation never opens a browser")
    try:
        api = etrade.get_accounts(tokens, env)
        raw = api.list_accounts(resp_format="json")
        accts = raw.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])
        if isinstance(accts, dict):
            accts = [accts]
        accounts = []
        for a in accts:
            key, acct_id = a.get("accountIdKey", ""), a.get("accountId", "")
            if not key:
                continue
            comp = (api.get_account_balance(key, resp_format="json")
                    .get("BalanceResponse", {}).get("Computed", {}))
            last4 = acct_id[-4:]
            accounts.append({
                "account_last4": last4,
                "desc": a.get("accountDesc", ""),
                "sleeve": CFG.oceanview_sleeves.get(last4),
                "net_value": float(comp.get("RealTimeValues", {}).get("totalAccountValue", 0) or 0),
                "cash": _cash(comp),
            })
        positions = [{**p, "date_acquired": p["date_acquired"].isoformat() if p.get("date_acquired") else None}
                     for p in etrade.fetch_positions(tokens, env)]
    except Exception as e:  # any broker/API failure degrades to the cache; never raises past here
        raise LiveUnavailable(f"E*TRADE read failed: {type(e).__name__}: {e}") from e
    return {"accounts": accounts, "positions": positions}


# ── Local state (always re-read fresh) ──────────────────────────────────────────

def _portfolio_summary(ddir: str) -> dict | None:
    path = os.path.join(ddir, "ai_portfolio_game.json")
    try:
        with open(path, encoding="utf-8") as f:
            g = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    hist = g.get("history") or []
    sells = [h for h in hist if h.get("type") == "SELL"]
    return {
        "cash": g.get("balance"), "equity": g.get("equity"), "profile": g.get("profile"),
        "start_date": g.get("start_date"), "positions": sorted((g.get("positions") or {}).keys()),
        "transactions": len(hist), "closed_sells": len(sells),
        "realized_pnl": round(sum(h.get("pnl") or 0 for h in sells), 2),
        "last_transaction": max((h.get("date", "") for h in hist), default=None),
    }


def _study_gates(ddir: str) -> dict:
    """What each Data/*_study.json states about itself — never an inferred verdict."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ddir, "*_study.json"))):
        name = os.path.basename(path)[:-len("_study.json")]
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            out[name] = {"error": f"{type(e).__name__}"}
            continue
        if not isinstance(d, dict):
            out[name] = {"verdict": None}
            continue
        entry = {"as_of": d.get("as_of")}
        if "pass" in d or "verdict" in d:
            entry["verdict"] = d.get("verdict", d.get("pass"))
        elif isinstance(d.get("gates"), dict):
            entry["gates"] = {k: v.get("pass") for k, v in d["gates"].items() if isinstance(v, dict)}
        else:
            entry["verdict"] = None   # no machine-readable verdict in the file
        out[name] = entry
    return out


def placeholder_share(ohlcv_root: str, symbol: str) -> float | None:
    """Share of the last RECENT_BARS OHLCV bars that are Chaikin placeholders."""
    path = os.path.join(ohlcv_root, f"{symbol}_daily.json")
    try:
        with open(path, encoding="utf-8") as f:
            ts = json.load(f).get("Time Series (Daily)") or {}
    except (OSError, json.JSONDecodeError):
        return None
    recent = sorted(ts)[-RECENT_BARS:]
    return sum(is_provisional(ts[d]) for d in recent) / len(recent) if recent else None


def _data_health(ohlcv_root: str, symbols) -> dict:
    shares = {s: placeholder_share(ohlcv_root, s) for s in sorted(set(symbols))}
    bad = {s: round(v, 2) for s, v in shares.items() if v is not None and v > PLACEHOLDER_SHARE_LIMIT}
    missing = [s for s, v in shares.items() if v is None]
    return {"checked": len(shares), "placeholder_heavy": bad, "no_ohlcv": missing,
            "limit": PLACEHOLDER_SHARE_LIMIT}


# ── Cache ───────────────────────────────────────────────────────────────────────

def _load_cache(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, path)


# ── Entry point ─────────────────────────────────────────────────────────────────

def build_oceanview_context(live: bool = True, *, max_stale_hours: float = 24,
                            env: str = "production", data_dir: str | None = None,
                            now: datetime.datetime | None = None,
                            record_status: bool = False) -> dict:
    """Assemble the Context Pack. Always returns a health verdict (ok / degraded / failed).

    data_dir relocates the pack's own files (cache, game JSON, studies, OHLCV) to one folder.
    Without it: pack files under paths.data_dir() ($AETHER_DATA_DIR), OHLCV under
    paths.ohlcv_dir() ($AETHER_CACHE_DIR). The broker token is always resolved by
    aether.etrade via paths.data_dir(), so from a worktree set the env vars rather than
    passing data_dir — and never link a worktree's Data/ into the real one (#163).
    """
    ddir = data_dir or _default_data_dir()
    now = now or _now()
    cache_path = os.path.join(ddir, CACHE_NAME)
    warnings, health = [], "ok"
    broker, broker_as_of, source = None, None, "live"

    if live:
        try:
            broker = read_broker(env)
            broker_as_of = now.isoformat(timespec="seconds")
            _write_cache(cache_path, {"broker_as_of": broker_as_of, "broker": broker})
        except LiveUnavailable as e:
            warnings.append(f"live broker read unavailable: {e}")
    if broker is None:
        source = "cache"
        cached = _load_cache(cache_path)
        if cached and cached.get("broker_as_of"):
            broker, broker_as_of = cached.get("broker"), cached["broker_as_of"]
    staleness = None
    if broker_as_of:
        staleness = round((now - datetime.datetime.fromisoformat(broker_as_of)).total_seconds() / 3600, 2)
    if source == "cache":
        if broker is None:
            health = "failed"
            warnings.append("no cached broker snapshot — do not advise on account numbers")
        elif staleness is not None and staleness > max_stale_hours:
            health = "failed"
            warnings.append(f"broker snapshot is {staleness}h old (> {max_stale_hours}h) — do not act on it")
        else:
            health = "degraded"
            warnings.append(f"broker numbers are from cache, as of {broker_as_of} ({staleness}h old)")

    portfolio = _portfolio_summary(ddir)
    if portfolio is None:
        warnings.append("ai_portfolio_game.json unreadable — paper-game summary missing")
    held = [p.get("symbol") for p in (broker or {}).get("positions", []) if p.get("symbol")]
    held += (portfolio or {}).get("positions", [])
    # OHLCV lives under the cache root (#163: $AETHER_CACHE_DIR, else <checkout>/Data); an
    # explicit data_dir keeps everything, OHLCV included, under that one folder.
    ohlcv_root = os.path.join(data_dir, "Symbol_full") if data_dir else paths.ohlcv_dir()
    data_health = _data_health(ohlcv_root, held)
    if data_health["placeholder_heavy"]:
        warnings.append("ATR stops unreliable for " + ", ".join(
            f"{s} ({v:.0%} placeholder bars)" for s, v in data_health["placeholder_heavy"].items()))
    if data_health["no_ohlcv"]:
        # Worse than placeholder-heavy: with no file the stop resolver falls back to 8% off
        # price for every such position (the state a cache wipe leaves behind).
        warnings.append("ATR stops unavailable (no OHLCV file) for " + ", ".join(data_health["no_ohlcv"]))
    stops_affected = data_health["placeholder_heavy"] or data_health["no_ohlcv"]
    if stops_affected and _HEALTH_RANK[health] < _HEALTH_RANK["degraded"]:
        health = "degraded"

    meta = {"generated_at": now.isoformat(timespec="seconds"), "source": source,
            "broker_as_of": broker_as_of, "staleness_hours": staleness,
            "health": health, "warnings": warnings}
    if record_status:
        try:
            _write_cache(os.path.join(ddir, STATUS_NAME), meta)
        except OSError as e:
            warnings.append(f"could not write {STATUS_NAME}: {e}")
    return {
        "meta": meta,
        "knowledge": _knowledge(),
        "state": {
            "accounts": (broker or {}).get("accounts"),
            "positions": (broker or {}).get("positions"),
            "sleeves": CFG.oceanview_sleeves,
            "portfolio": portfolio,
            "study_gates": _study_gates(ddir),
            "data_health": data_health,
        },
        "capabilities": _capabilities(),
        "guardrails": GUARDRAILS,
    }
