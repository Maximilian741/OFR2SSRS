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
python3 backend/app.py
