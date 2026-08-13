@echo off
REM autoStudio — one double-click: environment, latest code, console.
REM The operator's machine is where releases happen, so it must never be the
REM stale one; pulling first removes a whole class of "did you pull?" turns.

cd /d "%~dp0"
title autoStudio

echo === updating from origin/main ===
git pull origin main
if errorlevel 1 (
  echo.
  echo Could not pull ^(offline, or local edits in the way^).
  echo Continuing with the code already on this machine.
)

REM A private virtualenv rather than the system interpreter: a shared install
REM is what makes a machine's Python quietly diverge from what the studio was
REM tested against. Rebuilt when requirements.txt changes.
if not exist ".venv" (
  echo.
  echo === first run: building the environment ^(a minute or two^) ===
  python -m venv .venv
  if errorlevel 1 (
    echo Could not create .venv — is Python 3.11+ installed and on PATH?
    pause
    exit /b 1
  )
)
if not exist ".venv\.requirements-installed" (
  echo.
  echo === installing dependencies ===
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency install failed — see the error above.
    pause
    exit /b 1
  )
  echo installed > .venv\.requirements-installed
)

if not exist ".env" (
  echo.
  echo No .env yet — copying .env.example, which describes what to paste.
  copy .env.example .env >nul
  notepad .env
)

echo.
echo === checking the Instagram keys ===
.venv\Scripts\python -m studio.publisher_instagram

echo.
echo === opening the console ===
start "" http://localhost:8377
.venv\Scripts\python dashboard\serve.py

echo.
echo The console stopped. Press any key to close this window.
pause >nul
