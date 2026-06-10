@echo off
REM One-command pre-push safety check. Run this before every "git push".
python tools\preflight_push.py
