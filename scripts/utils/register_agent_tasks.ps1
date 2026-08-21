<#
.SYNOPSIS
    Registers and configures Project AETHER's cognitive agent skills in Windows Task Scheduler.
.DESCRIPTION
    This script is CLI-agnostic. It sets up the 5 core automated workflows
    under a unified "AETHER_Agents" task folder. It dynamically builds commands,
    handles E*TRADE token alignment, and configures stdout/stderr redirection.
.PARAMETER Engine
    The CLI engine executable to run (e.g., "gemini", "claude", "antigravity"). Default is "gemini".
.PARAMETER Args
    Headless/automation arguments to pass to the engine. Default is "--approval-mode yolo --skip-trust".
.PARAMETER Force
    Overwrites existing scheduled tasks if they exist.
.EXAMPLE
    .\register_agent_tasks.ps1 -Engine "gemini" -Args "--approval-mode yolo --skip-trust"
#>
[CmdletBinding()]
param (
    [string]$Engine = "gemini",
    [string]$Args = "--approval-mode yolo --skip-trust",
    [switch]$Force = $true
)

# 1. Dynamically discover Repo Root (Script sits in scripts/utils)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Get-Item $ScriptDir).Parent.Parent.FullName
$LogDir = Join-Path $RepoRoot "Data\logs\agent_runs"

# Ensure log directory exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Host "Created log directory at $LogDir" -ForegroundColor Green
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   AETHER Headless Agent Scheduling Registration script"   -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Repo Root:  $RepoRoot"
Write-Host "CLI Engine: $Engine"
Write-Host "CLI Args:   $Args"
Write-Host "Log Dir:    $LogDir"
Write-Host "----------------------------------------------------------"

# Task Mapping array holding times, prompts, names, and logs
$Tasks = @(
    @{
        Name     = "AETHER_Watchdog"
        # 24/7 Hourly Trigger Design:
        # Starts in the past (1/1/2000) to ensure Task Scheduler activates the repetition
        # loop IMMEDIATELY upon registration, repeating every 1 hour indefinitely (9999 days).
        Triggers  = @(
            $(
                $T = New-ScheduledTaskTrigger -Once -At "1/1/2000 12:00 AM" -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 9999)
                $T
            )
        )
        Prompt   = "Execute the automated skill defined in .claude/commands/watchdog.md"
        Log      = "watchdog_agent.log"
        Desc     = "Hourly diagnostics and self-healing loop running 24/7."
    },
    @{
        Name     = "AETHER_StopMonitor"
        # Daily at 6:45 AM, repeat every 30 minutes for 7 hours (covers the entire market session)
        Triggers  = @(
            $(
                $T = New-ScheduledTaskTrigger -Daily -At "6:45 AM"
                $T.Repetition = (New-ScheduledTaskTrigger -Once -At "6:45 AM" -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 7)).Repetition
                $T
            )
        )
        Prompt   = "Execute the automated skill defined in .claude/commands/intraday-monitor.md"
        Log      = "intraday_monitor_agent.log"
        Desc     = "Real-time risk monitor checking open positions against stop levels every 30 mins."
    },
    @{
        Name     = "AETHER_DailyDriver"
        Triggers  = @(
            (New-ScheduledTaskTrigger -Daily -At "7:00 AM")
        )
        Prompt   = "Execute the automated skill defined in .claude/commands/daily-run.md"
        Log      = "daily_driver_agent.log"
        Desc     = "Core screener, rebalancing, and buy/sell execution pipeline at 7:00 AM PST."
    },
    @{
        Name     = "AETHER_PostMarketReporter"
        Triggers  = @(
            (New-ScheduledTaskTrigger -Daily -At "2:00 PM")
        )
        Prompt   = "Execute the automated skill defined in .claude/commands/status.md"
        Log      = "post_market_reporter_agent.log"
        Desc     = "Generates post-market closing equity, scheduler checks, and error scanning at 2:00 PM PST."
    },
    @{
        Name     = "AETHER_PostMarketSync"
        Triggers  = @(
            (New-ScheduledTaskTrigger -Daily -At "1:30 PM")
        )
        Prompt   = "Execute the automated skill defined in .claude/commands/post-market-sync.md"
        Log      = "post_market_sync_agent.log"
        Desc     = "Nightly post-market data sync to refresh Chaikin ratings and backfill price caches at 1:30 PM PST."
    },
    @{
        Name     = "AETHER_RD_Scientist"
        # Saturdays at 10:00 AM
        Triggers  = @(
            (New-ScheduledTaskTrigger -Weekly -At "10:00 AM" -DaysOfWeek Saturday)
        )
        Prompt   = "Execute the automated R&D skills defined in .claude/commands/pattern-discover.md and .claude/commands/failure-dna.md"
        Log      = "pattern_discovery_agent.log"
        Desc     = "Weekly self-improving R&D loop on Saturdays at 10:00 AM PST."
    },
    @{
        Name     = "AETHER_PreFlight_Audit"
        Triggers  = @(
            (New-ScheduledTaskTrigger -Daily -At "9:30 PM")
        )
        Script   = "venv_new\Scripts\python.exe scripts/diagnostics/preflight_validator.py --email"
        Log      = "preflight_audit.log"
        Desc     = "Nightly pre-flight system diagnostics and connection check at 9:30 PM PST."
    }
)

# Settings: standard reliable settings (wake machine, allow demand run, run missed)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Iterate and register each task
foreach ($T in $Tasks) {
    $TaskName = $T.Name
    $PromptPayload = $T.Prompt
    $LogFile = Join-Path $LogDir $T.Log
    
    # Build the command: cd to repo root, then run either a raw script or the engine+prompt.
    if ($T.Script) {
        $ExecCmd = "cd '$RepoRoot'; $($T.Script) >> '$LogFile' 2>&1"
    } else {
        $ExecCmd = "cd '$RepoRoot'; $Engine $Args -p '$PromptPayload' >> '$LogFile' 2>&1"
    }

    # Run in a hidden PowerShell window (no visible console) so the scheduled task is non-interactive.
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"$ExecCmd`""
    
    Write-Host "Registering task: $TaskName..." -ForegroundColor Yellow
    Write-Host "  Trigger:     $($T.Desc)"
    Write-Host "  Destination: $LogFile"
    
    try {
        # Check if already exists to support clean Force override
        if ($Force) {
            $Existing = Get-ScheduledTask -TaskName $TaskName -TaskPath "\AETHER_Agents\" -ErrorAction SilentlyContinue
            if ($Existing) {
                Unregister-ScheduledTask -TaskName $TaskName -TaskPath "\AETHER_Agents\" -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
                Write-Host "  Removed existing $TaskName task." -ForegroundColor DarkGray
            }
        }
        
        # Register the task cleanly under the path \AETHER_Agents\
        Register-ScheduledTask -TaskName $TaskName -TaskPath "\AETHER_Agents\" -Action $Action -Trigger $T.Triggers -Settings $Settings -Description $T.Desc | Out-Null
        Write-Host "  [SUCCESS] registered $TaskName successfully." -ForegroundColor Green
    }
    catch {
        Write-Error "  [FAILED] to register task $TaskName. Reason: $_"
    }
}

Write-Host "----------------------------------------------------------"
Write-Host "All agent tasks have been scheduled under folder '\AETHER_Agents\'." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
