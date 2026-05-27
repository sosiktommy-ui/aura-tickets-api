param(
    [string]$PythonExe
)

$ErrorActionPreference = 'Stop'

function Resolve-PythonExe {
    param([string]$RequestedPython)

    if ($RequestedPython) {
        return (Resolve-Path $RequestedPython).Path
    }

    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $cursor = [System.IO.DirectoryInfo]$scriptDir

    while ($null -ne $cursor) {
        $venvPython = Join-Path $cursor.FullName '.venv\Scripts\python.exe'
        if (Test-Path $venvPython) {
            return $venvPython
        }
        $cursor = $cursor.Parent
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw 'Python executable not found. Pass -PythonExe explicitly.'
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backupScript = Join-Path $scriptDir 'backup_postgres.py'
$logDir = Join-Path $scriptDir 'backups\logs'
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logFile = Join-Path $logDir "backup_run_$timestamp.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$resolvedPython = Resolve-PythonExe -RequestedPython $PythonExe

& $resolvedPython $backupScript 2>&1 | Tee-Object -FilePath $logFile
exit $LASTEXITCODE