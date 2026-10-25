$ErrorActionPreference = 'Stop'
$webRoot = 'C:/ValMAXBuild/deps/webview2'
$webVersion = '155.0.4283.60'
$webCab = Join-Path $webRoot "Microsoft.WebView2.FixedVersionRuntime.$webVersion.x64.cab"
$webRuntime = Join-Path $webRoot "extracted/Microsoft.WebView2.FixedVersionRuntime.$webVersion.x64"
$webExpectedSha = '5c5b38df347b2d05b51ef01640ed5ba48511499e4310e71e679e620aaff2ad40'
if ((Get-FileHash -LiteralPath $webCab -Algorithm SHA256).Hash.ToLowerInvariant() -ne $webExpectedSha) { throw 'cab_hash_mismatch' }
$webNames = @('msedgewebview2.exe', 'msedge.dll', 'msedge_elf.dll', 'EBWebView/x64/EmbeddedBrowserWebView.dll')
$webSignatures = @()
foreach ($webName in @((Split-Path $webCab -Leaf)) + $webNames) {
    $webFile = if ($webName.EndsWith('.cab')) { Get-Item -LiteralPath $webCab } else { Get-Item -LiteralPath (Join-Path $webRuntime $webName) }
    $webSignature = Get-AuthenticodeSignature -LiteralPath $webFile.FullName
    if ($webSignature.Status -ne 'Valid' -or $webSignature.SignerCertificate.Subject -notmatch '^CN=Microsoft Corporation, O=Microsoft Corporation,') { throw "invalid_microsoft_signature:$webName" }
    if (-not $webName.EndsWith('.cab')) {
        if ($webFile.VersionInfo.FileVersion -ne $webVersion) { throw "version_mismatch:$webName" }
        $webStream = [IO.File]::OpenRead($webFile.FullName)
        try {
            $webReader = [IO.BinaryReader]::new($webStream)
            if ($webReader.ReadUInt16() -ne 0x5A4D) { throw 'missing_mz' }
            $webStream.Position = 0x3C
            $webPeOffset = $webReader.ReadUInt32()
            $webStream.Position = $webPeOffset
            if ($webReader.ReadUInt32() -ne 0x4550 -or $webReader.ReadUInt16() -ne 0x8664) { throw "architecture_mismatch:$webName" }
        } finally { $webStream.Dispose() }
    }
    $webSignatures += [ordered]@{ name = $webName; status = 'Valid'; signer = $webSignature.SignerCertificate.Subject; thumbprint = $webSignature.SignerCertificate.Thumbprint; version = $webFile.VersionInfo.FileVersion; sha256 = (Get-FileHash -LiteralPath $webFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$webFiles = @(Get-ChildItem -LiteralPath $webRuntime -File -Recurse | Sort-Object FullName | ForEach-Object {
    if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'unexpected_reparse_point' }
    [pscustomobject][ordered]@{ path = [IO.Path]::GetRelativePath($webRuntime, $_.FullName).Replace('\', '/'); bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$webBytes = ($webFiles | Measure-Object -Property bytes -Sum).Sum
if ($webFiles.Count -ne 256 -or $webBytes -ne 705188190) { throw 'extraction_count_or_size_mismatch' }
$webResult = [ordered]@{ schema = 1; verified = $true; version = $webVersion; architecture = 'x64'; cab = $webCab; runtime = $webRuntime; cabBytes = (Get-Item -LiteralPath $webCab).Length; cabSha256 = $webExpectedSha; officialCabShaPublished = $false; signatures = $webSignatures; extractedFiles = $webFiles.Count; extractedBytes = $webBytes; files = $webFiles; runtimeStarted = $false; userInstallationModified = $false }
$webJson = $webResult | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $webRoot 'verification.json'), $webJson)
[IO.File]::WriteAllText('D:/ValMAX/analysis/native-migration-v1/qt-webview2-bundle-proof.json', $webJson)
$webDownload = Get-Content -LiteralPath (Join-Path $webRoot 'download.json') -Raw | ConvertFrom-Json
$webDownload.signatureVerified = $true
$webDownload.extracted = $true
$webDownload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $webRoot 'download.json')
[ordered]@{ verified = $true; version = $webVersion; signatures = $webSignatures.Count; files = $webFiles.Count; extractedBytes = $webBytes } | ConvertTo-Json -Compress
