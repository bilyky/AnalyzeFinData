@echo off
:: Project AETHER agent launcher — heals the S4U scheduled-task PATH, then runs the requested engine.
:: Usage: run_agent.cmd <engine> [engine-args...]
:: Background S4U tasks start with a minimal PATH (missing the per-user npm / Node.js dirs), which
:: caused 0x80070002 (file not found). This restores the runtime + npm-global dirs GENERICALLY —
:: it is engine-agnostic (keyed on %1, no hardcoded CLI name), so any npm-installed engine resolves.
::
:: NOTE: delayed expansion is intentionally OFF so a "!" in the forwarded -p prompt is not eaten;
:: each PATH append reads %PATH% once (constant append, no intra-block re-read needed).

setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo [AETHER] Usage: run_agent.cmd ^<engine^> [args...]
    exit /b 1
)

:: 1. Node.js runtime — add the standard install dir only if node is not already resolvable.
where node.exe >nul 2>&1
if errorlevel 1 (
    if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%PATH%;%ProgramFiles%\nodejs"
)

:: 2. npm global bin dir — where globally-installed CLI wrappers live (gemini, claude, ...).
::    Added only if the requested engine (%~1) is not already on PATH. No engine literal.
where "%~1" >nul 2>&1
if errorlevel 1 (
    if exist "%APPDATA%\npm" set "PATH=%PATH%;%APPDATA%\npm"
)

:: 3. Run the engine command exactly as passed by Task Scheduler.
%*
exit /b %errorlevel%
