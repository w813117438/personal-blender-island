[CmdletBinding()]
param(
    [string]$BlenderExecutable,
    [string]$LogDirectory
)
$ErrorActionPreference = 'Stop'
$sourceDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (!$BlenderExecutable) {
    $BlenderExecutable = Join-Path $sourceDir '.local-build/build-full/bin/Release/blender.exe'
}
if (!$LogDirectory) { $LogDirectory = Join-Path $sourceDir '.local-build/logs' }
if (!(Test-Path -LiteralPath $BlenderExecutable -PathType Leaf)) { throw 'Compiled Blender is missing.' }
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$startupCheck = "import bpy; assert hasattr(bpy.types, 'ANIM_OT_island_inbetween'); assert hasattr(bpy.types, 'CONSTRAINT_OT_island_childof_space_switch'); assert hasattr(bpy.types, 'VIEW3D_PT_island_space_switch'); assert hasattr(bpy.types, 'GRAPH_PT_island_inbetween'); assert hasattr(bpy.types, 'DOPESHEET_PT_island_inbetween_popover'); assert hasattr(bpy.types, 'VIEW3D_PT_island_finger_picker'); assert bpy.context.scene.island_inbetween.frames == 1; assert bpy.context.scene.island_inbetween.scope == 'CONTEXT'; assert {item.identifier: item.value for item in bpy.context.scene.island_inbetween.bl_rna.properties['scope'].enum_items} == {'CONTEXT': 0, 'SCENE': 1, 'EDITOR': 2}; assert bpy.context.scene.island_space_switch.mode == 'CURRENT'; print('Builtin registration OK', bpy.app.build_hash.decode())"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $BlenderExecutable --background --factory-startup --python-exit-code 1 --python-expr $startupCheck 2>&1 |
        ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" } } |
        Tee-Object -FilePath (Join-Path $LogDirectory 'builtin-startup-tests.log')
    $startupExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
}
if ($startupExit -ne 0) { throw 'Built-in feature registration failed.' }
$tests = @(
    'animation_inbetween',
    'childof_space_switch',
    'finger_picker',
    'graph_selected_channels',
    'transform_handle_preferences',
    'view3d_cursor_world_snap',
    'smart_constraint',
    'smart_rotation_conversion'
)
foreach ($test in $tests) {
    $scriptPath = Join-Path $sourceDir "tests/python/$test.py"
    Write-Host "Testing $test"
    $ErrorActionPreference = 'Continue'
    try {
        & $BlenderExecutable --background --factory-startup --python-exit-code 1 --python $scriptPath 2>&1 |
            ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" } } |
            Tee-Object -FilePath (Join-Path $LogDirectory "$test-tests.log")
        $testExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($testExit -ne 0) { throw "$test failed (exit $testExit)." }
}
Write-Host 'All personal Blender regression suites passed.'
