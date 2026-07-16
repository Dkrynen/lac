[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP) -or
    [string]::IsNullOrWhiteSpace($env:APPDATA)) {
    throw "The eSigner cleanup retry requires an ephemeral GitHub Windows runner."
}

$sessionMarker = Join-Path $env:RUNNER_TEMP "esigner-cka-session.marker"
if (-not (Test-Path -LiteralPath $sessionMarker)) {
    return
}
if (-not (Test-Path -LiteralPath $sessionMarker -PathType Leaf)) {
    throw "Refusing cleanup without an ordinary eSigner ownership marker."
}
$marker = Get-Item -LiteralPath $sessionMarker -Force
if (($marker.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing cleanup with a reparse-point session marker."
}

$requestedSubject = ([string]$env:ESIGNER_CERTIFICATE_SUBJECT).Trim()
$requestedThumbprint = ([string]$env:ESIGNER_CERTIFICATE_THUMBPRINT -replace '\s', '').ToUpperInvariant()
if ($requestedSubject.Length -gt 512 -or $requestedSubject -cnotmatch '^[\x20-\x7e]+$' -or
    $requestedThumbprint -cnotmatch '^[0-9A-F]{40}$') {
    throw "The eSigner cleanup retry requires the exact approved public certificate identity."
}

$archivePath = Join-Path $env:RUNNER_TEMP "SSL.COM-eSigner-CKA_1.0.6.zip"
$extractRoot = Join-Path $env:RUNNER_TEMP "esigner-cka-package"
$installRoot = Join-Path $env:RUNNER_TEMP "esigner-cka-install"
$masterKeyPath = Join-Path $env:RUNNER_TEMP "esigner-cka-master.key"
$sessionRoot = Join-Path $env:APPDATA "eSignerCKA"
$ckaTool = Join-Path $installRoot "eSignerCKATool.exe"
$cleanupFailures = [Collections.Generic.List[string]]::new()

function Remove-ExactTree([string]$Path, [string]$Parent, [string]$Leaf) {
    $expected = [IO.Path]::GetFullPath((Join-Path $Parent $Leaf))
    $actual = [IO.Path]::GetFullPath($Path)
    if ($actual -cne $expected) {
        throw "Refusing an unexpected cleanup retry path."
    }
    if (-not (Test-Path -LiteralPath $actual)) {
        return
    }
    $item = Get-Item -LiteralPath $actual -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to recursively remove a reparse point."
    }
    $nestedReparsePoints = @(Get-ChildItem -LiteralPath $actual -Force -Recurse | Where-Object {
        ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    })
    if ($nestedReparsePoints.Count -gt 0) {
        throw "Refusing to recursively remove a tree containing a reparse point."
    }
    Remove-Item -LiteralPath $actual -Recurse -Force
}

if (Test-Path -LiteralPath $ckaTool -PathType Leaf) {
    try {
        $ckaToolItem = Get-Item -LiteralPath $ckaTool -Force
        if (($ckaToolItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a reparse-point eSigner CKA tool."
        }
        & $ckaTool unload *> $null
        if ($LASTEXITCODE -ne 0) {
            $cleanupFailures.Add("unload")
        }
    } catch {
        $cleanupFailures.Add("unload")
    }
}

if ($cleanupFailures.Count -gt 0) {
    throw "The retry could not unload eSigner CKA; owned material and the ownership marker were retained."
}

$ownedCertificates = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object {
    $_.Thumbprint -eq $requestedThumbprint -and $_.Subject -ceq $requestedSubject
})
foreach ($ownedCertificate in $ownedCertificates) {
    try {
        Remove-Item -LiteralPath "Cert:\CurrentUser\My\$($ownedCertificate.Thumbprint)" -Force
    } catch {
        $cleanupFailures.Add("certificate")
    }
}

if (Test-Path -LiteralPath $masterKeyPath) {
    try {
        $masterKey = Get-Item -LiteralPath $masterKeyPath -Force
        if (($masterKey.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a reparse-point master key."
        }
        Remove-Item -LiteralPath $masterKeyPath -Force
    } catch {
        $cleanupFailures.Add("master-key")
    }
}

foreach ($tree in @(
    @{ Path = $extractRoot; Parent = $env:RUNNER_TEMP; Leaf = "esigner-cka-package" },
    @{ Path = $installRoot; Parent = $env:RUNNER_TEMP; Leaf = "esigner-cka-install" },
    @{ Path = $sessionRoot; Parent = $env:APPDATA; Leaf = "eSignerCKA" }
)) {
    try {
        Remove-ExactTree -Path $tree.Path -Parent $tree.Parent -Leaf $tree.Leaf
    } catch {
        $cleanupFailures.Add("session-tree")
    }
}

if (Test-Path -LiteralPath $archivePath) {
    try {
        $archive = Get-Item -LiteralPath $archivePath -Force
        if (($archive.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a reparse-point package archive."
        }
        Remove-Item -LiteralPath $archivePath -Force
    } catch {
        $cleanupFailures.Add("package")
    }
}

if ($cleanupFailures.Count -gt 0) {
    throw "One or more retry eSigner CKA cleanup operations failed; the ownership marker was retained."
}
Remove-Item -LiteralPath $sessionMarker -Force
