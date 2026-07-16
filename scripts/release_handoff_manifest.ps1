[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Write", "Verify")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$RootPath,

    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [Parameter(Mandatory = $true)]
    [ValidateSet("unsigned-app", "signed-app", "unsigned-installer")]
    [string]$Stage,

    [Parameter(Mandatory = $true)]
    [string]$SourceCommit,

    [Parameter(Mandatory = $true)]
    [string]$Tag,

    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$ExpectedManifestSha256 = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($SourceCommit -cnotmatch '^[0-9a-f]{40}$') {
    throw "The handoff source commit must be an exact lowercase Git commit ID."
}
if ($Tag -cnotmatch '^v\d+\.\d+\.\d+$' -or $Version -cnotmatch '^\d+\.\d+\.\d+$') {
    throw "The handoff tag and version must be exact semantic versions."
}
if ($Tag.Substring(1) -cne $Version) {
    throw "The handoff tag and version do not identify the same release."
}

$workspace = if ([string]::IsNullOrWhiteSpace($env:GITHUB_WORKSPACE)) {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
} else {
    [IO.Path]::GetFullPath($env:GITHUB_WORKSPACE)
}
$handoffRoot = [IO.Path]::GetFullPath((Join-Path $workspace ".handoff"))
$root = [IO.Path]::GetFullPath($RootPath)
$manifestFile = [IO.Path]::GetFullPath($ManifestPath)
$handoffPrefix = $handoffRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$rootPrefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar

if (-not $rootPrefix.StartsWith($handoffPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The handoff payload root must remain below the workspace handoff directory."
}
if (-not $manifestFile.StartsWith($handoffPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $manifestFile.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The handoff manifest must be outside its payload and below the handoff directory."
}
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "The handoff payload root does not exist."
}

function Assert-OrdinaryPathChain([string]$Path, [string]$StopAt) {
    $current = [IO.Path]::GetFullPath($Path)
    $stop = [IO.Path]::GetFullPath($StopAt)
    while ($true) {
        if (-not (Test-Path -LiteralPath $current)) {
            throw "Every handoff path ancestor must already exist."
        }
        $currentItem = Get-Item -LiteralPath $current -Force
        if (($currentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a handoff path with a reparse-point ancestor."
        }
        if ($current -ceq $stop) {
            break
        }
        $parent = [IO.Path]::GetFullPath((Split-Path -Parent $current))
        if ($parent -ceq $current -or
            -not ($current + [IO.Path]::DirectorySeparatorChar).StartsWith(
                $stop.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            )) {
            throw "The handoff path chain escaped its approved root."
        }
        $current = $parent
    }
}

Assert-OrdinaryPathChain -Path $root -StopAt $handoffRoot
$manifestParent = Split-Path -Parent $manifestFile
Assert-OrdinaryPathChain -Path $manifestParent -StopAt $handoffRoot

$rootItem = Get-Item -LiteralPath $root -Force
$treeItems = @(Get-ChildItem -LiteralPath $root -Force -Recurse)
if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
    @($treeItems | Where-Object {
        ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    }).Count -ne 0) {
    throw "Refusing a handoff tree containing a reparse point."
}

$actualFiles = @(
    $treeItems |
        Where-Object { -not $_.PSIsContainer } |
        ForEach-Object {
            $fileFullPath = [IO.Path]::GetFullPath($_.FullName)
            if (-not $fileFullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "The handoff file escaped its payload root."
            }
            $relativePath = $fileFullPath.Substring($rootPrefix.Length).Replace('\', '/')
            if ($relativePath -match '(^|/)\.\.(/|$)' -or $relativePath.StartsWith('/')) {
                throw "The handoff contains an invalid relative path."
            }
            [pscustomobject][ordered]@{
                path = $relativePath
                bytes = [long]$_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        } |
        Sort-Object -Property path
)
if ($actualFiles.Count -eq 0) {
    throw "The handoff payload may not be empty."
}

if ($Mode -eq "Write") {
    if (Test-Path -LiteralPath $manifestFile) {
        throw "Refusing to overwrite an existing handoff manifest."
    }
    $manifestParentItem = Get-Item -LiteralPath $manifestParent -Force
    if (($manifestParentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to write a manifest through a reparse point."
    }
    $manifest = [ordered]@{
        schema_version = 1
        stage = $Stage
        source_commit = $SourceCommit
        tag = $Tag
        version = $Version
        files = $actualFiles
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestFile -Encoding utf8
    (Get-FileHash -LiteralPath $manifestFile -Algorithm SHA256).Hash.ToLowerInvariant()
    return
}

if ($ExpectedManifestSha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw "An exact lowercase expected handoff manifest SHA-256 is required."
}
if (-not (Test-Path -LiteralPath $manifestFile -PathType Leaf)) {
    throw "The handoff manifest does not exist."
}
$manifestItem = Get-Item -LiteralPath $manifestFile -Force
if (($manifestItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing a handoff manifest that is a reparse point."
}
$actualManifestSha256 = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualManifestSha256 -cne $ExpectedManifestSha256) {
    throw "The handoff manifest failed its expected SHA-256 check."
}

$manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or
    $manifest.stage -cne $Stage -or
    $manifest.source_commit -cne $SourceCommit -or
    $manifest.tag -cne $Tag -or
    $manifest.version -cne $Version) {
    throw "The handoff manifest metadata does not match this release stage."
}
$manifestFiles = @($manifest.files)
if ($actualFiles.Count -ne $manifestFiles.Count) {
    throw "The handoff payload file set does not match its manifest."
}

for ($index = 0; $index -lt $actualFiles.Count; $index += 1) {
    $actual = $actualFiles[$index]
    $expected = $manifestFiles[$index]
    if ([string]$expected.path -cne $actual.path -or
        [long]$expected.bytes -ne $actual.bytes -or
        [string]$expected.sha256 -cne $actual.sha256 -or
        [string]$expected.sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw "The handoff payload failed file-by-file SHA-256 verification."
    }
}
