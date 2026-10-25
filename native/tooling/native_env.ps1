# Development shell only; no global environment changes or end-user prerequisites.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'msvc_env.ps1')
$env:CARGO_HOME = 'C:\ValMAXBuild\cargo'
$env:RUSTUP_HOME = 'C:\ValMAXBuild\rustup'
$env:CARGO_TARGET_DIR = 'C:\ValMAXBuild\target'
$env:TEMP = 'C:\ValMAXBuild\tmp'
$env:TMP = $env:TEMP
$env:PATH = "C:\ValMAXBuild\cargo\bin;D:\ValMAX\tools\native\python\Scripts;$env:PATH"
$env:CMAKE_PREFIX_PATH = 'C:\ValMAXBuild\Qt'
