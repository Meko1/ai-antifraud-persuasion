param(
    [int]$Port = 21818
)

$ErrorActionPreference = "Stop"

$AppId = "ai-antifraud-persuasion"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $AppDir ".venv\Scripts\python.exe"
$RuntimeDir = Join-Path $env:USERPROFILE ".$AppId"
$LogDir = Join-Path $RuntimeDir "logs"
$PidFile = Join-Path $RuntimeDir "app.pid"
$OutLog = Join-Path $LogDir "app.log"
$ErrLog = Join-Path $LogDir "app.err.log"
$HealthUrl = "http://127.0.0.1:$Port/healthz"
$StartTimeout = 60

function Write-Log {
    param([string]$Message)
    Write-Host "[start-local] $Message"
}

function Fail {
    param([string]$Message)
    Write-Error "[start-local][ERROR] $Message"
    exit 1
}

function Test-Health {
    try {
        Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

if (-not (Test-Path $VenvPython)) {
    Fail "virtual environment not found: $VenvPython. Run .\install-local.ps1 first."
}

if (-not (Test-Path (Join-Path $AppDir ".env"))) {
    Fail ".env not found. Run .\install-local.ps1 first."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (Test-Path $PidFile) {
    $oldPid = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Log "existing service detected (PID $oldPid), running stop-local.ps1 first"
        & (Join-Path $AppDir "stop-local.ps1") -Port $Port
    }
}

Write-Log "starting service on port $Port, logs: $OutLog / $ErrLog"
$process = Start-Process `
    -FilePath $VenvPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "info") `
    -WorkingDirectory $AppDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII
Write-Log "process started (PID $($process.Id))"

Write-Log "waiting for health check (timeout ${StartTimeout}s)"
foreach ($i in 1..$StartTimeout) {
    if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
        Write-Host "[start-local][ERROR] process exited; last 40 stderr lines:" -ForegroundColor Red
        if (Test-Path $ErrLog) {
            Get-Content -LiteralPath $ErrLog -Tail 40
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        exit 1
    }

    if (Test-Health) {
        Write-Log "service ready, health check passed (${i}s)"
        Write-Log "open: http://127.0.0.1:$Port/"
        exit 0
    }

    Start-Sleep -Seconds 1
}

Write-Host "[start-local][ERROR] health check did not pass within ${StartTimeout}s; last 40 stderr lines:" -ForegroundColor Red
if (Test-Path $ErrLog) {
    Get-Content -LiteralPath $ErrLog -Tail 40
}
Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
exit 1
