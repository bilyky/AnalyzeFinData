@echo off
:: claude (Claude Code) against dev GNAI API (fm7duybilyk001:8036)
::
:: Usage:
::   claude-dev.cmd [claude args...]
::
:: Examples:
::   claude-dev.cmd --print "say hi"
::   claude-dev.cmd  (interactive session)
::
:: First run sets up the SSH tunnel; subsequent runs reuse it.

setlocal

set DEV_HOST=ybilyk@fm7duybilyk001
set DEV_PORT=8036
set LOCAL_PORT=18036
set SSH_KEY=%USERPROFILE%\.ssh\id_rsa
:: /providers/anthropic prefix exposes the Anthropic-compatible endpoint
set ANTHROPIC_BASE_URL=http://localhost:%LOCAL_PORT%/providers/anthropic
:: base64("ybilyk:") — dev server decodes Bearer token as Basic auth
set ANTHROPIC_API_KEY=eWJpbHlrOg==
:: Bypass corporate proxy for localhost tunnel traffic
set NO_PROXY=localhost,127.0.0.1
set DEV_MODEL=claude-4-6-sonnet

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
