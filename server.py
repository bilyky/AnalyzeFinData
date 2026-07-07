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
import os
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

# ── Config (lazy import to avoid import-time side effects in CLI mode) ────────

def _cfg():
    from config import CFG
    return CFG


# ── FastAPI application ───────────────────────────────────────────────────────

def create_app():
    import asyncio
    from fastapi import FastAPI, Header, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    import data_api

    app = FastAPI(title="AETHER Dashboard", version="1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8888", "http://127.0.0.1:8888",
                        "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Static files ──────────────────────────────────────────────────────────

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
        loop = asyncio.get_event_loop()
        try:
            from ai_portfolio_game import get_live_prices
            fresh = await loop.run_in_executor(None, get_live_prices, sym_list)
            _price_cache.update(fresh)
            _price_ts = now
        except Exception:
            fresh = {}
        return {s: _price_cache.get(s, 0) for s in sym_list}

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

    # ── Run pipeline (protected) ──────────────────────────────────────────────

    _pipeline_proc: list = []   # mutable container for the running subprocess

    def _require_key(x_api_key: str):
        """Fail-closed auth for mutating endpoints. Rejects when no key is configured
        so a pipeline/heal can never be triggered by an unauthenticated caller."""
        cfg = _cfg()
        if not cfg.web_api_key:
            raise HTTPException(
                status_code=403,
                detail="Mutating endpoints are disabled: set web.api_key in config.json to enable.",
            )
        if x_api_key != cfg.web_api_key:
            raise HTTPException(status_code=403, detail="Invalid API key")

    @app.post("/api/pipeline/run")
    async def run_pipeline(x_api_key: str = Header(default="")):
        _require_key(x_api_key)
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
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, data_api.read_scheduled_tasks)
        return {"tasks": result}

    @app.post("/api/tasks/heal")
    async def heal_tasks(x_api_key: str = Header(default="")):
        _require_key(x_api_key)
        loop = asyncio.get_event_loop()
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


def cmd_serve(host: str, port: int):
    """Run uvicorn in-process (called by cmd_start subprocess)."""
    import uvicorn
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AETHER Dashboard server")
    parser.add_argument(
        "cmd", nargs="?", default="dev",
        choices=["start", "stop", "restart", "status", "upgrade", "serve", "dev"],
        help="start/stop/restart/status/upgrade the background server; "
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
