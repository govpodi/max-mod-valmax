param(
    [Parameter(Mandatory=$true)][string]$CoreDll,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [string]$BuildDirectory = 'C:\ValMAXBuild\build\transfer-ffi'
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
. (Join-Path $repoRoot 'native\tooling\native_env.ps1')
$corePath = (Resolve-Path -LiteralPath $CoreDll).Path
$testBuild = [IO.Path]::GetFullPath($BuildDirectory)
if (-not $corePath.StartsWith('C:\ValMAXBuild\', [StringComparison]::OrdinalIgnoreCase) -or
    -not $testBuild.StartsWith('C:\ValMAXBuild\build\', [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFileName($corePath) -ne 'valmax_core.dll' -or
    $ExpectedSha256 -cnotmatch '^[a-f0-9]{64}$') { throw 'Invalid owned test paths/hash' }
$before = (Get-FileHash -LiteralPath $corePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($before -cne $ExpectedSha256) { throw 'Core hash mismatch before FFI test' }
New-Item -ItemType Directory -Path $testBuild -Force | Out-Null
$exe = Join-Path $testBuild 'valmax_transfer_ffi_test.exe'
$obj = Join-Path $testBuild 'valmax_transfer_ffi_test.obj'
$compileLog = Join-Path $testBuild 'compile.log'
$runLog = Join-Path $testBuild 'result.json'
$compileArgs = @('/nologo', '/std:c++20', '/EHsc', '/MT', '/W4', '/WX', '/permissive-', '/utf-8', '/O2',
    '/DWIN32_LEAN_AND_MEAN', '/DNOMINMAX', (Join-Path $PSScriptRoot 'ffi_test.cpp'), "/Fo$obj", "/Fe$exe", '/link', '/INCREMENTAL:NO')
& cl.exe @compileArgs *> $compileLog
if ($LASTEXITCODE -ne 0) { Get-Content -LiteralPath $compileLog; throw 'FFI client compile failed' }
& $exe $corePath > $runLog
$testExit = $LASTEXITCODE
$after = (Get-FileHash -LiteralPath $corePath -Algorithm SHA256).Hash.ToLowerInvariant()
Get-Content -LiteralPath $runLog
if ($testExit -ne 0 -or $before -cne $after) { throw 'FFI test failed or DLL changed' }
$result = Get-Content -Raw -LiteralPath $runLog | ConvertFrom-Json
$result | Add-Member -NotePropertyName coreDll -NotePropertyValue $corePath
$result | Add-Member -NotePropertyName coreSha256 -NotePropertyValue $before
$result | Add-Member -NotePropertyName coreUnchanged -NotePropertyValue ($before -ceq $after)
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $runLog -Encoding utf8
