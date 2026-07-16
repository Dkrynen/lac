[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Application", "Installer")]
    [string]$ArtifactKind,

    [Parameter(Mandatory = $true)]
    [string]$ArtifactPath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

if ($ExpectedVersion -cnotmatch '^\d+\.\d+\.\d+$') {
    throw "The signing target version must be an exact semantic version."
}
if ([string]::IsNullOrWhiteSpace($env:GITHUB_WORKSPACE) -or
    [string]::IsNullOrWhiteSpace($env:RUNNER_TEMP) -or
    [string]::IsNullOrWhiteSpace($env:APPDATA)) {
    throw "The signing helper requires an ephemeral GitHub Windows runner."
}

$workspace = [IO.Path]::GetFullPath($env:GITHUB_WORKSPACE)
$handoffRoot = [IO.Path]::GetFullPath((Join-Path $workspace ".handoff"))
$handoffPrefix = $handoffRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$artifact = [IO.Path]::GetFullPath($ArtifactPath)
if (-not $artifact.StartsWith($handoffPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
    throw "The signing target must be an existing handoff artifact."
}
$artifactItem = Get-Item -LiteralPath $artifact -Force
if (($artifactItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing to sign a reparse-point artifact."
}
$preSigningSignature = Get-AuthenticodeSignature -LiteralPath $artifact
if ($preSigningSignature.Status -ne "NotSigned") {
    throw "The handoff target must contain no Authenticode signature before signing."
}
$artifactRelativePath = [IO.Path]::GetRelativePath($handoffRoot, $artifact).Replace('/', '\')
if ($ArtifactKind -eq "Application") {
    if ($artifactRelativePath -cnotmatch '^[^\\]+\\payload\\dist\\lac\\lac\.exe$') {
        throw "The application signing target is not the exact handoff lac.exe."
    }
} else {
    $escapedVersion = [regex]::Escape($ExpectedVersion)
    if ($artifactRelativePath -cnotmatch "^[^\\]+\\payload\\dist\\LAC-Setup-$escapedVersion\.exe$") {
        throw "The installer signing target does not match the exact release version."
    }
}

foreach ($secretName in @("ESIGNER_USERNAME", "ESIGNER_PASSWORD", "ESIGNER_TOTP_SECRET")) {
    $secretValue = [Environment]::GetEnvironmentVariable($secretName)
    if ([string]::IsNullOrWhiteSpace($secretValue)) {
        throw "All protected eSigner credentials are required for a signing invocation."
    }
}
$esignerUsername = $env:ESIGNER_USERNAME
$esignerPassword = $env:ESIGNER_PASSWORD
$esignerTotpSecret = $env:ESIGNER_TOTP_SECRET
Remove-Item Env:ESIGNER_USERNAME, Env:ESIGNER_PASSWORD, Env:ESIGNER_TOTP_SECRET -ErrorAction Stop

if ([string]::IsNullOrWhiteSpace($env:ESIGNER_CERTIFICATE_SUBJECT) -or
    [string]::IsNullOrWhiteSpace($env:ESIGNER_CERTIFICATE_THUMBPRINT)) {
    throw "The protected environment must select an approved eSigner certificate identity."
}
$requestedSubject = $env:ESIGNER_CERTIFICATE_SUBJECT.Trim()
$requestedThumbprint = ($env:ESIGNER_CERTIFICATE_THUMBPRINT -replace '\s', '').ToUpperInvariant()
if ($requestedSubject.Length -gt 512 -or $requestedSubject -cnotmatch '^[\x20-\x7e]+$') {
    throw "The eSigner certificate subject is malformed."
}
if ($requestedThumbprint -cnotmatch '^[0-9A-F]{40}$') {
    throw "The eSigner certificate thumbprint must be an exact SHA-1 thumbprint."
}

function Read-StaticPythonFrozenset([string]$Source, [string]$Name) {
    $escapedName = [regex]::Escape($Name)
    $matches = @([regex]::Matches(
        $Source,
        "(?m)^$escapedName\s*:\s*frozenset\[str\]\s*=\s*frozenset\((?<body>[^\r\n]*)\)\s*$"
    ))
    if ($matches.Count -ne 1) {
        throw "The committed Authenticode trust-root declaration is not statically readable."
    }
    $body = $matches[0].Groups["body"].Value.Trim()
    if ($body.Length -eq 0) {
        return @()
    }
    if (-not ($body.StartsWith('{') -and $body.EndsWith('}'))) {
        throw "The committed Authenticode trust roots must be a literal string set."
    }
    $literalMatches = @([regex]::Matches(
        $body,
        '"(?<double>[^"\\\r\n]+)"|''(?<single>[^''\\\r\n]+)'''
    ))
    if ($literalMatches.Count -eq 0) {
        throw "The committed Authenticode trust roots contain no readable literals."
    }
    $cursor = 0
    $values = [Collections.Generic.List[string]]::new()
    for ($index = 0; $index -lt $literalMatches.Count; $index += 1) {
        $literal = $literalMatches[$index]
        $gap = $body.Substring($cursor, $literal.Index - $cursor)
        if (($index -eq 0 -and $gap -cnotmatch '^\{\s*$') -or
            ($index -gt 0 -and $gap -cnotmatch '^\s*,\s*$')) {
            throw "The committed Authenticode trust roots contain non-literal syntax."
        }
        $value = if ($literal.Groups["double"].Success) {
            $literal.Groups["double"].Value
        } else {
            $literal.Groups["single"].Value
        }
        if ($value -cnotmatch '^[\x20-\x7e]+$' -or $values.Contains($value)) {
            throw "The committed Authenticode trust roots are malformed or duplicated."
        }
        $values.Add($value)
        $cursor = $literal.Index + $literal.Length
    }
    $tail = $body.Substring($cursor)
    if ($tail -cnotmatch '^\s*,?\s*\}$') {
        throw "The committed Authenticode trust roots contain trailing syntax."
    }
    return @($values)
}

$trustSourcePath = Join-Path $workspace "scripts\enterprise_launch_gate.py"
if (-not (Test-Path -LiteralPath $trustSourcePath -PathType Leaf)) {
    throw "The committed Authenticode trust-root source is missing."
}
$trustSourceItem = Get-Item -LiteralPath $trustSourcePath -Force
if (($trustSourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing a reparse-point Authenticode trust-root source."
}
$trustSource = Get-Content -LiteralPath $trustSourcePath -Raw
$trustedSubjects = @(Read-StaticPythonFrozenset $trustSource "EXPECTED_AUTHENTICODE_SUBJECTS")
$trustedThumbprints = @(Read-StaticPythonFrozenset $trustSource "EXPECTED_AUTHENTICODE_THUMBPRINTS" |
    ForEach-Object { ($_ -replace '\s', '').ToUpperInvariant() })
if ($trustedSubjects.Count -eq 0 -or $trustedThumbprints.Count -eq 0 -or
    $trustedSubjects -cnotcontains $requestedSubject -or
    $trustedThumbprints -cnotcontains $requestedThumbprint) {
    throw "The selected eSigner identity is not present in the committed Authenticode trust roots."
}

$preExistingCertificates = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object {
    $_.Thumbprint -eq $requestedThumbprint -and $_.Subject -ceq $requestedSubject
})
if ($preExistingCertificates.Count -ne 0) {
    throw "Refusing to replace or later remove a pre-existing approved certificate."
}
$certificateStoreWasClear = $true

# Deliberately audited/checksummed workflow pin. This is not a current-version claim.
$packageUrl = "https://github.com/SSLcom/eSignerCKA/releases/download/v1.0.6/SSL.COM-eSigner-CKA_1.0.6.zip"
$packageSha256 = "e4971440e4ebed94328492cf36e18999554c5c657c856f1cb14a6072c8b1c263" # pragma: allowlist secret -- public package checksum
$expectedInstallerName = "SSL.COM eSigner CKA_1.0.6_build_20230829.exe"
$expectedInstallerBytes = 15811648
$expectedInstallerSubject = "OID.1.3.6.1.4.1.311.60.2.1.3=US, OID.1.3.6.1.4.1.311.60.2.1.2=Nevada, OID.2.5.4.15=Private Organization, CN=SSL Corp, SERIALNUMBER=NV20081614243, O=SSL Corp, L=Houston, S=Texas, C=US"
$expectedInstallerThumbprint = "67CFD66E24C76E766D55B0BC4B852CD52F2F8794" # pragma: allowlist secret -- public certificate thumbprint
$expectedInstallerTimestampSubject = "CN=SSL.com Timestamping Unit 2022, O=SSL Corp, L=Houston, S=Texas, C=US"
$expectedInstallerTimestampThumbprint = "AAC9F9414B41C33A2DFF9D8F4BD25244305489B2" # pragma: allowlist secret -- public certificate thumbprint
$archivePath = Join-Path $env:RUNNER_TEMP "SSL.COM-eSigner-CKA_1.0.6.zip"
$extractRoot = Join-Path $env:RUNNER_TEMP "esigner-cka-package"
$installRoot = Join-Path $env:RUNNER_TEMP "esigner-cka-install"
$masterKeyPath = Join-Path $env:RUNNER_TEMP "esigner-cka-master.key"
$sessionMarker = Join-Path $env:RUNNER_TEMP "esigner-cka-session.marker"
$sessionRoot = Join-Path $env:APPDATA "eSignerCKA"
$sessionOwned = $false
$ckaSessionMayBeLoaded = $false
$ckaTool = Join-Path $installRoot "eSignerCKATool.exe"

function Remove-ExactTree([string]$Path, [string]$Parent, [string]$Leaf) {
    $expected = [IO.Path]::GetFullPath((Join-Path $Parent $Leaf))
    $actual = [IO.Path]::GetFullPath($Path)
    if ($actual -cne $expected) {
        throw "Refusing an unexpected cleanup path."
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

try {
    foreach ($path in @(
        $archivePath, $extractRoot, $installRoot, $masterKeyPath, $sessionMarker, $sessionRoot
    )) {
        if (Test-Path -LiteralPath $path) {
            throw "Refusing to reuse pre-existing eSigner setup material."
        }
    }
    New-Item -ItemType File -Path $sessionMarker | Out-Null
    $sessionOwned = $true
    New-Item -ItemType Directory -Path $sessionRoot | Out-Null

    Invoke-WebRequest -Uri $packageUrl -OutFile $archivePath -MaximumRedirection 5
    $actualPackageSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualPackageSha256 -cne $packageSha256) {
        throw "The eSigner CKA package failed its pinned SHA-256 check."
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    $installers = @(Get-ChildItem -LiteralPath $extractRoot -Filter *.exe -File -Recurse)
    if ($installers.Count -ne 1 -or
        $installers[0].Name -cne $expectedInstallerName -or
        $installers[0].Length -ne $expectedInstallerBytes) {
        throw "The pinned eSigner CKA package has an unexpected inner installer identity."
    }
    $installer = $installers[0]
    $installerSignature = Get-AuthenticodeSignature -LiteralPath $installer.FullName
    if ($installerSignature.Status -ne "Valid" -or
        $installerSignature.SignerCertificate.Subject -cne $expectedInstallerSubject -or
        $installerSignature.SignerCertificate.Thumbprint -ne $expectedInstallerThumbprint -or
        $null -eq $installerSignature.TimeStamperCertificate -or
        $installerSignature.TimeStamperCertificate.Subject -cne $expectedInstallerTimestampSubject -or
        $installerSignature.TimeStamperCertificate.Thumbprint -ne $expectedInstallerTimestampThumbprint) {
        throw "The pinned eSigner CKA installer lacks its exact audited signer and timestamp identity."
    }
    New-Item -ItemType Directory -Path $installRoot | Out-Null
    & $installer.FullName /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES "/DIR=$installRoot"
    if ($LASTEXITCODE -ne 0) {
        throw "eSigner CKA installation failed."
    }
    if (-not (Test-Path -LiteralPath $ckaTool -PathType Leaf)) {
        throw "eSignerCKATool.exe was not installed at the audited location."
    }
    $ckaToolItem = Get-Item -LiteralPath $ckaTool -Force
    if (($ckaToolItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing a reparse-point eSigner CKA tool."
    }

    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -File -Recurse |
        Where-Object FullName -Match '\\x64\\signtool\.exe$' |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $signtool) {
        throw "signtool.exe was not found."
    }

    & $ckaTool config -mode "product" -user $esignerUsername -pass $esignerPassword -totp $esignerTotpSecret -key $masterKeyPath -r *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "eSigner CKA configuration failed."
    }
    $esignerUsername = $null
    $esignerPassword = $null
    $esignerTotpSecret = $null
    # Mark the session as uncertain before invoking the provider. Native-command
    # fail-fast can throw before control returns when load partially succeeds.
    $ckaSessionMayBeLoaded = $true
    & $ckaTool load *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "eSigner CKA certificate load failed."
    }

    $matchingCertificates = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object {
        $_.Thumbprint -eq $requestedThumbprint -and $_.Subject -ceq $requestedSubject
    })
    if ($matchingCertificates.Count -ne 1) {
        throw "Exactly one source-approved eSigner certificate must be loaded."
    }
    $certificate = $matchingCertificates[0]
    $now = [DateTime]::UtcNow
    $hasCodeSigningEku = @($certificate.EnhancedKeyUsageList | Where-Object {
        $_.ObjectId.Value -eq "1.3.6.1.5.5.7.3.3"
    }).Count -gt 0
    if (-not $certificate.HasPrivateKey -or -not $hasCodeSigningEku -or
        $certificate.NotBefore.ToUniversalTime() -gt $now -or
        $certificate.NotAfter.ToUniversalTime() -le $now) {
        throw "The loaded eSigner certificate is unusable for code signing."
    }

    & $signtool sign /fd SHA256 /tr http://ts.ssl.com /td SHA256 /sha1 $requestedThumbprint /s My $artifact
    if ($LASTEXITCODE -ne 0) {
        throw "The eSigner Authenticode operation failed."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $artifact
    if ($signature.Status -ne "Valid" -or
        $signature.SignerCertificate.Thumbprint -ne $requestedThumbprint -or
        $signature.SignerCertificate.Subject -cne $requestedSubject -or
        $null -eq $signature.TimeStamperCertificate) {
        throw "The signed artifact failed exact identity or timestamp verification."
    }
    $timestampEku = $signature.TimeStamperCertificate.Extensions |
        Where-Object { $_.Oid.Value -eq "2.5.29.37" }
    if (@($timestampEku.EnhancedKeyUsages | Where-Object {
        $_.Value -eq "1.3.6.1.5.5.7.3.8"
    }).Count -eq 0) {
        throw "The signed artifact timestamp lacks the timestamping EKU."
    }
    $verification = @(& $signtool verify /pa /all /v $artifact 2>&1)
    $verificationText = $verification -join "`n"
    $signatureIndexes = @([regex]::Matches(
        $verificationText,
        '(?im)^\s*Signature Index:\s*(?<index>\d+).*$'
    ))
    if ($LASTEXITCODE -ne 0 -or
        $signatureIndexes.Count -ne 1 -or
        $signatureIndexes[0].Groups["index"].Value -cne "0" -or
        $verificationText -cnotmatch '(?im)^\s*The signature is timestamped:\s*.+$') {
        throw "The signed artifact failed independent SignTool timestamp verification."
    }
} finally {
    $esignerUsername = $null
    $esignerPassword = $null
    $esignerTotpSecret = $null
    $cleanupFailures = [Collections.Generic.List[string]]::new()

    $unloadConfirmed = -not $ckaSessionMayBeLoaded

    if ($ckaSessionMayBeLoaded -and (Test-Path -LiteralPath $ckaTool -PathType Leaf)) {
        try {
            & $ckaTool unload *> $null
            if ($LASTEXITCODE -ne 0) {
                $cleanupFailures.Add("unload")
            } else {
                $ckaSessionMayBeLoaded = $false
                $unloadConfirmed = $true
            }
        } catch {
            $cleanupFailures.Add("unload")
        }
    } elseif ($ckaSessionMayBeLoaded) {
        $cleanupFailures.Add("unload")
    }

    $cleanupAuthorized = $false
    if ($sessionOwned -and (Test-Path -LiteralPath $sessionMarker -PathType Leaf)) {
        try {
            $marker = Get-Item -LiteralPath $sessionMarker -Force
            if (($marker.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing cleanup with a reparse-point session marker."
            }
            $cleanupAuthorized = $true
        } catch {
            $cleanupFailures.Add("session-marker-ownership")
        }
    } elseif ($sessionOwned) {
        $cleanupFailures.Add("session-marker-ownership")
    }

    if ($cleanupAuthorized -and $unloadConfirmed) {
        if ($certificateStoreWasClear) {
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
        if ($cleanupFailures.Count -eq 0) {
            try {
                Remove-Item -LiteralPath $sessionMarker -Force
            } catch {
                $cleanupFailures.Add("session-marker")
            }
        }
    }

    if ($cleanupFailures.Count -gt 0) {
        throw "One or more eSigner CKA cleanup operations failed."
    }
}
