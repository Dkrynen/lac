@echo off
REM Model Hub — Installer Script
REM Run this as Administrator for best results

title Model Hub Installer
echo ============================================
echo   Model Hub v2.0.0 — Installer
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.10+ is required. Install from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Check Ollama
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Ollama is not installed or not in PATH.
    echo Download from: https://ollama.com/download
    echo.
    pause
)

REM Get project directory (where this script lives)
set PROJECT_DIR=%~dp0

REM Install/upgrade pip
echo [1/3] Upgrading pip...
python -m pip install --upgrade pip >nul 2>&1

REM Install the package
echo [2/3] Installing Model Hub...
pip install -e "%PROJECT_DIR%"
if %errorlevel% neq 0 (
    echo [ERROR] Installation failed.
    pause
    exit /b 1
)

REM Done
echo [3/3] Installation complete!
echo.
echo ============================================
echo   Usage:
echo ============================================
echo.
echo   Start the web app:
echo     model-hub-server
echo     Then open http://127.0.0.1:5050 in your browser
echo.
echo   CLI commands:
echo     model-hub --help        Show all commands
echo     model-hub list          List installed models
echo     model-hub chat          Start interactive chat
echo     model-hub scan          Scan your hardware
echo     model-hub recommend     Get model recommendations
echo.
echo   Desktop app (Pake):
echo     Push a version tag to GitHub to trigger CI build:
echo       git tag v2.1.0
echo       git push origin v2.1.0
echo.
pause
