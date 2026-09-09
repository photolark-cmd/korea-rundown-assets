@echo off
rem Double-click launcher for the retouch studio. ASCII only on purpose:
rem Korean text inside a .cmd breaks depending on the console code page.
rem All Korean prompts live in launch.py.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] Python venv not found: .venv
  echo     Run this once:  python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launch.py %*
if errorlevel 1 pause
