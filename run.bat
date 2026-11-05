@echo off
rem Twinkly bridge launcher for Windows. First run: py -m pip install -r requirements.txt
cd /d "%~dp0"
py -3 run.py %*
pause
