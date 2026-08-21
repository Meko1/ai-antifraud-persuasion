param(
    [int]$Port = 21818
)

$ErrorActionPreference = "Stop"

$AppId = "ai-antifraud-persuasion"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $env:USERPROFILE ".$AppId"
$PidFile = Join-Path $RuntimeDir "app.pid"

function Write-Log {
    param([string]$Message)
    Write-Host "[stop-local] $Message"
}

function Test-AppProcess {
    param([int]$ProcessId)

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $proc) {
        return $false
    }

    return ($proc.CommandLine -like "*uvicorn*app.main:app*") -or ($proc.CommandLine -like "*app.main:app*")
}

function Stop-AppProcess {
    param([int]$ProcessId)

    if (Test-AppProcess -ProcessId $ProcessId) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        Write-Log "stopped process PID $ProcessId"
        return $true
    }

    Write-Log "skipped PID ${ProcessId}: command line does not look like this app"
    return $false
}

$stopped = $false

if (Test-Path $PidFile) {
    $pidText = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pidValue = 0
    if ([int]::TryParse($pidText, [ref]$pidValue)) {
        $stopped = (Stop-AppProcess -ProcessId $pidValue) -or $stopped
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    $stopped = (Stop-AppProcess -ProcessId $listener.OwningProcess) -or $stopped
}

if ($stopped) {
    Write-Log "stop complete"
}
else {
    Write-Log "no local service process found"
}
