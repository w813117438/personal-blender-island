[CmdletBinding()]
param(
    [ValidateSet('Build', 'Check', 'Prepare', 'Run')][string]$Action = 'Build',
    [ValidateSet('full', 'lite')][string]$Profile = 'full',
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
$sourceDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$stateDir = Join-Path $sourceDir '.local-build'
$buildDir = Join-Path $stateDir "build-$Profile"
$blenderExe = Join-Path $buildDir 'bin\Release\blender.exe'
New-Item -ItemType Directory -Force -Path "$stateDir\logs" | Out-Null
$logPath = Join-Path $stateDir ('logs\{0}-{1}.log' -f $Action,(Get-Date -Format 'yyyyMMdd-HHmmss'))
Start-Transcript -Path $logPath | Out-Null
$exitCode = 0
$lock = $null
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    Write-Host ('> {0} {1}' -f $Exe, ($Arguments -join ' '))
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed (exit $LASTEXITCODE). See $logPath" }
}
function Find-CMake {
    $portable = Join-Path $stateDir 'tools\cmake-3.31.8-windows-x86_64\bin\cmake.exe'
    if (Test-Path $portable) { return $portable }
    $command = Get-Command cmake.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}
function Start-TestBlender {
    if (!(Test-Path $blenderExe)) { throw "No compiled Blender at $blenderExe. Run Build first." }
    # Keep this development build's preferences separate from installed Blender.
    $env:BLENDER_USER_CONFIG = Join-Path $stateDir 'user-config'
    New-Item -ItemType Directory -Force -Path $env:BLENDER_USER_CONFIG | Out-Null
    Start-Process -FilePath $blenderExe -WorkingDirectory (Split-Path $blenderExe)
    Write-Host "Started: $blenderExe"
}
try {
    if ($Action -eq 'Run') { Start-TestBlender }
    else {
        $lock = [IO.File]::Open((Join-Path $stateDir 'build.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        if (![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
            throw 'This launcher is configured for Windows x64.'
        }
        $git = (Get-Command git.exe -ErrorAction Stop).Source
        Invoke-Native $git @('lfs', 'version')
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
        if (!(Test-Path $vswhere)) { throw 'Install Visual Studio with Desktop development with C++ and a Windows SDK.' }
        $vsJson = & $vswhere -latest -version '[17.0,18.0)' -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -format json
        $vs = @($vsJson | ConvertFrom-Json | ForEach-Object { $_ } | Where-Object { $_.installationPath })
        if (!$vs.Count) { throw "VS 2022 C++ tools (17.14.14+) required. Run $sourceDir\Install-Build-Tools.cmd, then retry." }
        $toolVersionFile = Join-Path $vs[0].installationPath 'VC\Auxiliary\Build\Microsoft.VCToolsVersion.default.txt'
        $toolVersion = if (Test-Path $toolVersionFile) { [version](Get-Content $toolVersionFile -Raw).Trim() } else { [version]'0.0' }
        $compilerPath = Join-Path $vs[0].installationPath "VC\Tools\MSVC\$toolVersion\bin\Hostx64\x64\cl.exe"
        if (!(Test-Path -LiteralPath $compilerPath)) { throw "Compiler not found: $compilerPath" }
        # Servicing updates can replace cl.exe without changing the toolset directory name.
        $compilerInfo = (Get-Item -LiteralPath $compilerPath).VersionInfo
        $compilerVersion = [version]("{0}.{1}.{2}.{3}" -f $compilerInfo.FileMajorPart, $compilerInfo.FileMinorPart, $compilerInfo.FileBuildPart, $compilerInfo.FilePrivatePart)
        Write-Host "Actual MSVC compiler: $compilerVersion (toolset directory: $toolVersion)"
        if ($compilerVersion -lt [version]'19.44.35216.0') {
            throw "Compiler $compilerVersion is too old. Run $sourceDir\Install-Build-Tools.cmd (requires MSVC compiler 19.44.35216+)."
        }
        $major = ([version]$vs[0].installationVersion).Major
        $year = switch ($major) { 16 {'2019'} 17 {'2022'} 18 {'2026'} default { throw "Unsupported Visual Studio version: $major" } }
        $vsFlag = $year
        if ($vs[0].productId -eq 'Microsoft.VisualStudio.Product.BuildTools') { $vsFlag += 'b' }
        Write-Host "Source: $sourceDir"
        Write-Host "Compiler: $($vs[0].installationPath)"
        $cmake = Find-CMake
        Write-Host "CMake: $cmake"
        Write-Host "Windows dependencies present: $(Test-Path "$sourceDir\lib\windows_x64\.git")"
        Write-Host "Build output: $buildDir"
        if ($Action -ne 'Check') {
            if (!$cmake) {
                Write-Host 'Downloading portable CMake 3.31.8 from Kitware (no system installation).'
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                $toolsDir = Join-Path $stateDir 'tools'
                New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
                $asset = 'cmake-3.31.8-windows-x86_64.zip'
                $base = 'https://github.com/Kitware/CMake/releases/download/v3.31.8'
                $zip = Join-Path $toolsDir $asset
                Invoke-WebRequest "$base/$asset" -OutFile $zip -UseBasicParsing
                $hashFile = Join-Path $toolsDir 'cmake-3.31.8-SHA-256.txt'
                Invoke-WebRequest "$base/cmake-3.31.8-SHA-256.txt" -OutFile $hashFile -UseBasicParsing
                $hashes = Get-Content -LiteralPath $hashFile -Raw
                $line = @($hashes -split "`n" | Where-Object { $_.Trim().EndsWith($asset) })
                if ($line.Count -ne 1) { throw 'CMake checksum entry missing.' }
                $expected = ($line[0].Trim() -split '\s+')[0]
                if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $expected) { throw 'CMake checksum mismatch.' }
                Expand-Archive -LiteralPath $zip -DestinationPath $toolsDir -Force
                $cmake = Find-CMake
            }
            Invoke-Native $cmake @('--version')
            $env:PATH = (Split-Path $cmake) + ';' + $env:PATH
            Write-Host 'Preparing assets and Windows libraries. First download can take a long time.'
            Invoke-Native $git @('-C', $sourceDir, 'lfs', 'install', '--local')
            # GitHub mirrors source commits but does not host Blender's LFS objects.
            # Use the upstream LFS endpoint without changing the source/push remote.
            Invoke-Native $git @('-C', $sourceDir, '-c', 'lfs.url=https://projects.blender.org/blender/blender.git/info/lfs', 'lfs', 'pull', '--exclude=tests/**')
            $previousSmudge = $env:GIT_LFS_SKIP_SMUDGE
            try {
                $env:GIT_LFS_SKIP_SMUDGE = '1'
                Invoke-Native $git @('-C', $sourceDir, '-c', 'submodule.lib/windows_x64.update=checkout', 'submodule', 'update', '--init', '--checkout', '--progress', 'lib/windows_x64')
            } finally { $env:GIT_LFS_SKIP_SMUDGE = $previousSmudge }
            Invoke-Native $git @('-C', "$sourceDir\lib\windows_x64", 'lfs', 'pull')
            if ($Action -eq 'Build') {
                # CMake cannot reuse a VS 2019 cache with a VS 2022 generator.
                $cachePath = Join-Path $buildDir 'CMakeCache.txt'
                if (Test-Path $cachePath) {
                    $generator = Select-String -LiteralPath $cachePath -Pattern '^CMAKE_GENERATOR:INTERNAL=(.+)$'
                    $expectedGenerator = "Visual Studio $major $year"
                    if ($generator -and $generator.Matches[0].Groups[1].Value -ne $expectedGenerator) {
                        $resolvedBuild = (Resolve-Path -LiteralPath $buildDir).Path
                        $expectedBuild = [IO.Path]::GetFullPath((Join-Path $stateDir "build-$Profile"))
                        if ($resolvedBuild -ne $expectedBuild) { throw 'Unexpected build path; refusing to move.' }
                        $backupBuild = "$expectedBuild-old-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
                        Write-Host "Preserving incompatible CMake build directory: $backupBuild"
                        Move-Item -LiteralPath $resolvedBuild -Destination $backupBuild
                    }
                }
                Push-Location $sourceDir
                try {
                    Invoke-Native "$sourceDir\make.bat" @($Profile, $vsFlag, 'builddir', $buildDir)
                } finally { Pop-Location }
                if (!(Test-Path $blenderExe)) { throw "Build returned without producing $blenderExe" }
                Invoke-Native $blenderExe @('--background', '--factory-startup', '--python-exit-code', '1', '--python-expr', 'import bpy; print(bpy.app.version_string); assert bpy.context.scene is not None')
                if (!$NoLaunch) { Start-TestBlender }
            }
        } else {
            Write-Host 'Check complete. Missing CMake and libraries will be downloaded by Build/Prepare.'
        }
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($lock) { $lock.Dispose() }
    Write-Host "Log: $logPath"
    Stop-Transcript | Out-Null
}
exit $exitCode
