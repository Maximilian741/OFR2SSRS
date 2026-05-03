#!/usr/bin/env bash
# Oracle -> SSRS Converter launcher (Linux/Mac/WSL)
cd "$(dirname "$0")"
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt
python3 backend/app.py
