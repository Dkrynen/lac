@echo off
REM Model Hub Desktop App Builder
REM Requires: Node.js >=18, Visual Studio Build Tools with C++ workload
REM
REM For CI builds (recommended), push a tag and use GitHub Actions:
REM   git tag v2.0.0
REM   git push origin v2.0.0

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-pake.ps1" %*
