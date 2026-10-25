param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [switch]$RunCoreTests,
    [switch]$UseBuiltCore,
    [Parameter(Mandatory = $true)][string]$CoreProof
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $PSScriptRoot 'native_env.ps1')
$runtimeInput = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$cmake = 'D:/ValMAX/tools/native/python/Scripts/cmake.exe'
$python = 'D:/CodexLabs/max-mod-26.35.1/venv/Scripts/python.exe'
$ninja = 'D:/ValMAX/tools/native/python/Scripts/ninja.exe'
$hooksBuild = 'C:/ValMAXBuild/build/hooks-all-features'
$qtBuild = 'C:/ValMAXBuild/build/qt-all-features'
$CoreProof = (Resolve-Path -LiteralPath $CoreProof).Path
$coreEvidence = Get-Content -LiteralPath $CoreProof -Raw | ConvertFrom-Json
$expectedCoreSha = [string]$coreEvidence.dll.sha256
if ($expectedCoreSha -cnotmatch '^[a-f0-9]{64}$') { throw 'CoreProof must contain a canonical SHA-256 digest.' }
# Every proven core has a separate immutable input directory. Never replace the
# historical f29 snapshot or silently reuse it for another validation proof.
$inputs = Join-Path 'C:/ValMAXBuild/build' "all-feature-inputs-$expectedCoreSha"
function Invoke-CheckedNative([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Native build command failed: $LASTEXITCODE" }
}
if (-not $UseBuiltCore) {
    $cargoManifest = Join-Path $projectRoot 'native/Cargo.toml'
    Push-Location (Join-Path $projectRoot 'native')
    try {
        # A matching validation proof is mandatory even after a fresh build.
        Invoke-CheckedNative { cargo build --locked --offline --manifest-path $cargoManifest --release }
        # Production transfer operations share one named Windows mutex. Several
        # independent tests exercise that exact lock, so run their harnesses
        # serially to avoid reporting another fixture's lease as a product bug.
        if ($RunCoreTests) { Invoke-CheckedNative { cargo test --locked --offline --manifest-path $cargoManifest --release -- --test-threads=1 } }
    } finally { Pop-Location }
} elseif ($RunCoreTests) { throw 'RunCoreTests requires a core build; do not mutate a frozen core snapshot.' }
if (Test-Path -LiteralPath "$inputs/core-pair-proof.json") {
    Invoke-CheckedNative { & $python (Join-Path $PSScriptRoot 'snapshot_core_pair.py') --proof $CoreProof --output $inputs --reuse }
} else {
    if ((Test-Path -LiteralPath $inputs) -and (Get-ChildItem -LiteralPath $inputs -Force | Select-Object -First 1)) {
        throw 'The core snapshot directory is incomplete; preserve it for review and use a valid snapshot.'
    }
    Invoke-CheckedNative { & $python (Join-Path $PSScriptRoot 'snapshot_core_pair.py') --proof $CoreProof --output $inputs }
}
$coreLibrary = Join-Path $inputs 'valmax_core.dll.lib'
$qtSource = Join-Path $projectRoot 'native\qt'
$hooksSource = Join-Path $projectRoot 'native\hooks'
# Generators validate exact reviewed vendor/spec bytes and reversible QA pins.
# No CLI, environment or QML expected-hash override is accepted by the DLLs.
Invoke-CheckedNative { & $cmake -S $hooksSource -B $hooksBuild -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_MAKE_PROGRAM=$ninja" "-DVALMAX_BUILD_PYTHON=$python" "-DVALMAX_GHOST_RUNTIME_ROOT=$runtimeInput" "-DVALMAX_NOTIFICATION_RUNTIME_ROOT=$runtimeInput" "-DVALMAX_PRIVACY_RUNTIME_ROOT=$runtimeInput" }
# Explicit targets: retention is not built, linked or exercised by this step.
Invoke-CheckedNative { & $cmake --build $hooksBuild --target valmax_ghost valmax_notifications valmax_privacy }
Invoke-CheckedNative { & $cmake -S $qtSource -B $qtBuild -G Ninja -DCMAKE_BUILD_TYPE=Release "-DCMAKE_MAKE_PROGRAM=$ninja" -DQt6_DIR=C:/ValMAXBuild/Qt/lib/cmake/Qt6 -DVALMAX_REQUIRE_ALL_FEATURES=ON "-DVALMAX_CORE_LIBRARY=$coreLibrary" "-DVALMAX_GHOST_LIBRARY=$hooksBuild/valmax_ghost.lib" "-DVALMAX_NOTIFICATION_LIBRARY=$hooksBuild/valmax_notifications.lib" "-DVALMAX_PRIVACY_LIBRARY=$hooksBuild/valmax_privacy.lib" }
Invoke-CheckedNative { & $cmake --build $qtBuild }
$artifacts = @{}
foreach ($file in @("$inputs/valmax_core.dll", "$inputs/valmax_core.dll.lib", "$inputs/core-pair-proof.json", "$qtBuild/qml/ValMAX/Native/valmaxnativeplugin.dll", "$hooksBuild/valmax_ghost.dll", "$hooksBuild/valmax_notifications.dll", "$hooksBuild/valmax_privacy.dll", "$hooksBuild/ghost-pins.json", "$hooksBuild/notification-pins.json", "$hooksBuild/privacy-pins.json")) {
    $artifacts[$file] = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
}
$proof = [ordered]@{ schema = 1; compiled = $true; allFeatures = @('core','ghost','notifications','privacy'); runtimeRoot = $runtimeInput; hookBuild = $hooksBuild; qtBuild = $qtBuild; artifacts = $artifacts; retentionBuilt = $false; installedClientChanged = $false; readyRuntime = $false; earlyBootstrapGateVerified = $false }
$proof | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $projectRoot 'analysis/native-migration-v1/qt-all-features-build.json') -Encoding utf8
# A combined DLL is a component milestone, not a complete native installer.
