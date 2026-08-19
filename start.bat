@echo off
REM Start WhatsApp Ghost. Creates the virtualenv and .env on first run.
REM
REM   start.bat                 - http://127.0.0.1:8787
REM   start.bat --port 9000     - a different port
REM   start.bat --reload        - auto-reload on source changes
REM   start.bat --open          - also open the console in a browser
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8787"
set "OPENBROWSER=0"
set "EXTRA="

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--host" (set "HOST=%~2" & shift & shift & goto parse)
if /i "%~1"=="--port" (set "PORT=%~2" & shift & shift & goto parse)
if /i "%~1"=="--open" (set "OPENBROWSER=1" & shift & goto parse)
if /i "%~1"=="--mode" (set "EXTRA=!EXTRA! --mode %~2" & shift & shift & goto parse)
if /i "%~1"=="-h" goto help
if /i "%~1"=="--help" goto help
set "EXTRA=!EXTRA! %~1"
shift
goto parse

:help
echo Usage: start.bat [--host H] [--port N] [--mode strict^|lenient] [--reload] [--open]
exit /b 0

:parsed
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo No virtualenv found, creating one...
  where uv >nul 2>&1
  if !errorlevel! equ 0 (
    uv sync
  ) else (
    python -m venv .venv
    if not exist "%PYTHON%" (
      echo ERROR: could not create the virtualenv. Is Python installed and on PATH?
      exit /b 1
    )
    "%PYTHON%" -m pip install --quiet --upgrade pip
    "%PYTHON%" -m pip install --quiet -e .
  )
)
if not exist "%PYTHON%" (
  echo ERROR: %PYTHON% is still missing after setup.
  exit /b 1
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo Created .env from .env.example
)

REM The startup banner prints arrows that crash on Windows' cp1252 console.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo WhatsApp Ghost starting on http://%HOST%:%PORT%
echo   Console  http://%HOST%:%PORT%/console
echo   Phone    http://%HOST%:%PORT%/phone
echo   Docs     http://%HOST%:%PORT%/docs
echo.

if "%OPENBROWSER%"=="1" start "" /b cmd /c "timeout /t 2 >nul & start """" ""http://%HOST%:%PORT%/console"""

"%PYTHON%" ghost.py start --host %HOST% --port %PORT%%EXTRA%
exit /b %errorlevel%
