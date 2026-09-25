<#
Stops and removes the tasks created by install-autostart.ps1.

  powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1
#>
$ErrorActionPreference = 'Stop'
$tasks = Get-ScheduledTask -TaskPath '\Personal-API\' -ErrorAction SilentlyContinue
if (-not $tasks) { Write-Output 'No Personal-API tasks registered.'; return }
foreach ($t in $tasks) {
    Stop-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName -Confirm:$false
    Write-Output "removed $($t.TaskPath)$($t.TaskName)"
}
