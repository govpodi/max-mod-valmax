param(
    [string]$ToolchainRoot = 'C:\ValMAXBuild\msvc'
)
# Dot-source this file in the current PowerShell before CMake/Ninja/Cargo.
# Only the current process environment is modified. No machine/user PATH edits.
$valmaxMsvcRoot = [System.IO.Path]::GetFullPath($ToolchainRoot)
$valmaxVcVersion = '14.44.35207'
$valmaxSdkVersion = '10.0.26100.0'
$valmaxVcRoot = Join-Path $valmaxMsvcRoot "VC\Tools\MSVC\$valmaxVcVersion"
$valmaxSdkRoot = Join-Path $valmaxMsvcRoot 'Windows Kits\10'
$valmaxVcBin = Join-Path $valmaxVcRoot 'bin\Hostx64\x64'
$valmaxSdkBin = Join-Path $valmaxSdkRoot "bin\$valmaxSdkVersion\x64"
foreach ($valmaxRequiredFile in @(
    (Join-Path $valmaxVcBin 'cl.exe'),
    (Join-Path $valmaxVcBin 'link.exe'),
    (Join-Path $valmaxSdkBin 'rc.exe'),
    (Join-Path $valmaxSdkBin 'mt.exe'),
    (Join-Path $valmaxVcRoot 'include\iostream'),
    (Join-Path $valmaxSdkRoot "Include\$valmaxSdkVersion\um\Windows.h"),
    (Join-Path $valmaxSdkRoot "Lib\$valmaxSdkVersion\um\x64\kernel32.lib"),
    (Join-Path $valmaxSdkRoot "Lib\$valmaxSdkVersion\ucrt\x64\ucrt.lib")
)) {
    if (-not (Test-Path -LiteralPath $valmaxRequiredFile -PathType Leaf)) {
        throw "Incomplete ValMAX native toolchain: $valmaxRequiredFile"
    }
}
$env:PATH = "$valmaxVcBin;$valmaxSdkBin;$env:PATH"
$env:INCLUDE = (@(
    (Join-Path $valmaxVcRoot 'include'),
    (Join-Path $valmaxSdkRoot "Include\$valmaxSdkVersion\ucrt"),
    (Join-Path $valmaxSdkRoot "Include\$valmaxSdkVersion\shared"),
    (Join-Path $valmaxSdkRoot "Include\$valmaxSdkVersion\um"),
    (Join-Path $valmaxSdkRoot "Include\$valmaxSdkVersion\winrt"),
    (Join-Path $valmaxSdkRoot "Include\$valmaxSdkVersion\cppwinrt")
) -join ';')
$env:LIB = (@(
    (Join-Path $valmaxVcRoot 'lib\x64'),
    (Join-Path $valmaxSdkRoot "Lib\$valmaxSdkVersion\ucrt\x64"),
    (Join-Path $valmaxSdkRoot "Lib\$valmaxSdkVersion\um\x64")
) -join ';')
$env:LIBPATH = $env:LIB
$env:VCToolsInstallDir = "$valmaxVcRoot\"
$env:VCINSTALLDIR = (Join-Path $valmaxMsvcRoot 'VC\')
$env:VCToolsVersion = $valmaxVcVersion
$env:WindowsSdkDir = "$valmaxSdkRoot\"
$env:WindowsSDKVersion = "$valmaxSdkVersion\"
$env:WindowsSdkBinPath = (Join-Path $valmaxSdkRoot 'bin\')
$env:WindowsSdkVerBinPath = (Join-Path $valmaxSdkRoot "bin\$valmaxSdkVersion\")
$env:UniversalCRTSdkDir = "$valmaxSdkRoot\"
$env:UCRTVersion = $valmaxSdkVersion
$env:VSCMD_ARG_TGT_ARCH = 'x64'
$env:VSCMD_ARG_HOST_ARCH = 'x64'
$env:VSCMD_VER = '17.14.42'
$env:PreferredToolArchitecture = 'x64'
$env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = (Join-Path $valmaxVcBin 'link.exe')
$env:CC_x86_64_pc_windows_msvc = (Join-Path $valmaxVcBin 'cl.exe')
$env:CXX_x86_64_pc_windows_msvc = (Join-Path $valmaxVcBin 'cl.exe')
$env:AR_x86_64_pc_windows_msvc = (Join-Path $valmaxVcBin 'lib.exe')
$env:CMAKE_GENERATOR = 'Ninja'
$env:VALMAX_MSVC_ROOT = $valmaxMsvcRoot
