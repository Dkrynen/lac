@echo off
set PY_SCRIPT=%~dp0cli.py
set PYTHON=
for %%p in (
  "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  python3.exe
  python.exe
) do (
  if exist %%p set PYTHON=%%p
  if not "!PYTHON!"=="" goto :found
)
:found
if "%PYTHON%"=="" (
  echo Python not found. Please install Python 3.10+
  pause
  exit /b 1
)
"%PYTHON%" "%PY_SCRIPT%" %*
