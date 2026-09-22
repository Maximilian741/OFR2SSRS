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
REM (eol=# skips comment lines natively. The previous form used a substring
REM  expression on a FOR variable, which batch does not support, so any
REM  machine that HAD a .env died with "The syntax of the command is
REM  incorrect." before the app ever started.)
if exist .env (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" set "%%a=%%b"
    )
)

cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

REM READ-ONLY dependency probe (imports only; installs nothing). A fresh
REM clone that skipped the one-time install otherwise dies on a raw
REM "ModuleNotFoundError: No module named 'lxml'" traceback from Flask's
REM import chain -- which is what a first-time user actually hit. Name the
REM fix instead of the symptom.
%PY% -c "import flask, lxml, docx, werkzeug" >nul 2>nul
REM No ^> / ^< in these echo lines: inside a parenthesised block cmd can drop
REM the caret and turn the arrow into a REDIRECTION, silently creating an
REM empty file named after the next word (measured: a stray "SSRS" file).
if errorlevel 1 (
    echo Oracle to SSRS Converter: a required Python package is not installed.
    echo This launcher only STARTS the app; it never installs anything.
    echo Run this ONCE, then start the launcher again:
    echo.
    echo     %PY% -m pip install -r requirements.txt
    echo.
    exit /b 1
)
%PY% backend\app.py
