@echo off
setlocal
title Blender - Build and Run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\local-build\Build-Blender.ps1" %*
set "result=%errorlevel%"
echo.
if not "%result%"=="0" echo Build or startup failed. See the log path printed above.
pause
exit /b %result%
