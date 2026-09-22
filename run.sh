#!/usr/bin/env bash
# Oracle -> SSRS Converter launcher (Linux/Mac/WSL)
#
# NO INSTALLS. This script only STARTS the app. On locked-down machines an
# automatic `pip install` triggers install activity and PATH warnings the
# user may be unable to act on (work-machine verified) — so dependency
# setup is never done implicitly. If a required package is missing, the
# app's own import error names it, and the ONE optional command is:
#     python3 -m pip install -r requirements.txt
if [ -f .env ]; then set -a; . ./.env; set +a; fi
cd "$(dirname "$0")"

# READ-ONLY dependency probe (imports only; installs nothing). A fresh
# clone that skipped the one-time install otherwise dies on a raw
# "ModuleNotFoundError: No module named 'lxml'" traceback from Flask's
# import chain -- which is what a first-time user actually hit. Name the
# fix instead of the symptom.
if ! python3 -c "import flask, lxml, docx, werkzeug" >/dev/null 2>&1; then
    echo "Oracle -> SSRS Converter: a required Python package is not installed."
    echo "This launcher only STARTS the app; it never installs anything."
    echo "Run this ONCE, then start the launcher again:"
    echo ""
    echo "    python3 -m pip install -r requirements.txt"
    echo ""
    exit 1
fi
python3 backend/app.py
