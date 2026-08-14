@echo off
setlocal enabledelayedexpansion
set PID_FILE=%~dp0model-hub.pid
if exist "%PID_FILE%" (
  set /p OLD_PID=<"%PID_FILE%"
  if not "!OLD_PID!"=="" (
    echo Stopping old process !OLD_PID!...
    taskkill /F /PID !OLD_PID! 2>nul
    timeout /t 2 /nobreak >nul
  )
)
echo Starting Model Hub...
cd /d "%~dp0"
start "" cmd /c "python server.py & pause"
echo Model Hub started at http://127.0.0.1:5050
