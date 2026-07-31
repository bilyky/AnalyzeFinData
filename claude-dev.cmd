@echo off
:: claude (Claude Code) against a dev GNAI API over an SSH tunnel.
::
:: Usage:
::   claude-dev.cmd [claude args...]
::
:: Examples:
::   claude-dev.cmd --print "say hi"
::   claude-dev.cmd  (interactive session)
::
:: First run sets up the SSH tunnel; subsequent runs reuse it.
::
:: Host / port / API key live in claude-dev.local.cmd, which is gitignored.
:: Copy claude-dev.local.cmd.example to claude-dev.local.cmd and fill in
:: DEV_HOST / DEV_PORT / ANTHROPIC_API_KEY before first use.

setlocal

:: Load local (untracked) secrets: DEV_HOST, DEV_PORT, ANTHROPIC_API_KEY.
if not exist "%~dp0claude-dev.local.cmd" (
    echo [claude-dev] ERROR: claude-dev.local.cmd not found.
    echo [claude-dev] Copy claude-dev.local.cmd.example to claude-dev.local.cmd and fill in your values.
    exit /b 1
)
call "%~dp0claude-dev.local.cmd"

set LOCAL_PORT=18036
set SSH_KEY=%USERPROFILE%\.ssh\id_rsa
:: /providers/anthropic prefix exposes the Anthropic-compatible endpoint
set ANTHROPIC_BASE_URL=http://localhost:%LOCAL_PORT%/providers/anthropic
:: An inherited ANTHROPIC_AUTH_TOKEN (e.g. when launched from another Claude Code
:: session) outranks ANTHROPIC_API_KEY and makes the relay answer 403; clear it.
set ANTHROPIC_AUTH_TOKEN=
:: Bypass corporate proxy for localhost tunnel traffic
set NO_PROXY=localhost,127.0.0.1
:: Spell the model the way Anthropic does (claude-opus-4-8), not the reversed
:: GNAI form (claude-4-8-opus) — config.yml accepts either as an alias, but the
:: CLI's per-model capability tables key off the real id. The reversed spelling
:: mis-detects support and sends betas the relay rejects (e.g. a 400 "role
:: 'system' is not supported on this model" plus a silent retry every turn).
set DEV_MODEL=claude-opus-4-8
:: WA: the dev GNAI relay rejects newer anthropic-beta values with HTTP 400
:: ("Unexpected value(s) `advisor-tool-2026-03-01` for the `anthropic-beta` header").
:: This CLI adds that beta whenever the advisor gate is on; disable it here.
:: Remove once the dev API relay is upgraded to accept the beta.
set CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1

:: Check if tunnel is already up
netstat -an 2>nul | findstr /r ":%LOCAL_PORT%.*LISTEN" >nul 2>&1
if %errorlevel% neq 0 (
    echo [claude-dev] Starting SSH tunnel ^(localhost:%LOCAL_PORT% -^> %DEV_HOST%:%DEV_PORT%^)...
    start "" /B ssh -i "%SSH_KEY%" -fNL %LOCAL_PORT%:localhost:%DEV_PORT% %DEV_HOST%
    timeout /t 3 /nobreak >nul
    netstat -an 2>nul | findstr /r ":%LOCAL_PORT%.*LISTEN" >nul 2>&1
    if %errorlevel% neq 0 (
        echo [claude-dev] ERROR: SSH tunnel failed to start. Check that %DEV_HOST% is reachable.
        exit /b 1
    )
    echo [claude-dev] Tunnel up.
) else (
    echo [claude-dev] Tunnel already running on port %LOCAL_PORT%.
)

:: Add --model default unless caller already specified one
set EXTRA_MODEL=--model %DEV_MODEL%
echo %* | findstr /i "\-\-model" >nul 2>&1
if %errorlevel% equ 0 set EXTRA_MODEL=

echo [claude-dev] Connecting via %ANTHROPIC_BASE_URL% (model: %DEV_MODEL%)...
"%USERPROFILE%\.gnai\claude\claude.exe" --mcp-config "%USERPROFILE%\.gnai\claude\mcp.json" %EXTRA_MODEL% %*

endlocal
