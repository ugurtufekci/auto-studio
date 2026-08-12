@echo off
REM autoStudio — one double-click: get the latest code, then open the console.
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

echo.
echo === checking the Instagram keys ===
python -m studio.publisher_instagram

echo.
echo === opening the console ===
start "" http://localhost:8377
python dashboard\serve.py

echo.
echo The console stopped. Press any key to close this window.
pause >nul
