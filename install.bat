@echo off
rem twinkly-bridge first-time setup for Windows: creates venv\ and installs the pinned
rem requirements. Safe to re-run after a git pull. Then start the bridge with run.bat.
cd /d "%~dp0"
where py >nul 2>nul || (echo Python is not installed. Get Python 3.10 or newer from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^). & pause & exit /b 1)
if not exist venv\Scripts\python.exe (
  echo Creating venv ...
  py -3 -m venv venv || (echo Could not create the virtualenv. & pause & exit /b 1)
)
venv\Scripts\python.exe -m pip install --quiet --upgrade pip
venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || (echo pip install failed. & pause & exit /b 1)
if not exist .env copy .env.example .env >nul && echo Created .env from .env.example
if not exist instance mkdir instance
if exist bridge_config.json if not exist instance\bridge_config.json move bridge_config.json instance\bridge_config.json >nul && echo Moved bridge_config.json into instance\
echo.
echo Installed. Start the bridge with run.bat ^(add --host 0.0.0.0 to reach the UI from other PCs^).
pause
