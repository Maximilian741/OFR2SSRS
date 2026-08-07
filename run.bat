@echo off
REM Oracle -> SSRS Converter launcher (Windows)
REM
REM NO INSTALLS. This script only STARTS the app. On locked-down machines
REM an automatic `pip install` triggers install activity and PATH warnings
REM the user may be unable to act on (work-machine verified) — dependency
REM setup is never done implicitly. If a required package is missing, the
REM app's own import error names it, and the ONE optional command is:
REM     python -m pip install -r requirements.txt

REM Load .env if present (for ANTHROPIC_API_KEY etc.)
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)
%PY% backend\app.py
