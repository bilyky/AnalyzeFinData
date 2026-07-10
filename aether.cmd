@echo off
:: 🛡️ Project AETHER: Unified CLI Wrapper
:: Enforces local virtual environment and forwards all command-line arguments to server.py

cd /d "%~dp0"

:: If no arguments are passed, display a beautiful help manual
if "%~1"=="" goto help

:: Pick an interpreter: prefer the project venv (venv_new) if present, else fall
:: back to Python on PATH (avoids "system cannot find the path specified" when the
:: venv is absent).
set "PYEXE=python"
if exist "%~dp0venv_new\Scripts\python.exe" set "PYEXE=%~dp0venv_new\Scripts\python.exe"

:: Forward all arguments cleanly to the server daemon
"%PYEXE%" server.py %*
exit /b %errorlevel%

:help
echo =======================================================================
echo  🛡️  PROJECT AETHER: UNIFIED CLI DAEMON WRAPPER
echo =======================================================================
echo  Usage:
echo    aether start         - Starts the dashboard server in the background
echo    aether stop          - Stops the background dashboard server
echo    aether restart       - Restarts the background dashboard server
echo    aether status        - Displays active server PID and port status
echo    aether upgrade       - Pulls latest GitHub code and restarts server
echo    aether regen-secret  - Rotates HMAC session keys in config.json
echo.
echo  Additional Commands:
echo    aether serve         - Runs the server in-process (foreground)
echo    aether dev           - Runs the server in foreground auto-reload mode
echo =======================================================================
exit /b 0
