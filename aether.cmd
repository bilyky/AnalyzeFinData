@echo off
:: Project AETHER Launcher — Dynamically resolves PATH environment variables at runtime
:: to ensure background S4U task scheduler executions always find Node.js and Gemini CLI.

setlocal enabledelayedexpansion

:: 1. Dynamically find Node.js from standard system paths if not already in PATH
where node.exe >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\nodejs\node.exe" (
        set "PATH=!PATH!;C:\Program Files\nodejs"
    )
)

:: 2. Dynamically find global npm/gemini wrapper paths
where gemini.cmd >nul 2>&1
if errorlevel 1 (
    if exist "%APPDATA%\npm\gemini.cmd" (
        set "PATH=!PATH!;%APPDATA%\npm"
    )
)

:: 3. Execute the CLI command passed from Task Scheduler
cd /d "%~dp0"
if "%~1"=="" (
    echo [AETHER] No command arguments specified.
    exit /b 1
)

:: Execute the command natively in CMD
%*
