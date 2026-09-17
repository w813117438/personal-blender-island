@echo off
setlocal
set "BLENDER_USER_CONFIG=%~dp0.local-build\user-config"
set "BLENDER_BIN=%~dp0.local-build\build-full\bin\Release"
if not exist "%BLENDER_BIN%\blender.exe" (
    echo Blender has not been built yet. Run Build-And-Run.cmd first.
    pause
    exit /b 1
)
if not exist "%BLENDER_USER_CONFIG%" mkdir "%BLENDER_USER_CONFIG%"
start "" /D "%BLENDER_BIN%" "%BLENDER_BIN%\blender.exe"
exit /b %errorlevel%
