[CmdletBinding()]
param(
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version = 'v0.1.3',
    [string]$BuildDirectory,
    [string]$OutputDirectory,
    [string]$StagingDirectory
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$stateRoot = Join-Path $root '.local-build'
if (!$BuildDirectory) { $BuildDirectory = Join-Path $stateRoot 'build-full' }
if (!$OutputDirectory) { $OutputDirectory = Join-Path $stateRoot "packages/$Version" }
if (!$StagingDirectory) { $StagingDirectory = Join-Path $stateRoot "release-stage/$Version" }
$BuildDirectory = [IO.Path]::GetFullPath($BuildDirectory)
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$StagingDirectory = [IO.Path]::GetFullPath($StagingDirectory)
$packageName = "blender-island-$Version-windows-x64"
$programSource = Join-Path $BuildDirectory 'bin/Release'
$program = Join-Path $StagingDirectory $packageName
$package = Join-Path $OutputDirectory "$packageName.zip"
$releaseNotes = Join-Path $PSScriptRoot "RELEASE-$Version.md"

function Assert-NewWorkspaceDirectory([string]$Path) {
    $allowedPrefix = [IO.Path]::GetFullPath($stateRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$Path.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release staging/output must be a new directory inside $stateRoot`: $Path"
    }
    # Reject junction/symlink ancestors before creating or copying any files.
    $ancestor = $Path
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Release destination contains a junction or symbolic link: $ancestor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($ancestor)
        if ($parent -eq $ancestor) { break }
        $ancestor = $parent
    }
    if (Test-Path -LiteralPath $Path) {
        if (!(Test-Path -LiteralPath $Path -PathType Container) -or
            @(Get-ChildItem -LiteralPath $Path -Force).Count) {
            throw "Refusing to overwrite a nonempty release destination: $Path"
        }
    }
}

Assert-NewWorkspaceDirectory $OutputDirectory
Assert-NewWorkspaceDirectory $StagingDirectory
if ($OutputDirectory -eq $StagingDirectory -or
    $OutputDirectory.StartsWith($StagingDirectory.TrimEnd('\', '/') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $StagingDirectory.StartsWith($OutputDirectory.TrimEnd('\', '/') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Release staging and output directories must not contain one another.'
}
$sourcePrefix = $programSource.TrimEnd('\', '/') + '\'
if ($OutputDirectory.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $StagingDirectory.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Release staging/output must not be inside the compiled program directory.'
}
if (!(Test-Path -LiteralPath (Join-Path $programSource 'blender.exe') -PathType Leaf)) {
    throw "Compiled Blender is missing: $programSource\blender.exe"
}
if (!(Test-Path -LiteralPath $releaseNotes -PathType Leaf)) { throw "Release notes are missing: $releaseNotes" }

$sourceCommit = (& git -C $root rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -notmatch '^[0-9a-f]{40}$') { throw 'Cannot determine full source commit.' }
$dirty = @(& git -C $root status --porcelain --untracked-files=normal)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect source worktree.' }
if ($dirty.Count) { throw 'Commit the source changes before packaging so version.json identifies the exact release source.' }

New-Item -ItemType Directory -Force -Path $OutputDirectory, $StagingDirectory, $program | Out-Null
Write-Host "Staging freshly compiled Blender from $programSource"
foreach ($file in Get-ChildItem -LiteralPath $programSource -File -Recurse -Force) {
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Unexpected symbolic link in compiled runtime: $($file.FullName)"
    }
    $relative = $file.FullName.Substring($sourcePrefix.Length)
    # Never package a developer's user configuration or stale Python bytecode.
    if ($relative -match '^(portable|config)[\\/]' -or
        $relative -match '^[0-9]+\.[0-9]+[\\/]config[\\/]' -or
        $relative -match '(^|[\\/])__pycache__[\\/]' -or $file.Extension -eq '.pyc') { continue }
    # The official launcher is hardcoded to blender.exe. Users launch the renamed
    # executable directly; do not ship a launcher that points at a missing file.
    if ($relative -ieq 'blender-launcher.exe') { continue }
    if ($relative -ieq 'blender.exe') { $relative = 'blender-island.exe' }
    $destination = Join-Path $program $relative
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
    if ($file.DirectoryName -eq $programSource -and $file.Extension -ieq '.cmd') {
        # Preserve Blender's diagnostic helpers with the release executable name.
        $text = [IO.File]::ReadAllText($destination).Replace('blender.exe', 'blender-island.exe')
        [IO.File]::WriteAllText($destination, $text, [Text.UTF8Encoding]::new($false))
    }
}

$versionDirectories = @(Get-ChildItem -LiteralPath $program -Directory | Where-Object { $_.Name -match '^[0-9]+\.[0-9]+$' })
if ($versionDirectories.Count -ne 1) { throw 'Expected exactly one versioned Blender runtime directory.' }
$runtimeDirectory = $versionDirectories[0].FullName
$python = Join-Path $runtimeDirectory 'python/bin/python.exe'
if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Bundled Python is missing; cannot verify ZIP CRCs.' }

# Check the installed built-ins, not just the executable timestamp or Git hash.
$builtinFiles = @(
    'bl_operators/island_inbetween.py', 'bl_operators/__init__.py',
    'bl_ui/space_island_inbetween.py', 'bl_ui/__init__.py',
    'bl_ui/space_dopesheet.py', 'bl_ui/space_graph.py'
)
foreach ($relative in $builtinFiles) {
    $sourceFile = Join-Path $root "scripts/startup/$relative"
    $installedFile = Join-Path $runtimeDirectory "scripts/startup/$relative"
    if (!(Test-Path -LiteralPath $installedFile) -or
        (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash) {
        throw "Installed script differs from the committed source; rebuild/install first: $relative"
    }
}

$portable = Join-Path $program 'portable'
$config = Join-Path $portable 'config'
$docs = Join-Path $program 'docs'
New-Item -ItemType Directory -Path $config, $docs | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'COPYING') -Destination (Join-Path $program 'COPYING')
foreach ($name in @('FINGER-PICKER.md', 'CURSOR-WORLD-SNAP.md', 'SMART-CONSTRAINT.md',
                    'SMART-ROTATION-CONVERSION.md', 'TRANSFORM-CHANNELS-AND-HANDLES.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $docs
}
Copy-Item -LiteralPath $releaseNotes -Destination $docs
$readme = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Raw -Encoding UTF8
$guideMatch = [regex]::Match($readme, '(?ms)^## 插入 / 移除中间帧\r?\n(.*?)^## 本地编译与启动')
if (!$guideMatch.Success) { throw 'Cannot extract the inbetween user guide from README.md.' }
$guide = "# 插入 / 移除中间帧`n" + $guideMatch.Groups[1].Value
$guide = [regex]::Replace($guide, '(?m)^回归测试：.*\r?\n?', '')
[IO.File]::WriteAllText((Join-Path $docs 'INBETWEEN.md'), $guide.TrimEnd() + "`n", [Text.UTF8Encoding]::new($false))

$metadataPath = Join-Path $StagingDirectory 'compiled-metadata.json'
$validationScript = Join-Path $StagingDirectory 'validate-release.py'
@'
import json
import os
from pathlib import Path
import sys
import bpy

metadata_path, config_path = sys.argv[sys.argv.index('--') + 1:]
assert bpy.context.scene is not None
assert hasattr(bpy.types, 'ANIM_OT_island_inbetween')
assert hasattr(bpy.types, 'VIEW3D_PT_island_finger_picker')
assert bpy.context.scene.island_inbetween.frames == 1
assert bpy.context.window_manager.keyconfigs.active.name == 'Blender'
actual_config = os.path.normcase(os.path.realpath(bpy.utils.user_resource('CONFIG')))
assert actual_config == os.path.normcase(os.path.realpath(config_path)), actual_config
assert bpy.ops.wm.save_userpref() == {'FINISHED'}
assert (Path(config_path) / 'userpref.blend').is_file()

def decode(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)

metadata = {
    'blender_version': bpy.app.version_string,
    'build_hash': decode(bpy.app.build_hash),
    'build_branch': decode(bpy.app.build_branch),
    'build_date': decode(bpy.app.build_date),
    'build_time': decode(bpy.app.build_time),
    'default_keymap': bpy.context.window_manager.keyconfigs.active.name,
}
Path(metadata_path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('ISLAND_RELEASE_SMOKE_OK', json.dumps(metadata))
'@ | Set-Content -LiteralPath $validationScript -Encoding UTF8

$environmentNames = @('BLENDER_USER_RESOURCES', 'BLENDER_USER_CONFIG', 'BLENDER_USER_SCRIPTS',
    'BLENDER_USER_DATAFILES', 'BLENDER_USER_EXTENSIONS', 'BLENDER_SYSTEM_RESOURCES',
    'BLENDER_SYSTEM_SCRIPTS', 'BLENDER_SYSTEM_DATAFILES', 'BLENDER_SYSTEM_PYTHON',
    'PYTHONPATH', 'PYTHONHOME', 'PYTHONDONTWRITEBYTECODE')
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
try {
    [Environment]::SetEnvironmentVariable('BLENDER_USER_RESOURCES', $portable, 'Process')
    [Environment]::SetEnvironmentVariable('BLENDER_USER_CONFIG', $config, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '1', 'Process')
    $executable = Join-Path $program 'blender-island.exe'
    & $executable --background --factory-startup --python-exit-code 1 --python $validationScript -- $metadataPath $config 2>&1 |
        Tee-Object -FilePath (Join-Path $StagingDirectory 'release-smoke.log')
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $metadataPath)) {
        throw 'Packaged Blender failed its background startup/default-preferences check.'
    }
    $compiled = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($compiled.build_hash -notmatch '^[0-9a-f]{7,40}$' -or
        !$sourceCommit.StartsWith($compiled.build_hash, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Compiled hash $($compiled.build_hash) does not match source commit $sourceCommit. Rebuild before packaging."
    }
    $exeHash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    $versionInfo = [ordered]@{
        product = 'blender-island'
        release_version = $Version
        application_brand_version = 'v0.1'
        source_repository = 'https://github.com/w813117438/personal-blender-island'
        source_commit = $sourceCommit
        blender_version = $compiled.blender_version
        compiled_build_hash = $compiled.build_hash
        compiled_build_branch = $compiled.build_branch
        compiled_build_date = $compiled.build_date
        compiled_build_time = $compiled.build_time
        executable = 'blender-island.exe'
        executable_sha256 = $exeHash
        default_keymap = $compiled.default_keymap
        portable_config = 'portable/config'
        packaged_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    }
    $versionInfo | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $program 'version.json') -Encoding UTF8
    @"
blender-island $Version (Blender $($compiled.blender_version))

完整解压后运行 blender-island.exe。
中间帧入口：时间轴 / 摄影表 / 曲线编辑器顶栏 → 中间帧。
3D 视口侧栏：N → 动画工具。
使用说明：docs/INBETWEEN.md 和 docs/FINGER-PICKER.md。
本包使用 Blender 默认操作方案，配置独立保存在 portable/config。
源码提交、程序构建标识及 SHA256 记录在 version.json。
源码：https://github.com/w813117438/personal-blender-island
许可证：COPYING。
"@ | Set-Content -LiteralPath (Join-Path $program 'README.txt') -Encoding UTF8

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($program, $package, [IO.Compression.CompressionLevel]::Optimal, $true)
    # Read every compressed entry and verify its CRC using Blender's bundled Python.
    $archiveCheck = 'import sys,zipfile; p=sys.argv[1]; z=zipfile.ZipFile(p); bad=z.testzip(); assert bad is None, bad; print("ZIP_CRC_OK", len(z.infolist()), "entries"); z.close()'
    & $python -I -S -c $archiveCheck $package
    if ($LASTEXITCODE -ne 0) { throw 'Release ZIP integrity/CRC validation failed.' }
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
}

$packageHash = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
"$packageHash  $([IO.Path]::GetFileName($package))" | Set-Content -LiteralPath "$package.sha256" -Encoding ascii
Write-Host "Release package: $package"
Write-Host "SHA256: $packageHash"
Write-Host "Source commit: $sourceCommit"
Write-Host "Staged program: $program"
