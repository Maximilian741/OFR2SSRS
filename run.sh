#!/usr/bin/env bash
# Oracle -> SSRS Converter launcher (Linux/Mac/WSL)
# Load .env if present (for ANTHROPIC_API_KEY etc.)
if [ -f .env ]; then set -a; . ./.env; set +a; fi
cd "$(dirname "$0")"
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt
python3 backend/app.py
