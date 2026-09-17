@echo off
setlocal
title Install Blender C++ Build Tools
echo Installing Visual Studio 2022 C++ Build Tools and Windows SDK.
echo Existing Visual Studio 2019 and downloaded Blender libraries are preserved.
winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --source winget --accept-package-agreements --accept-source-agreements --override "--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
set "result=%errorlevel%"
echo Installer exit code: %result%
echo After installation completes, run Build-And-Run.cmd again.
pause
exit /b %result%
