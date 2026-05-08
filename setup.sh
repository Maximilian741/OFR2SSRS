#!/usr/bin/env bash
# Oracle2SSRS - Unix setup script
set -e

# Color output via tput (with fallback if tput unavailable)
if command -v tput >/dev/null 2>&1 && [ -t 1 ]; then
    BOLD=$(tput bold)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    RED=$(tput setaf 1)
    CYAN=$(tput setaf 6)
    RESET=$(tput sgr0)
else
    BOLD="" GREEN="" YELLOW="" RED="" CYAN="" RESET=""
fi

info()  { printf "%s[INFO]%s  %s\n"  "$CYAN"   "$RESET" "$1"; }
ok()    { printf "%s[OK]%s    %s\n"  "$GREEN"  "$RESET" "$1"; }
warn()  { printf "%s[WARN]%s  %s\n"  "$YELLOW" "$RESET" "$1"; }
fail()  { printf "%s[FAIL]%s  %s\n"  "$RED"    "$RESET" "$1"; }
hdr()   { printf "\n%s%s== %s ==%s\n" "$BOLD" "$CYAN" "$1" "$RESET"; }

# Move to script dir so relative paths work regardless of caller cwd
cd "$(dirname "$0")"

hdr "Oracle2SSRS Setup"

# ----------------------------------------------------------------------
# 1. Detect Python 3.9+
# ----------------------------------------------------------------------
hdr "Step 1/6: Detecting Python"
PYTHON=""
for candidate in python3 python python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    fail "No Python interpreter found on PATH."
    warn "Please install Python 3.9 or newer from https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info[1])')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    warn "Python ${PY_VERSION} detected. This project recommends Python 3.9+."
    warn "Continuing anyway; some features may not work."
else
    ok "Python ${PY_VERSION} found ($PYTHON)."
fi

# ----------------------------------------------------------------------
# 2. pip install requirements
# ----------------------------------------------------------------------
hdr "Step 2/6: Installing dependencies"
if [ ! -f requirements.txt ]; then
    fail "requirements.txt not found in $(pwd)."
    exit 1
fi

PIP_OK=0
if "$PYTHON" -m pip install -r requirements.txt 2>/tmp/oracle2ssrs_pip.log; then
    PIP_OK=1
    ok "Dependencies installed."
else
    warn "pip install failed; retrying with --break-system-packages..."
    if "$PYTHON" -m pip install --break-system-packages -r requirements.txt 2>>/tmp/oracle2ssrs_pip.log; then
        PIP_OK=1
        ok "Dependencies installed (with --break-system-packages)."
    fi
fi

if [ "$PIP_OK" -ne 1 ]; then
    fail "Could not install requirements."
    warn "Try manually:  $PYTHON -m pip install -r requirements.txt"
    warn "Or with a virtualenv:  $PYTHON -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    warn "Last 10 lines of pip log:"
    tail -n 10 /tmp/oracle2ssrs_pip.log 2>/dev/null || true
    exit 1
fi

# ----------------------------------------------------------------------
# 3. Seed sample SQLite DB
# ----------------------------------------------------------------------
hdr "Step 3/6: Seeding sample.sqlite"
if [ ! -f backend/db/seed_sample_db.py ]; then
    warn "backend/db/seed_sample_db.py not found, skipping seed step."
else
    if "$PYTHON" backend/db/seed_sample_db.py; then
        ok "Sample database built at backend/db/sample.sqlite."
    else
        fail "Seed script failed."
        warn "Try manually:  $PYTHON backend/db/seed_sample_db.py"
        exit 1
    fi
fi

# ----------------------------------------------------------------------
# 4. Smoke test the converter
# ----------------------------------------------------------------------
hdr "Step 4/6: Converter smoke test"
SMOKE_CODE="import sys; sys.path.insert(0,'backend'); from converter import convert; convert(open('samples/oracle/SAMPLE_INSPECTION.xml','rb').read()); print('OK')"
if "$PYTHON" -c "$SMOKE_CODE"; then
    ok "Converter smoke test passed."
else
    fail "Converter smoke test failed."
    warn "Run manually to debug:  $PYTHON -c \"$SMOKE_CODE\""
    exit 1
fi

# ----------------------------------------------------------------------
# 5. pytest (optional)
# ----------------------------------------------------------------------
hdr "Step 5/6: Running pytest (if available)"
if "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
    if "$PYTHON" -m pytest -q; then
        ok "All tests passed."
    else
        warn "pytest reported failures (non-fatal for setup)."
    fi
else
    info "pytest not installed; skipping. Install with:  $PYTHON -m pip install pytest"
fi

# ----------------------------------------------------------------------
# 6. Done
# ----------------------------------------------------------------------
hdr "Step 6/6: Finishing up"
ok "Setup complete. Run ./run.bat (Windows) or ./run.sh (Unix) to start the app."
