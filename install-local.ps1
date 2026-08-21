param(
    [switch]$Dev
)

$ErrorActionPreference = "Stop"

$AppId = "ai-antifraud-persuasion"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $AppDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RuntimeDir = Join-Path $env:USERPROFILE ".$AppId"
$MinVersion = [version]"3.10"

function Write-Log {
    param([string]$Message)
    Write-Host "[install-local] $Message"
}

function Fail {
    param([string]$Message)
    Write-Error "[install-local][ERROR] $Message"
    exit 1
}

function Get-Python {
    $candidates = @(
        @{ Command = "py"; Args = @("-3.13") },
        @{ Command = "py"; Args = @("-3.12") },
        @{ Command = "py"; Args = @("-3.11") },
        @{ Command = "py"; Args = @("-3.10") },
        @{ Command = "python"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if (-not $cmd) {
            continue
        }

        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            $versionText = & $candidate.Command @($candidate.Args + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")) 2>$null
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }

        if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($versionText)) {
            continue
        }

        $version = [version]$versionText.Trim()
        if ($version -ge $MinVersion) {
            return @{
                Command = $candidate.Command
                Args = $candidate.Args
                Version = $version
            }
        }
    }

    return $null
}

function Invoke-Python {
    param(
        [hashtable]$Python,
        [string[]]$Args
    )

    & $Python.Command @($Python.Args + $Args)
}

function Ensure-EnvFile {
    $envPath = Join-Path $AppDir ".env"
    $examplePath = Join-Path $AppDir ".env.example"

    if (-not (Test-Path $envPath)) {
        if (-not (Test-Path $examplePath)) {
            Fail ".env is missing and .env.example was not found"
        }
        Copy-Item -LiteralPath $examplePath -Destination $envPath
        Write-Log "created .env from .env.example"
    }

    $content = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8
    if ($content -notmatch "(?m)^STATE_SIGNING_SECRET\s*=\s*\S+") {
        $secret = & $VenvPython -c "import secrets; print(secrets.token_urlsafe(32))"
        if ($content -match "(?m)^STATE_SIGNING_SECRET\s*=") {
            $content = $content -replace "(?m)^STATE_SIGNING_SECRET\s*=.*$", "STATE_SIGNING_SECRET=$secret"
        }
        else {
            $content = $content.TrimEnd() + "`r`nSTATE_SIGNING_SECRET=$secret`r`n"
        }
        Set-Content -LiteralPath $envPath -Value $content -Encoding UTF8
        Write-Log "generated local STATE_SIGNING_SECRET in .env"
    }
}

Write-Log "app dir: $AppDir"

$python = Get-Python
if (-not $python) {
    Fail "Python 3.10+ was not found. Install Python 3.11 and retry."
}
Write-Log "using Python: $($python.Command) $($python.Args -join ' ') ($($python.Version))"

if (Test-Path $VenvPython) {
    Write-Log "reusing virtual environment: $VenvDir"
}
else {
    Write-Log "creating virtual environment: $VenvDir"
    Invoke-Python -Python $python -Args @("-m", "venv", $VenvDir)
}

Write-Log "upgrading pip / setuptools / wheel"
& $VenvPython -m pip install --upgrade pip setuptools wheel

$requirements = if ($Dev) { "requirements-dev.txt" } else { "requirements.txt" }
Write-Log "installing dependencies: $requirements"
& $VenvPython -m pip install -r (Join-Path $AppDir $requirements)

Ensure-EnvFile

New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir "logs") | Out-Null
Write-Log "runtime dir: $RuntimeDir"
Write-Log "install complete"
