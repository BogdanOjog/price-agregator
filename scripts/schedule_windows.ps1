# Registers a daily Windows scheduled task that runs `pisaprices collect --all` from this project.
# Usage (once): powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Time 07:30
# Remove it:    Unregister-ScheduledTask -TaskName "price-agregator collect" -Confirm:$false
param(
    [string]$Time = "07:30",
    [string]$TaskName = "price-agregator collect"
)

$root = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $candidate = Join-Path $env:APPDATA "Python\Python313\Scripts\uv.exe"
    if (Test-Path $candidate) { $uv = $candidate } else { throw "uv not found: install it or add it to PATH" }
}
New-Item -ItemType Directory -Force (Join-Path $root "data\logs") | Out-Null

$command = "cd /d `"$root`" && `"$uv`" run pisaprices collect --all >> data\logs\collect.log 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $command"
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Task '$TaskName' runs every day at $Time; log: $root\data\logs\collect.log"
