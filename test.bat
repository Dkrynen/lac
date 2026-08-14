@echo off
title Model Hub — System Test
echo ============================================
echo   Model Hub — System Test Suite
echo ============================================
echo.

set PASS=0
set FAIL=0

call :test "CLI help" model-hub --help
call :test "List models" model-hub list
call :test "Running models" model-hub ps
call :test "Hardware scan" model-hub scan
call :test "Settings" model-hub settings
call :test "Sessions" model-hub sessions
call :test "Model info" model-hub inspect qwen3:14b

echo.
echo ============================================
echo   Results: %PASS% passed, %FAIL% failed
echo ============================================
echo.
echo   Interactive tests to run manually:
echo.
echo   1. Chat with a model:
echo      model-hub chat
echo.
echo   2. Start the web server:
echo      model-hub-server
echo      Then open http://127.0.0.1:5050
echo.
echo   3. Session management:
echo      model-hub sessions
echo      model-hub sessions load ^<session-id^>
echo.
echo   4. Model parameters in CLI:
echo      model-hub chat --help
echo.
pause
exit /b

:test
setlocal
set NAME=%~1
set CMD=%~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
echo [TEST] %NAME%...
%CMD% >nul 2>&1
if %errorlevel% equ 0 (
    echo   [PASS]
    set /a PASS+=1
) else (
    echo   [FAIL] %CMD%
    set /a FAIL+=1
)
exit /b 0
