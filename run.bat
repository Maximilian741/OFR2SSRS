@echo off
REM Oracle -> SSRS Converter launcher (Windows)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)
%PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt
%PY% backend\app.py
