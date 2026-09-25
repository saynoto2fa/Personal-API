<#
Registers two per-user Task Scheduler tasks, under \Personal-API\, that start at logon with no
console window:
  - "API server": python -m app.serve   (http://127.0.0.1:8000, logs\api.log)
  - "Watcher":    python -m app.watcher (logs\watcher.log)
Each task also re-runs every 5 minutes. While the process is still running the new run is
ignored, so this only restarts it after a crash. The 5-minute schedule is a separate trigger
starting at install time; a repetition attached to the logon trigger would only begin after the
next logon. No admin rights needed.

  powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
  powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1
#>
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "Virtual environment not found: $pythonw (run the setup steps in README.md first)" }
$user = "$env:USERDOMAIN\$env:USERNAME"

$tasks = [ordered]@{
    'API server' = @{ Args = '-m app.serve --log-file logs\api.log'; Desc = 'Personal Unified API on http://127.0.0.1:8000' }
    'Watcher'    = @{ Args = '-m app.watcher --log-file logs\watcher.log'; Desc = 'Personal-API file watcher: indexes WATCH_SOURCES into pgvector' }
}

foreach ($name in $tasks.Keys) {
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $tasks[$name].Args -WorkingDirectory $repo
    $trigger = @(
        (New-ScheduledTaskTrigger -AtLogOn -User $user),
        (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
    )
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskPath '\Personal-API\' -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description $tasks[$name].Desc -Force | Out-Null
    Write-Output "registered \Personal-API\$name"
}
Write-Output "Start now without logging out:  Start-ScheduledTask -TaskPath '\Personal-API\' -TaskName 'API server'  (and 'Watcher')"
