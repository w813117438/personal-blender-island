[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('ZIP', 'MSI')][string]$Format,
    [string]$BuildDirectory,
    [string]$OutputDirectory,
    [string]$StagingDirectory
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (!$BuildDirectory) { $BuildDirectory = Join-Path $root '.local-build/build-full' }
if (!$OutputDirectory) { $OutputDirectory = Join-Path $root '.local-build/packages' }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
if ($Format -eq 'ZIP') {
    $revision = (git -C $root rev-parse --short=8 HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine source revision.' }
    $program = Join-Path $BuildDirectory 'bin/Release'
    if (!(Test-Path "$program/blender.exe")) { throw 'Compiled Blender is missing.' }
    $package = Join-Path $OutputDirectory "blender-personal-windows-x64-$revision.zip"
    & 7z a -tzip $package "$program/*" -mx=3
    if ($LASTEXITCODE -ne 0) { throw 'ZIP packaging failed.' }
    & 7z t $package
    if ($LASTEXITCODE -ne 0) { throw 'ZIP integrity check failed.' }
} else {
    $portable = Join-Path $root '.local-build/tools/cmake-3.31.8-windows-x86_64/bin/cpack.exe'
    $cpack = if (Test-Path $portable) { $portable } else { (Get-Command cpack.exe -ErrorAction Stop).Source }
    # WiX 3 light.exe cannot read some staged files when their paths exceed MAX_PATH.
    # CPack nests component and package names, so stage close to the drive root.
    if (!$StagingDirectory) {
        $StagingDirectory = Join-Path ([IO.Path]::GetPathRoot($root)) ('bp-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    }
    $StagingDirectory = [IO.Path]::GetFullPath($StagingDirectory)
    if (Test-Path $StagingDirectory) { throw "Staging directory must be new: $StagingDirectory" }
    if ($StagingDirectory.Length -gt 20) { throw 'Choose an MSI staging path of at most 20 characters, e.g. D:\bp-test.' }
    if (!(Get-Command candle.exe -ErrorAction SilentlyContinue)) {
        $wix = Get-ChildItem "${env:ProgramFiles(x86)}/WiX Toolset*/bin/candle.exe" | Select-Object -First 1
        if (!$wix) { throw 'WiX 3 is required for MSI packaging.' }
        $env:PATH = $wix.DirectoryName + ';' + $env:PATH
    }
    $logDirectory = Join-Path $OutputDirectory 'wix-diagnostics'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Push-Location $BuildDirectory
    try {
        & $cpack -G WIX -C Release --config CPackConfig.cmake -B $StagingDirectory 2>&1 |
            Tee-Object -FilePath (Join-Path $logDirectory 'cpack.log')
        if ($LASTEXITCODE -ne 0) { throw 'MSI packaging failed; see wix-diagnostics.' }
        $msi = @(Get-ChildItem -LiteralPath $StagingDirectory -Filter '*.msi' -File)
        if ($msi.Count -ne 1) { throw 'Expected exactly one MSI package.' }
        Copy-Item -LiteralPath $msi[0].FullName -Destination $OutputDirectory
        $package = Join-Path $OutputDirectory $msi[0].Name
    } finally {
        Pop-Location
        $wixLog = Join-Path $StagingDirectory '_CPack_Packages/Windows/WIX/wix.log'
        if (Test-Path $wixLog) {
            Copy-Item -LiteralPath $wixLog -Destination $logDirectory
            Get-Content -LiteralPath $wixLog -Tail 60 | Write-Host
        }
    }
}
$hash = Get-FileHash -LiteralPath $package -Algorithm SHA256
'{0}  {1}' -f $hash.Hash, (Split-Path $package -Leaf) | Set-Content "$package.sha256"
Write-Host "Package created: $package"
