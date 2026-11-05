@echo off
rem twinkly-bridge launcher for Windows. First run install.bat once.
rem Extra arguments pass through, e.g.  run.bat --host 0.0.0.0 --port 5120
cd /d "%~dp0"
if exist venv\Scripts\python.exe (
  venv\Scripts\python.exe run.py %*
) else (
  echo venv\ not found: run install.bat first. Trying the system Python instead.
  py -3 run.py %*
)
pause
