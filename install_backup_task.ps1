param(
    [string]$TaskName = 'ImprezaPostgresBackup',
    [int]$EveryDays = 1,
    [ValidatePattern('^\d{2}:\d{2}$')]
    [string]$At = '06:00'
)

$ErrorActionPreference = 'Stop'

if ($EveryDays -lt 1) {
    throw 'EveryDays must be 1 or greater.'
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runScript = Join-Path $scriptDir 'run_postgres_backup.ps1'

if (-not (Test-Path $runScript)) {
    throw "Backup runner not found: $runScript"
}

$powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$timeOfDay = [datetime]::ParseExact($At, 'HH:mm', $null)
$userId = if ($env:USERDOMAIN) { "$($env:USERDOMAIN)\$($env:USERNAME)" } else { $env:USERNAME }

$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $EveryDays -At $timeOfDay
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Scheduled task created: $TaskName"
Write-Host "Runs every $EveryDays day(s) at $At"
Write-Host "Command: $powerShellExe -NoProfile -ExecutionPolicy Bypass -File `"$runScript`""