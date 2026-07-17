"""
AETHER Web Dashboard — FastAPI server.

Usage:
    python server.py            # foreground (dev mode, auto-reload)
    python server.py start      # daemonize (background process)
    python server.py stop       # stop the background process
    python server.py restart    # stop + start
    python server.py status     # show running/stopped + port
    python server.py upgrade    # git pull + restart

Dashboard: http://localhost:8888  (port configurable via config.json web.port)
API docs:  http://localhost:8888/docs
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

_DIR     = Path(__file__).resolve().parent
_PID     = _DIR / "Data" / "webserver.pid"
_LOG     = _DIR / "Data" / "webserver.log"
_PYTHON  = sys.executable

_TOKEN_TTL = 12 * 3600   # session token lifetime (seconds)

# ── Config (lazy import to avoid import-time side effects in CLI mode) ────────

def _cfg():
    from config import CFG
    return CFG


# ── Session token (HMAC-signed, JWT-like; no external deps) ────────────────────

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user: str, secret: str, ttl: int = _TOKEN_TTL) -> str:
    body = _b64(json.dumps({"u": user, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str, secret: str) -> str | None:
    """Return the username if the token is valid and unexpired, else None."""
    if not token or not secret:
        return None
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("u")
    except Exception:
        return None


def check_credentials(username: str, password: str, admins: list) -> bool:
    """Constant-time-ish credential check against the configured admin list."""
    ok = False
    for a in admins:
        u_match = hmac.compare_digest(str(a.get("user", "")), username)
        p_match = hmac.compare_digest(str(a.get("pass", "")), password)
        if u_match and p_match:
            ok = True
    return ok


# ── FastAPI application ───────────────────────────────────────────────────────

def create_app():
    import asyncio
    from fastapi import Body, FastAPI, Header, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    import data_api
    from aether_logger import get_logger
    _log = get_logger("server")

    # Signing secret: configured value survives restarts; otherwise ephemeral.
    _secret = _cfg().web_secret or secrets.token_hex(32)
    if not _cfg().web_secret:
        print("[AETHER] No web.secret configured -- using an ephemeral signing "
              "secret (admin sessions won't survive a restart).")

    app = FastAPI(title="AETHER Dashboard", version="1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8888", "http://127.0.0.1:8888",
                        "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Static files ──────────────────────────────────────────────────────────

    @app.middleware("http")
    async def _revalidate_dashboard_assets(request, call_next):
        """Force the browser to revalidate the dashboard HTML/JS/CSS on every load
        (ETag/Last-Modified still yield 304s when unchanged). Without this, an edited
        app.js can be served from cache after a restart, leaving a tab stuck on
        'Loading…' because the old code lacks the new loader."""
        resp = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    web_dir = _DIR / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        index = web_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"status": "AETHER API running", "docs": "/docs"})

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        return data_api.get_system_health()

    # ── Portfolio ─────────────────────────────────────────────────────────────

    @app.get("/api/portfolio")
    async def portfolio():
        return data_api.read_portfolio()

    # ── Live prices ───────────────────────────────────────────────────────────

    _price_cache: dict = {}
    _price_ts: float   = 0.0

    @app.get("/api/prices")
    async def prices(symbols: str = Query(..., description="Comma-separated tickers")):
        nonlocal _price_ts
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        now = time.monotonic()
        # Serve from cache if <60s old
        cached_all = all(s in _price_cache for s in sym_list)
        if cached_all and now - _price_ts < 60:
            return {s: _price_cache[s] for s in sym_list}
        # Fetch live (blocking call — run in thread to avoid blocking event loop)
        loop = asyncio.get_running_loop()
        try:
            from ai_portfolio_game import get_live_prices
            fresh = await loop.run_in_executor(None, get_live_prices, sym_list)
            _price_cache.update(fresh)
            _price_ts = now
        except Exception:
            fresh = {}
        return {s: _price_cache.get(s, 0) for s in sym_list}

    # ── Wiki Config Hook ──────────────────────────────────────────────────────

    @app.get("/api/wiki/config")
    async def wiki_config():
        import ai_portfolio_game
        return {
            "DEFENSIVE": ai_portfolio_game.get_strategy_rules("DEFENSIVE"),
            "BALANCED": ai_portfolio_game.get_strategy_rules("BALANCED"),
            "AGGRESSIVE": ai_portfolio_game.get_strategy_rules("AGGRESSIVE")
        }

    # ── Picks ─────────────────────────────────────────────────────────────────

    @app.get("/api/picks")
    async def picks():
        return data_api.read_picks()

    # ── Replacements ──────────────────────────────────────────────────────────

    @app.get("/api/replacements")
    async def replacements():
        return data_api.read_replacements()

    # ── A-Reserves ────────────────────────────────────────────────────────────

    @app.get("/api/reserves")
    async def reserves():
        return data_api.read_reserves()

    # ── Accounts (2 real + 1 game) ──────────────────────────────────────────────

    @app.get("/api/accounts")
    async def accounts():
        return data_api.read_accounts()

    # ── History ───────────────────────────────────────────────────────────────

    @app.get("/api/history")
    async def history(
        limit:  int = Query(50,  ge=1, le=500),
        offset: int = Query(0,   ge=0),
    ):
        return data_api.read_history(limit=limit, offset=offset)

    @app.get("/api/history/equity-curve")
    async def equity_curve():
        return data_api.read_equity_curve()

    # ── Pipeline log ──────────────────────────────────────────────────────────

    @app.get("/api/pipeline/logs")
    async def pipeline_logs(lines: int = Query(100, ge=1, le=1000)):
        return {"lines": data_api.read_log_tail(lines)}

    # ── Research sheet (full screener output) ─────────────────────────────────

    @app.get("/api/research")
    async def research():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, data_api.read_research)

    # ── Symbol detail (all-in-one for the symbol modal) ──────────────────────

    @app.get("/api/symbol/{symbol}")
    async def symbol_detail(symbol: str):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, data_api.read_symbol, symbol)

    # ── Level backtest (support/resistance accuracy) ──────────────────────────

    @app.get("/api/backtest")
    async def backtest(symbol: str = Query(..., min_length=1, max_length=8),
                       horizon: int = Query(20, ge=5, le=60)):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, data_api.read_backtest, symbol, horizon)

    # ── AI Chat ───────────────────────────────────────────────────────────────

    @app.post("/api/chat")
    async def chat_endpoint(
        messages: list = Body(...),
        authorization: str = Header(default=""),
    ):
        """Multi-turn chat with the configured AI provider, augmented with live
        AETHER context (portfolio state, regime, top picks, OHLCV freshness).
        messages: [{role: user|assistant, content: str}]"""
        import ai_client
        if not ai_client.primary():
            return {"reply": "No AI provider configured. Enable one in config.json under ai.providers.", "provider": None}

        # Sanitise incoming history: allow only known roles with string content.
        # Strips role=system injections that could override our system prompt.
        VALID_ROLES = {"user", "assistant"}
        messages = [m for m in messages
                    if isinstance(m, dict) and m.get("role") in VALID_ROLES
                    and isinstance(m.get("content"), str)][:50]  # cap history length

        import re as _re

        # Detect uppercase ticker candidates in the last user message BEFORE
        # building context — this gates the 9-second read_research() cold load.
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        raw_candidates = _re.findall(r'\b[A-Z]{2,5}\b', last_user)

        def _build_context():
            """Build the system prompt. Runs in a thread via run_in_executor so
            it does not block the event loop while loading OHLCV data."""
            try:
                pf = data_api.read_portfolio()
                health = data_api.get_system_health()
                picks_d = data_api.read_picks()
                positions_str = ", ".join(
                    f"{p['symbol']} (status: held)"
                    for p in pf.get("positions", [])
                ) or "none"
                top_picks = ", ".join(
                    p.get("Symbol", "") for p in picks_d.get("picks", [])[:5]
                ) or "none"

                symbol_context = ""
                if raw_candidates:
                    try:
                        research_rows = {r["symbol"]: r
                                         for r in data_api.read_research().get("rows", [])}
                        mentioned = [w for w in raw_candidates if w in research_rows][:3]
                        if mentioned:
                            lines = [
                                f"  {sym}: PGR={r.get('pgr','?')} S10={r.get('s10','?')} "
                                f"L60={r.get('l60','?')} Combined={r.get('combined','?')} "
                                f"Status={r.get('status','?')} Setup={'YES' if r.get('setup') else 'no'} "
                                f"Price=${r.get('price','?')} Stop=${r.get('stop','?')}({r.get('stop_source','?')}) "
                                f"Target=${r.get('target','?')} WinPct={r.get('win_pct','?')}% "
                                f"MoneyFlow={r.get('money_flow','?')} Patterns={r.get('patterns','?') or 'none'}"
                                for sym in mentioned
                                for r in [research_rows[sym]]
                            ]
                            symbol_context = "\nLive AETHER data for mentioned symbols:\n" + "\n".join(lines)
                    except Exception as _sym_err:
                        _log.warning("Symbol context build failed", extra={"error": str(_sym_err)})

                return (
                    "You are AETHER, an expert quantitative trading assistant embedded in a live portfolio dashboard.\n"
                    "IMPORTANT: All the data you need is provided below in this system prompt. "
                    "Never say you cannot access real-time data — you already have it. "
                    "Answer from the data provided; do not suggest the user check external sites.\n\n"
                    f"Portfolio state:\n"
                    f"  Profile: {pf.get('profile','?')} | Equity: ${pf.get('equity',0):,.2f} "
                    f"| Cash: ${pf.get('balance',0):,.2f} | Return: {pf.get('return_pct',0):+.2f}%\n"
                    f"  Open positions: {positions_str}\n"
                    f"  Market regime: {health.get('market_regime', picks_d.get('market_regime','?'))}\n"
                    f"  Top screener picks today: {top_picks}\n"
                    f"  Data fresh: {'yes' if health.get('data_fresh') else 'NO — stale'}"
                    f"{symbol_context}\n\n"
                    "Rules: hard ATR stop > soft momentum signal > winner-protection "
                    "(Flower Protection — never sell a winner above its 50-DMA on a momentum dip). "
                    "Capital preservation is the absolute priority. Answer concisely."
                )
            except Exception as _ctx_err:
                _log.error("Chat context build failed", extra={"error": str(_ctx_err)}, exc_info=True)
                return "You are AETHER, a quantitative trading assistant. Live portfolio data is currently unavailable."

        # Build context in executor (off the event loop), then call AI with
        # the full system prompt. read_research() is cached 60s so only the
        # first ticker-bearing message per minute pays the OHLCV load cost.
        loop = asyncio.get_running_loop()
        system = await loop.run_in_executor(None, _build_context)
        reply = await loop.run_in_executor(
            None, lambda: ai_client.chat(messages, system=system, max_tokens=800)
        )
        if not reply:
            _log.warning("ai_client.chat() returned empty reply",
                         extra={"provider": ai_client.primary(), "msg_count": len(messages)})
            return {"reply": "AI provider returned no response. Check your API key and provider config.", "provider": ai_client.primary()}
        return {"reply": reply, "provider": ai_client.primary()}

    # ── Selector scorecard (backtracked) ──────────────────────────────────────

    @app.get("/api/scorecard")
    async def scorecard(horizon: int = Query(10, ge=1, le=60)):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, data_api.read_scorecard, horizon)

    # ── Run pipeline (protected) ──────────────────────────────────────────────

    _pipeline_proc: list = []   # mutable container for the running subprocess

    def _require_admin(authorization: str) -> str:
        """Fail-closed admin gate. Requires a valid Bearer session token.
        Returns the admin username, or raises 401."""
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        user = verify_token(token, _secret)
        if not user:
            raise HTTPException(status_code=401, detail="Admin authentication required")
        return user

    # ── Auth ──────────────────────────────────────────────────────────────────

    @app.post("/api/login")
    async def login(username: str = Body(...), password: str = Body(...)):
        cfg = _cfg()
        if not cfg.web_admins:
            raise HTTPException(status_code=403,
                                detail="Admin login disabled: configure web.admins in config.json.")
        if not check_credentials(username, password, cfg.web_admins):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        return {"token": make_token(username, _secret), "user": username, "expires_in": _TOKEN_TTL}

    @app.get("/api/whoami")
    async def whoami(authorization: str = Header(default="")):
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        user = verify_token(token, _secret)
        return {"authenticated": bool(user), "user": user}

    @app.post("/api/pipeline/run")
    async def run_pipeline(authorization: str = Header(default="")):
        _require_admin(authorization)
        lock = _DIR / "Data" / "pipeline.lock"
        if lock.exists():
            return {"status": "already_running", "message": "Pipeline is already running"}
        try:
            proc = subprocess.Popen(
                [_PYTHON, str(_DIR / "autonomous_pipeline.py")],
                cwd=str(_DIR),
            )
            _pipeline_proc.clear()
            _pipeline_proc.append(proc)
            return {"status": "started", "pid": proc.pid}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── Scheduled tasks ───────────────────────────────────────────────────────

    @app.get("/api/tasks")
    async def tasks():
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, data_api.read_scheduled_tasks)
        return {"tasks": result}

    @app.get("/api/tasks/manual")
    async def manual_tasks():
        return {"tasks": data_api.read_manual_tasks()}

    # In-memory run registry: run_id -> {log_path, pid, done}
    _runs: dict = {}

    @app.post("/api/tasks/run")
    async def run_task(
        task_id: str = Body(...),
        input_value: str = Body(default=""),
        authorization: str = Header(default=""),
    ):
        import uuid, tempfile, os as _os
        registry = {t["id"]: t for t in data_api.MANUAL_TASKS}
        task = registry.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Unknown task: {task_id}")
        if task.get("admin_only"):
            _require_admin(authorization)
        script = _DIR / task["script"]
        if not script.exists():
            raise HTTPException(status_code=404, detail=f"Script not found: {task['script']}")
        args = list(task.get("args", []))
        inp = task.get("input")
        if inp:
            val = (input_value.strip().upper() if input_value.strip()
                   else (inp.get("default") or "")).upper()
            if not val:
                return {"status": "error", "message": "Input value required."}
            args.insert(inp.get("arg_position", len(args)), val)
        run_id = str(uuid.uuid4())[:8]
        log_dir = _DIR / "Data" / "task_runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{run_id}.log"
        try:
            with open(log_path, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(
                    [_PYTHON, str(script)] + args,
                    stdout=lf, stderr=subprocess.STDOUT,
                    cwd=str(_DIR),
                )
            _runs[run_id] = {"log": log_path, "pid": proc.pid, "proc": proc}
            return {"status": "started", "pid": proc.pid, "run_id": run_id,
                    "task_id": task_id, "label": task["label"]}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/api/tasks/output/{run_id}")
    async def task_output(run_id: str, offset: int = Query(0, ge=0)):
        """Stream lines from a task's log file starting at byte `offset`.
        Returns {lines, offset, done} so the client can poll incrementally."""
        run = _runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        log_path = run["log"]
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                chunk = f.read(32768)   # max 32 KB per poll
                new_offset = f.tell()
        except FileNotFoundError:
            chunk, new_offset = "", offset
        proc = run["proc"]
        done = proc.poll() is not None
        return {"lines": chunk, "offset": new_offset, "done": done,
                "exit_code": proc.returncode if done else None}

    @app.post("/api/tasks/heal")
    async def heal_tasks(authorization: str = Header(default="")):
        _require_admin(authorization)
        loop = asyncio.get_running_loop()
        def _heal():
            import watchdog
            watchdog.heal_tasks([], force=True)
        await loop.run_in_executor(None, _heal)
        return {"status": "healed"}

    return app


# ── CLI lifecycle management ──────────────────────────────────────────────────

def _read_pid() -> int | None:
    try:
        return int(_PID.read_text().strip())
    except Exception:
        return None


def _is_running(pid: int) -> bool:
    """Cross-platform liveness check.

    On Windows, os.kill(pid, 0) TERMINATES the process (there is no signal-0
    probe — it falls through to TerminateProcess), so we must use OpenProcess.
    """
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def cmd_start():
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"Already running (pid {pid}).")
        return
    cfg = _cfg()
    port = cfg.web_port
    host = cfg.web_host
    log_f = open(_LOG, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [_PYTHON, __file__, "serve", "--port", str(port), "--host", host],
        cwd=str(_DIR),
        stdout=log_f, stderr=log_f,
        start_new_session=True,
    )
    _PID.write_text(str(proc.pid))
    time.sleep(1.5)
    if _is_running(proc.pid):
        print(f"Started AETHER Dashboard (pid {proc.pid}) -> http://{host}:{port}")
    else:
        print("Failed to start. Check Data/webserver.log.")


def cmd_stop():
    pid = _read_pid()
    if not pid:
        print("Not running (no PID file).")
        return
    if not _is_running(pid):
        _PID.unlink(missing_ok=True)
        print("Not running (stale PID removed).")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            if not _is_running(pid):
                break
        _PID.unlink(missing_ok=True)
        print(f"Stopped (pid {pid}).")
    except Exception as e:
        print(f"Error stopping: {e}")


def cmd_status():
    pid = _read_pid()
    cfg = _cfg()
    if pid and _is_running(pid):
        print(f"Running  pid={pid}  http://{cfg.web_host}:{cfg.web_port}")
    else:
        print(f"Stopped  (port {cfg.web_port})")


def cmd_restart():
    cmd_stop()
    time.sleep(0.5)
    cmd_start()


def cmd_upgrade():
    print("Pulling latest code...")
    subprocess.run(["git", "pull"], cwd=str(_DIR), check=True)
    cmd_restart()


def _write_commit_stamp():
    """Stamp the git HEAD at server start so health checks can detect stale processes."""
    try:
        import subprocess as _sp
        head = _sp.check_output(["git", "rev-parse", "--short", "HEAD"],
                                 cwd=str(_DIR), text=True, timeout=3).strip()
        (_DIR / "Data" / "server_commit.txt").write_text(head)
    except Exception:
        pass


def cmd_serve(host: str, port: int):
    """Run uvicorn in-process (called by cmd_start subprocess)."""
    import uvicorn
    _write_commit_stamp()
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_regen_secret():
    """Generate a fresh web.secret in config.json and restart if running.
    Invalidates all existing admin sessions (everyone must log in again)."""
    import datetime
    import json
    import shutil

    cfg_path = _DIR / "config.json"
    if not cfg_path.exists():
        print("config.json not found — create it from config.json.example first.")
        return

    # Backup before writing (Data/Backup is gitignored)
    (_DIR / "Data" / "Backup").mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = _DIR / "Data" / "Backup" / f"config_{ts}.json"
    shutil.copy2(cfg_path, bak)

    cfg = json.loads(cfg_path.read_text())
    web = cfg.get("web") or {}
    web["secret"] = secrets.token_hex(32)
    cfg["web"] = web
    tmp = str(cfg_path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, cfg_path)

    print(f"New web.secret written (64 chars). Backup: {bak}")
    print("All existing admin sessions are now invalid.")
    pid = _read_pid()
    if pid and _is_running(pid):
        print("Server is running -> restarting to apply...")
        cmd_restart()
    else:
        print("Start the server for it to take effect: python server.py start")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AETHER Dashboard server")
    parser.add_argument(
        "cmd", nargs="?", default="dev",
        choices=["start", "stop", "restart", "status", "upgrade",
                 "regen-secret", "serve", "dev"],
        help="start/stop/restart/status/upgrade the background server; "
             "regen-secret rotates web.secret in config.json; "
             "'serve' runs uvicorn in-process; omit for foreground dev mode",
    )
    parser.add_argument("--port", type=int, help="Override configured port (serve/dev)")
    parser.add_argument("--host", help="Override configured host (serve/dev)")
    args = parser.parse_args()

    if args.cmd == "start":
        cmd_start()
    elif args.cmd == "stop":
        cmd_stop()
    elif args.cmd == "restart":
        cmd_restart()
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "upgrade":
        cmd_upgrade()
    elif args.cmd == "regen-secret":
        cmd_regen_secret()
    elif args.cmd == "serve":
        cfg = _cfg()
        cmd_serve(args.host or cfg.web_host, args.port or cfg.web_port)
    else:
        # Default: foreground dev mode (auto-reload)
        cfg = _cfg()
        import uvicorn
        app = create_app()
        uvicorn.run(app, host=args.host or cfg.web_host,
                    port=args.port or cfg.web_port, log_level="info")
