<#
.SYNOPSIS
    Registers Project AETHER's scheduled tasks in Windows Task Scheduler.
.DESCRIPTION
    The task list lives in scripts/utils/scheduled_tasks.json (the one source: the watchdog
    audits it and the System page shows it). This script reads it and registers every task
    under the "\AETHER_Agents\" folder, with its schedule, command, time limit, log redirect
    and principal.
.PARAMETER Engine
    The CLI engine for agent (prompt) tasks, e.g. "gemini", "claude". Default "gemini".
.PARAMETER Args
    Headless arguments passed to the engine. Default "--approval-mode yolo --skip-trust".
.PARAMETER Force
    Replace tasks that already exist (default on).
.PARAMETER DryRun
    Build every task and print it as JSON (one line per task) without registering anything.
.EXAMPLE
    .\register_agent_tasks.ps1 -Force
    .\register_agent_tasks.ps1 -DryRun
#>
[CmdletBinding()]
param (
    [string]$Engine = "gemini",
    [string]$Args = "--approval-mode yolo --skip-trust",
    [switch]$Force = $true,
    [switch]$DryRun
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Get-Item $ScriptDir).Parent.Parent.FullName
$LogDir = Join-Path $RepoRoot "Data\logs\agent_runs"
$Manifest = Get-Content (Join-Path $ScriptDir "scheduled_tasks.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$Folder = $Manifest.folder
$Python = "venv_new\Scripts\python.exe"
$Launcher = Join-Path $RepoRoot "run_agent.cmd"

if (-not $DryRun) {
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "   AETHER scheduled-task registration ($($Manifest.tasks.Count) tasks)" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "Repo Root:  $RepoRoot"
    Write-Host "CLI Engine: $Engine"
    Write-Host "Log Dir:    $LogDir"
    Write-Host "----------------------------------------------------------"
}

function New-AetherTrigger($s) {
    switch ($s.type) {
        "hourly" {
            # Starts in the past so the hourly repetition is active immediately, for 9999 days.
            return New-ScheduledTaskTrigger -Once -At "1/1/2000 12:00 AM" -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 9999)
        }
        "daily" {
            $t = New-ScheduledTaskTrigger -Daily -At $s.at
            if ($s.repeat_every_min) {
                $t.Repetition = (New-ScheduledTaskTrigger -Once -At $s.at -RepetitionInterval (New-TimeSpan -Minutes $s.repeat_every_min) -RepetitionDuration (New-TimeSpan -Hours $s.repeat_for_hours)).Repetition
            }
            return $t
        }
        "weekly" { return New-ScheduledTaskTrigger -Weekly -At $s.at -DaysOfWeek $s.day }
        default  { throw "Unknown schedule type '$($s.type)'" }
    }
}

# MultipleInstances IgnoreNew: if an instance is still running, skip the new trigger. These tasks
# mutate shared state (ai_portfolio_game.json, state_of_the_day.xlsx) and send email, so two
# concurrent runs would race writes and duplicate reports.
foreach ($T in $Manifest.tasks) {
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes $T.time_limit_min)
    $Trigger = New-AetherTrigger $T.schedule
    $LogFile = Join-Path $LogDir $T.log

    if ($T.script) {
        # cmd.exe, not PowerShell: Windows PowerShell 5.1 turns every stderr line of a native
        # program into an error record under 2>&1, so LastTaskResult was 1 whenever Python logged
        # a warning (PROD 10-12: the preflight then aborted the morning pipeline), and its >>
        # wrote the log as UTF-16. cmd passes Python's exit code through and appends plain text.
        $Command = "$Python $($T.script) $($T.args)".TrimEnd()
        $Argument = "/c `"cd /d `"$RepoRoot`" && $Command >> `"$LogFile`" 2>&1`""
    } else {
        # Agent tasks: the CLI engine runs the prompt through the environment-healing launcher.
        $Argument = "/c `"`"$Launcher`" $Engine $Args -p `"$($T.prompt)`" >> `"$LogFile`" 2>&1`""
    }
    $Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $Argument
    $Principal = $null
    if ($T.interactive) {
        # Headed Chrome needs the interactive desktop: run only when the user is logged on.
        $Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    }

    if ($DryRun) {
        [pscustomobject]@{
            name           = $T.name
            execute        = "cmd.exe"
            argument       = $Argument
            start          = $Trigger.StartBoundary
            days_interval  = $Trigger.DaysInterval
            days_of_week   = $Trigger.DaysOfWeek
            repeat         = $Trigger.Repetition.Interval
            repeat_for     = $Trigger.Repetition.Duration
            time_limit     = $Settings.ExecutionTimeLimit
            interactive    = [bool]$Principal
        } | ConvertTo-Json -Compress
        continue
    }

    Write-Host "Registering task: $($T.name)..." -ForegroundColor Yellow
    Write-Host "  Summary:     $($T.summary)"
    Write-Host "  Destination: $LogFile"
    try {
        if ($Force) {
            $Existing = Get-ScheduledTask -TaskName $T.name -TaskPath $Folder -ErrorAction SilentlyContinue
            if ($Existing) {
                Unregister-ScheduledTask -TaskName $T.name -TaskPath $Folder -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
                Write-Host "  Removed existing $($T.name) task." -ForegroundColor DarkGray
            }
        }
        $RegParams = @{
            TaskName    = $T.name
            TaskPath    = $Folder
            Action      = $Action
            Trigger     = $Trigger
            Settings    = $Settings
            Description = $T.summary
        }
        if ($Principal) { $RegParams.Principal = $Principal }
        Register-ScheduledTask @RegParams | Out-Null
        Write-Host "  [SUCCESS] registered $($T.name)." -ForegroundColor Green
    }
    catch {
        Write-Error "  [FAILED] to register task $($T.name). Reason: $_"
    }
}

if (-not $DryRun) {
    Write-Host "----------------------------------------------------------"
    Write-Host "All agent tasks have been scheduled under folder '$Folder'." -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Cyan
}
