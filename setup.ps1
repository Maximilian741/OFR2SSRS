#!/usr/bin/env pwsh
# Oracle2SSRS - Windows PowerShell setup script
# Idempotent, fail-soft setup for fresh clones.

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Hdr  ($Msg) { Write-Host ""; Write-Host "== $Msg ==" -ForegroundColor Cyan }
function Write-Info ($Msg) { Write-Host "[INFO]  $Msg" -ForegroundColor Cyan  }
function Write-Ok   ($Msg) { Write-Host "[OK]    $Msg" -ForegroundColor Green }
function Write-Warn2($Msg) { Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Write-Fail ($Msg) { Write-Host "[FAIL]  $Msg" -ForegroundColor Red   }

# Move to script directory so relative paths resolve correctly
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Hdr "Oracle2SSRS Setup"

# ---------------------------------------------------------------------------
# 1. Detect Python 3.9+
# ---------------------------------------------------------------------------
Write-Hdr "Step 1/6: Detecting Python"

$Python = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $ver = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) {
                $Python = $cmd.Source
                $PyVersion = $ver.Trim()
                break
            }
        } catch {}
    }
}

if (-not $Python) {
    Write-Fail "No Python interpreter found on PATH."
    Write-Warn2 "Install Python 3.9+ from https://www.python.org/downloads/"
    Write-Warn2 "Be sure to check 'Add Python to PATH' during installation."
    exit 1
}

$verParts = $PyVersion.Split('.')
$major = [int]$verParts[0]
$minor = [int]$verParts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
    Write-Warn2 "Python $PyVersion detected. This project recommends Python 3.9+."
    Write-Warn2 "Continuing anyway; some features may not work."
} else {
    Write-Ok "Python $PyVersion found ($Python)."
}

# ---------------------------------------------------------------------------
# 2. pip install requirements
# ---------------------------------------------------------------------------
Write-Hdr "Step 2/6: Installing dependencies"

if (-not (Test-Path 'requirements.txt')) {
    Write-Fail "requirements.txt not found in $ScriptDir."
    exit 1
}

$pipLog = Join-Path $env:TEMP 'oracle2ssrs_pip.log'
$pipOk  = $false
try {
    & $Python -m pip install -r requirements.txt 2>&1 | Tee-Object -FilePath $pipLog | Out-Null
    if ($LASTEXITCODE -eq 0) { $pipOk = $true }
} catch {
    # fall through to retry
}

if (-not $pipOk) {
    Write-Warn2 "Initial pip install failed. Retrying with --user..."
    try {
        & $Python -m pip install --user -r requirements.txt 2>&1 | Tee-Object -FilePath $pipLog -Append | Out-Null
        if ($LASTEXITCODE -eq 0) { $pipOk = $true }
    } catch {}
}

if (-not $pipOk) {
    Write-Fail "Could not install requirements."
    Write-Warn2 "Try manually:  $Python -m pip install -r requirements.txt"
    Write-Warn2 "Or with a virtualenv:  $Python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
    if (Test-Path $pipLog) {
        Write-Warn2 "Last 10 lines of pip log ($pipLog):"
        Get-Content $pipLog -Tail 10 | ForEach-Object { Write-Host "    $_" }
    }
    exit 1
}
Write-Ok "Dependencies installed."

# ---------------------------------------------------------------------------
# 3. Seed sample SQLite DB
# ---------------------------------------------------------------------------
Write-Hdr "Step 3/6: Seeding sample.sqlite"

$seedScript = Join-Path 'backend' 'db' 'seed_sample_db.py'
if (-not (Test-Path $seedScript)) {
    Write-Warn2 "$seedScript not found, skipping seed step."
} else {
    & $Python $seedScript
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Seed script failed (exit $LASTEXITCODE)."
        Write-Warn2 "Try manually:  $Python $seedScript"
        exit 1
    }
    Write-Ok "Sample database built at backend\db\sample.sqlite."
}

# ---------------------------------------------------------------------------
# 4. Converter smoke test
# ---------------------------------------------------------------------------
Write-Hdr "Step 4/6: Converter smoke test"

$smokeCode = "import sys; sys.path.insert(0,'backend'); from converter import convert; convert(open('samples/oracle/MVWF_PERMIT.xml','rb').read()); print('OK')"
& $Python -c $smokeCode
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Converter smoke test failed (exit $LASTEXITCODE)."
    Write-Warn2 "Run manually to debug:  $Python -c `"$smokeCode`""
    exit 1
}
Write-Ok "Converter smoke test passed."

# ---------------------------------------------------------------------------
# 5. pytest (optional)
# ---------------------------------------------------------------------------
Write-Hdr "Step 5/6: Running pytest (if available)"

& $Python -c "import pytest" 2>$null
if ($LASTEXITCODE -eq 0) {
    & $Python -m pytest -q
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "All tests passed."
    } else {
        Write-Warn2 "pytest reported failures (non-fatal for setup)."
    }
} else {
    Write-Info "pytest not installed; skipping. Install with:  $Python -m pip install pytest"
}

# ---------------------------------------------------------------------------
# 6. Done
# ---------------------------------------------------------------------------
Write-Hdr "Step 6/6: Finishing up"
Write-Ok "Setup complete. Run ./run.bat (Windows) or ./run.sh (Unix) to start the app."
