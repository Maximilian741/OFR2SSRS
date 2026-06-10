@echo off
REM Load .env if present (for ANTHROPIC_API_KEY etc.)
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

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
