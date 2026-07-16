from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SIGNER = ROOT / "scripts" / "sign_windows_artifact.ps1"
HANDOFF = ROOT / "scripts" / "release_handoff_manifest.ps1"
CLEANER = ROOT / "scripts" / "cleanup_esigner_session.ps1"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _signer() -> str:
    return SIGNER.read_text(encoding="utf-8")


def _handoff() -> str:
    return HANDOFF.read_text(encoding="utf-8")


def _cleaner() -> str:
    return CLEANER.read_text(encoding="utf-8")


def _job(name: str) -> str:
    text = _workflow()
    start = text.index(f"  {name}:\n")
    next_job = re.search(r"(?m)^  [a-z][a-z0-9-]+:\n", text[start + 1 :])
    end = len(text) if next_job is None else start + 1 + next_job.start()
    return text[start:end]


def _step(job_name: str, step_name: str) -> str:
    job = _job(job_name)
    start = job.index(f"      - name: {step_name}\n")
    next_step = job.find("\n      - name:", start + 1)
    return job[start:] if next_step == -1 else job[start:next_step]


def test_release_workflow_requires_exact_source_version_and_protected_controls():
    text = _workflow()
    authorize = _job("authorize-release")

    assert "scripts/verify_release_version.py --expected $tag" in text
    assert "scripts/pro_commerce_readiness.py --require-baked-gate --allow-missing-lac-pro" in text
    assert "--worker-env production --allow-worker-placeholders" in text
    assert text.count('--expected-deployment-commit "${{ github.sha }}"') >= 2
    assert "--valid-key-env LAC_PRO_TEST_LOCAL_KEY" in text
    assert "--valid-key-env LAC_PRO_TEST_CLOUD_KEY" in text
    assert "PRO_GATE_TESTED_VERSION" in text
    assert "$env:PRO_GATE_TESTED_VERSION -ne $env:GITHUB_SHA" in text
    assert "PRO_GATE_WAF_EVIDENCE" in text
    assert "REQUESTED_RELEASE_TAG:" in text
    assert "$tag = $env:REQUESTED_RELEASE_TAG" in text
    assert '$tag = "${{' not in authorize
    assert 'git rev-parse --verify "refs/tags/$tag^{commit}"' in text
    assert "$tagCommit -ne $env:GITHUB_SHA" in text
    assert 'git cat-file -t "refs/tags/$tag"' in text
    assert '"/repos/$env:GITHUB_REPOSITORY/git/tags/$tagObject"' in text
    assert "$tagRecord.verification.verified -ne $true" in text
    assert "$tagRecord.tag -ne $tag" in text
    assert "persist-credentials: false" in text
    assert '$env:GITHUB_REF -ne "refs/tags/$tag"' in text
    assert "group: release-${{ github.event_name == 'workflow_dispatch' && inputs.version || github.ref_name }}" in text
    assert '"/repos/$env:GITHUB_REPOSITORY/git/ref/tags/$tag"' in text
    assert "$tagRefRecord.object.sha -ne $tagObject" in text
    assert "production-release" in text
    assert "INNO_SETUP_LICENSE_CONFIRMED" in text
    assert "ESIGNER_USERNAME" in text
    assert "ESIGNER_PASSWORD" in text
    assert "ESIGNER_TOTP_SECRET" in text
    assert "ESIGNER_CERTIFICATE_SUBJECT" in text
    assert "ESIGNER_CERTIFICATE_THUMBPRINT" in text
    assert "SIGNING_CERTIFICATE_PFX_BASE64" not in text
    assert "SIGNING_CERTIFICATE_PASSWORD" not in text
    assert "Import-PfxCertificate" not in text
    assert "-replace '#define MyAppVersion" not in text
    assert text.index("Resolve and verify the immutable release version") < text.index(
        "Require protected release controls"
    )


def test_release_workflow_signs_payload_then_installer_and_verifies_both():
    text = _workflow()

    assert "  authorize-release:" in text
    assert "  build-unsigned-app:" in text
    assert "  sign-application:" in text
    assert "  package-unsigned-installer:" in text
    assert "  sign-installer-and-attest:" in text
    assert "needs: authorize-release" in _job("build-unsigned-app")
    assert "needs: [authorize-release, build-unsigned-app]" in _job("sign-application")
    assert "needs: [authorize-release, sign-application]" in _job(
        "package-unsigned-installer"
    )
    assert "needs: [authorize-release, package-unsigned-installer]" in _job(
        "sign-installer-and-attest"
    )
    assert text.count("scripts/sign_windows_artifact.ps1") == 2
    assert '-ArtifactKind Application' in _job("sign-application")
    assert '-ArtifactKind Installer' in _job("sign-installer-and-attest")
    assert "Get-AuthenticodeSignature" in _job("sign-installer-and-attest")
    assert 'if ($signature.Status -ne "Valid")' in text
    assert "release-provenance.json" in text
    assert "SHA256SUMS.txt" in text
    assert "requirements-release.lock" in text
    assert "--require-hashes" in text
    assert "dependency_lock_sha256" in text
    assert "schema_version = 2" in text
    assert "authenticode_subject =" not in text
    assert "authenticode_thumbprint =" not in text
    assert '$pythonSbomItem = Get-Item "dist\\python-sbom.json"' in text
    assert '$webSbomItem = Get-Item "dist\\web-sbom.json"' in text
    assert "python_sbom = [ordered]@{" in text
    assert "web_sbom = [ordered]@{" in text
    assert "            dist/web-sbom.json" in text
    assert "            web/dist/web-sbom.json" not in text
    assert "actions/attest-build-provenance@" in text
    assert "Get-Rfc3161SignatureEvidence" in text
    assert 'protocol = "RFC3161"' in text
    assert "TimeStamperCertificate" in text
    assert "timestamped_at_utc" in text
    assert 'python -m pytest -m "not live"' in text
    assert "detect-secrets-hook --baseline .secrets.baseline" in text
    assert "npx wrangler deploy --dry-run --env production" in text


def test_release_workflow_attests_every_exact_release_subject():
    text = _job("sign-installer-and-attest")
    attest_start = text.index("      - name: Attest the signed release provenance")
    attest_end = text.index("      - name: Upload signed release candidate", attest_start)
    attestation_step = text[attest_start:attest_end]

    assert "          subject-path: |" in attestation_step
    assert [
        line.strip()
        for line in attestation_step.splitlines()
        if line.startswith("            dist/")
    ] == [
        "dist/LAC-Setup-${{ needs.authorize-release.outputs.version }}.exe",
        "dist/lac/lac.exe",
        "dist/SHA256SUMS.txt",
        "dist/release-provenance.json",
        "dist/python-sbom.json",
        "dist/web-sbom.json",
    ]


def test_signed_candidate_retains_packaged_application_without_publishing_it_directly():
    text = _job("sign-installer-and-attest")
    upload_start = text.index("      - name: Upload signed release candidate")
    candidate_step = text[upload_start:]

    assert "            dist/lac/lac.exe" in candidate_step
    assert "            release/lac/lac.exe" not in text


def test_release_workflow_pins_every_third_party_action_to_a_commit():
    text = _workflow()
    uses_lines = [
        line.strip().removeprefix("- ")
        for line in text.splitlines()
        if line.strip().removeprefix("- ").startswith("uses:")
    ]

    assert uses_lines
    for line in uses_lines:
        reference = line.split("@", 1)[1].split()[0]
        assert len(reference) == 40
        assert all(character in "0123456789abcdef" for character in reference)

    assert text.count(
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"  # pragma: allowlist secret -- public Git commit
    ) == 4
    assert text.count(
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"  # pragma: allowlist secret -- public Git commit
    ) == 3


def test_release_workflow_pins_and_verifies_esigner_cka_before_execution():
    signer = _signer()

    package_url = (
        "https://github.com/SSLcom/eSignerCKA/releases/download/v1.0.6/"
        "SSL.COM-eSigner-CKA_1.0.6.zip"
    )
    package_sha256 = "e4971440e4ebed94328492cf36e18999554c5c657c856f1cb14a6072c8b1c263"  # pragma: allowlist secret -- public package checksum

    assert package_url in signer
    assert package_sha256 in signer
    assert "Get-FileHash -LiteralPath $archivePath -Algorithm SHA256" in signer
    assert signer.index("Get-FileHash -LiteralPath $archivePath") < signer.index(
        "Expand-Archive -LiteralPath $archivePath"
    )
    assert signer.index("Expand-Archive -LiteralPath $archivePath") < signer.index(
        "& $installer.FullName"
    )
    assert 'config -mode "product"' in signer and '-r *> $null' in signer
    assert '& $ckaTool load *> $null' in signer
    assert "EXPECTED_AUTHENTICODE_SUBJECTS" in signer
    assert "EXPECTED_AUTHENTICODE_THUMBPRINTS" in signer
    assert signer.index("EXPECTED_AUTHENTICODE_SUBJECTS") < signer.index(
        "Invoke-WebRequest"
    )
    assert "$preExistingCertificates.Count -ne 0" in signer
    assert signer.index("$preExistingCertificates.Count -ne 0") < signer.index(
        "Invoke-WebRequest"
    )
    assert "Refusing to replace or later remove a pre-existing approved certificate" in signer
    assert '$sessionRoot = Join-Path $env:APPDATA "eSignerCKA"' in signer
    assert "$sessionMarker, $sessionRoot" in signer
    assert "New-Item -ItemType Directory -Path $sessionRoot" in signer
    assert "$matchingCertificates.Count -ne 1" in signer
    assert "Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert" in signer
    assert signer.count("Select-Object -First 1") == 1
    assert 'SSL.COM eSigner CKA_1.0.6_build_20230829.exe' in signer
    assert "$expectedInstallerBytes = 15811648" in signer
    assert "67CFD66E24C76E766D55B0BC4B852CD52F2F8794" in signer  # pragma: allowlist secret -- public certificate thumbprint
    assert "AAC9F9414B41C33A2DFF9D8F4BD25244305489B2" in signer  # pragma: allowlist secret -- public certificate thumbprint


def test_release_workflow_uses_exact_approved_esigner_identity_for_both_signatures():
    text = _workflow()
    signer = _signer()

    # Verify the fixed RFC3161 endpoint without presenting a URL substring check
    # for CodeQL to misclassify as a security decision in this contract test.
    assert "/fd SHA256 /tr http:" in signer
    assert "//ts.ssl.com /td SHA256 /sha1 $requestedThumbprint" in signer
    assert "$signature.SignerCertificate.Thumbprint -ne $requestedThumbprint" in signer
    assert "$signature.SignerCertificate.Subject -cne $requestedSubject" in signer
    assert "$signature.SignerCertificate.Thumbprint -ne $env:ESIGNER_CERTIFICATE_THUMBPRINT" in text
    assert "$signature.SignerCertificate.Subject -cne $env:ESIGNER_CERTIFICATE_SUBJECT" in text


def test_release_workflow_always_removes_esigner_session_material():
    text = _workflow()
    signer = _signer()
    cleaner = _cleaner()

    assert "finally {" in signer
    assert "eSignerCKATool.exe" in signer
    assert "& $ckaTool unload *> $null" in signer
    assert "esigner-cka-session.marker" in signer
    assert "Refusing cleanup with a reparse-point session marker" in signer
    assert "Remove-Item -LiteralPath $masterKeyPath" in signer
    assert "Remove-ExactTree" in signer
    assert "if ($cleanupAuthorized -and $unloadConfirmed)" in signer
    assert text.count("scripts/cleanup_esigner_session.ps1") == 2
    assert text.count("if: ${{ always() }}") == 2
    assert "ownership marker was retained" in cleaner
    assert cleaner.index("$cleanupFailures.Count -gt 0") < cleaner.index(
        "Remove-Item -LiteralPath $sessionMarker -Force"
    )
    assert "Upload eSignerCKA Logs" not in text
    assert "GITHUB_ENV" not in signer
    assert "GITHUB_ENV" not in text


def test_partial_esigner_load_failure_requires_confirmed_unload_before_cleanup():
    signer = _signer()
    load = signer.index("& $ckaTool load *> $null")
    may_be_loaded = signer.rindex("$ckaSessionMayBeLoaded = $true", 0, load)
    cleanup = signer[signer.index("} finally {") :]
    unload = cleanup.index("& $ckaTool unload *> $null")
    cleanup_gate = cleanup.index("if ($cleanupAuthorized -and $unloadConfirmed)")

    assert may_be_loaded < load
    assert "if ($ckaSessionMayBeLoaded -and" in cleanup
    assert "$unloadConfirmed = -not $ckaSessionMayBeLoaded" in cleanup
    assert "$unloadConfirmed = $true" in cleanup
    assert unload < cleanup_gate
    assert cleanup.index("Remove-ExactTree -Path", cleanup_gate) > cleanup_gate
    assert cleanup.index("Remove-Item -LiteralPath $sessionMarker", cleanup_gate) > cleanup_gate
    assert "& $ckaTool load *> $null" in signer
    assert "& $ckaTool unload *> $null" in signer


def test_esigner_sessions_wrap_only_one_sign_and_verify_operation():
    signer = _signer()
    load = signer.index("& $ckaTool load *> $null")
    unload = signer.index("& $ckaTool unload *> $null", load)
    active_session = signer[load:unload]

    assert "& $signtool sign" in active_session
    assert "Get-AuthenticodeSignature" in active_session
    assert "& $signtool verify /pa /all /v" in active_session
    assert '$preSigningSignature.Status -ne "NotSigned"' in signer
    assert "/as" not in signer
    assert "$signatureIndexes.Count -ne 1" in active_session
    for forbidden in (
        "Invoke-WebRequest",
        "Expand-Archive",
        "choco",
        "ISCC.exe",
        "npm ",
        "pip ",
        "pyinstaller",
    ):
        assert forbidden not in active_session


def test_signing_credentials_run_only_on_fresh_dedicated_jobs():
    build = _job("build-unsigned-app")
    app_sign = _job("sign-application")
    package = _job("package-unsigned-installer")
    installer_sign = _job("sign-installer-and-attest")

    assert "${{ secrets." not in build
    assert "${{ secrets." not in package
    assert "npm ci" in build
    assert "uv pip install" in build
    assert "pyinstaller -y build.spec" in build
    assert "jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe" in package
    assert "choco" not in package.lower()
    assert "ISCC.exe" in package

    for signing_job in (app_sign, installer_sign):
        assert "runs-on: windows-latest" in signing_job
        assert "actions/checkout@" in signing_job
        assert "actions/download-artifact@" in signing_job
        assert "setup-python" not in signing_job
        assert "setup-node" not in signing_job
        for forbidden in (
            "npm ci",
            "uv pip install",
            "pyinstaller -y",
            "choco install",
            "& $iscc installer.iss",
        ):
            assert forbidden not in signing_job
        for secret in ("ESIGNER_USERNAME", "ESIGNER_PASSWORD", "ESIGNER_TOTP_SECRET"):
            assert signing_job.count("${{ secrets." + secret + " }}") == 1


def test_every_artifact_handoff_has_a_digest_bound_manifest():
    text = _workflow()
    handoff = _handoff()

    assert text.count("actions/upload-artifact@") == 4
    assert text.count("actions/download-artifact@") == 3
    assert text.count("scripts/release_handoff_manifest.ps1") >= 6
    for stage in ("unsigned-app", "signed-app", "unsigned-installer"):
        assert f'-Stage "{stage}"' in text
        assert f"EXPECTED_{stage.replace('-', '_').upper()}_MANIFEST_SHA256" in text
    assert text.count("artifact-digest") >= 3
    assert "ExpectedManifestSha256" in handoff
    assert "Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256" in handoff
    assert "$actualFiles.Count -ne $manifestFiles.Count" in handoff
    assert "Refusing a handoff tree containing a reparse point" in handoff
    assert "exit 0" not in handoff
    assert "Assert-OrdinaryPathChain -Path $root -StopAt $handoffRoot" in handoff
    assert "Assert-OrdinaryPathChain -Path $manifestParent -StopAt $handoffRoot" in handoff
    assert "Refusing a handoff path with a reparse-point ancestor" in handoff
    assert text.count("-cnotmatch '^[0-9a-f]{64}$'") >= 3


def test_each_job_is_bound_to_the_verified_source_commit_and_tag():
    text = _workflow()

    assert '"source_commit=$env:GITHUB_SHA" >> $env:GITHUB_OUTPUT' in text
    assert '"tag_object=$tagObject" >> $env:GITHUB_OUTPUT' in text
    for name in (
        "build-unsigned-app",
        "sign-application",
        "package-unsigned-installer",
        "sign-installer-and-attest",
    ):
        job = _job(name)
        assert "needs.authorize-release.outputs.source_commit" in job
        assert "${{ github.sha }}" in job
        assert "git rev-parse --verify HEAD" in job


def test_packaging_checks_exact_inno_install_before_execution():
    package = _job("package-unsigned-installer")

    download = package.index("Invoke-WebRequest -Uri $innoInstallerUrl")
    archive_hash = package.index("Get-FileHash -LiteralPath $innoInstallerPath", download)
    install = package.index("& $innoInstallerPath", archive_hash)
    installed_hash = package.index("Get-FileHash -LiteralPath $iscc", install)
    compile_installer = package.index("& $iscc installer.iss")

    assert download < archive_hash < install < installed_hash < compile_installer
    assert "choco" not in package.lower()
    assert "CurrentVersion\\Uninstall" not in package
    assert (
        "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/"
        "innosetup-6.7.3.exe"
    ) in package
    assert "$expectedInnoInstallerBytes = 10592232" in package
    assert "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732" in package  # pragma: allowlist secret -- public package checksum
    assert "E0AB19C8D38CBF9C44709925122A7A02F8C70CB7" in package  # pragma: allowlist secret -- public certificate thumbprint
    assert "38C914811044B4DC663E93D4744B814186A9B5B1" in package  # pragma: allowlist secret -- public certificate thumbprint
    assert "CN=Sectigo Public Time Stamping Signer R36" in package
    assert '"/DIR=$innoInstallRoot"' in package
    assert "/CURRENTUSER" in package
    assert "Refusing to reuse pre-existing Inno Setup material" in package
    assert "Refusing to remove an Inno Setup reparse point" in package
    assert "finally {" in package
    assert "unins000.exe" in package
    assert "Remove-ExactInnoTree" in package
    assert "Get-AuthenticodeSignature -LiteralPath $iscc" in package
    assert '$isccSignature.Status -ne "Valid"' in package
    assert "$expectedIsccBytes = 1456272" in package
    assert "$isccItem.Length -ne $expectedIsccBytes" in package
    assert "0a8757031b33777e4c9cbffee40f11a5062b36d25cbe144c1db73b6102b80ad7" in package  # pragma: allowlist secret -- public package checksum
    assert "E0AB19C8D38CBF9C44709925122A7A02F8C70CB7" in package  # pragma: allowlist secret -- public certificate thumbprint
    assert '$applicationSignature.Status -ne "Valid"' in package


def test_native_workflow_blocks_stop_before_later_commands_can_mask_failures():
    production_gate = _step(
        "authorize-release", "Smoke the exact protected production delivery gate"
    )
    first_gate = production_gate.index(
        "python scripts/pro_commerce_readiness.py"
    )
    assert production_gate.count("python scripts/pro_commerce_readiness.py") == 2
    assert production_gate.index('$ErrorActionPreference = "Stop"') < first_gate
    assert production_gate.index(
        "$PSNativeCommandUseErrorActionPreference = $true"
    ) < first_gate

    for step_name, first_native in (
        ("Build web app", "npm ci"),
        ("Audit and install Python dependencies", "python -m pip install"),
        ("Run the complete source and secret gates", 'python -m pytest -m "not live"'),
        ("Run the complete delivery Worker gates", "npm ci"),
        ("Build unsigned application", "pyinstaller -y build.spec"),
    ):
        step = _step("build-unsigned-app", step_name)
        command = step.index(first_native)
        assert step.index('$ErrorActionPreference = "Stop"') < command
        assert step.index("$PSNativeCommandUseErrorActionPreference = $true") < command

    for job_name, step_name, first_native in (
        ("authorize-release", "Resolve and verify the immutable release version", "git cat-file"),
        ("build-unsigned-app", "Bind build inputs to the authorized source", "git rev-parse"),
        ("build-unsigned-app", "Assemble unsigned application handoff", "python --version"),
        ("sign-application", "Bind signing helper to the authorized source", "git rev-parse"),
        (
            "sign-application",
            "Revalidate immutable tag immediately before application signing",
            "gh api",
        ),
        (
            "package-unsigned-installer",
            "Bind packaging inputs to the authorized source",
            "git rev-parse",
        ),
        (
            "package-unsigned-installer",
            "Build unsigned installer with pinned Inno Setup",
            "& $innoInstallerPath",
        ),
        (
            "sign-installer-and-attest",
            "Bind final signing helper to the authorized source",
            "git rev-parse",
        ),
        (
            "sign-installer-and-attest",
            "Revalidate immutable tag immediately before installer signing",
            "gh api",
        ),
        (
            "sign-installer-and-attest",
            "Verify release artifacts and create provenance",
            "& $signtool verify",
        ),
    ):
        step = _step(job_name, step_name)
        command = step.index(first_native)
        assert step.index('$ErrorActionPreference = "Stop"') < command
        assert step.index("$PSNativeCommandUseErrorActionPreference = $true") < command

    assert "$PSNativeCommandUseErrorActionPreference = $true" in _signer()
    assert "$PSNativeCommandUseErrorActionPreference = $true" in _cleaner()


def test_remote_tag_is_revalidated_immediately_before_each_provider_call():
    for job_name, sign_step in (
        ("sign-application", "Sign application through eSigner CKA"),
        ("sign-installer-and-attest", "Sign installer through eSigner CKA"),
    ):
        job = _job(job_name)
        revalidate = job.index("Revalidate immutable tag immediately before")
        signing = job.index(sign_step)
        between = job[revalidate:signing]

        assert revalidate < signing
        assert '"/repos/$env:GITHUB_REPOSITORY/git/ref/tags/$tag"' in between
        assert '"/repos/$env:GITHUB_REPOSITORY/git/tags/$expectedTagObject"' in between
        assert "$tagRecord.verification.verified -ne $true" in between
        assert "needs.authorize-release.outputs.tag_object" in between
        assert "needs.authorize-release.outputs.source_commit" in between


def test_release_workflow_is_candidate_only_until_the_enterprise_gate_can_run():
    text = _workflow()

    assert text.startswith("name: Signed Windows Release Candidate\n")
    assert "draft-release" not in text
    assert "softprops/action-gh-release" not in text
    assert "contents: write" not in text
    assert "draft: true" not in text
    assert "draft: false" not in text


def test_python_sbom_step_creates_root_dist_before_writing_into_it():
    text = _workflow()
    step_start = text.index("      - name: Audit and install Python dependencies")
    step_end = text.index("      - name: Run the complete source and secret gates", step_start)
    step = text[step_start:step_end]

    create_dist = "New-Item -ItemType Directory -Force -Path dist | Out-Null"
    write_sbom = "python -m cyclonedx_py environment --output-file dist/python-sbom.json"
    assert create_dist in step
    assert write_sbom in step
    assert step.index(create_dist) < step.index(write_sbom)


def test_release_secrets_are_scoped_to_the_steps_that_need_them():
    text = _workflow()

    for secret in ("ESIGNER_USERNAME", "ESIGNER_PASSWORD", "ESIGNER_TOTP_SECRET"):
        reference = "${{ secrets." + secret + " }}"
        assert text.count(reference) == 2

    assert "SIGNING_CERTIFICATE_PFX_BASE64" not in text
    assert "SIGNING_CERTIFICATE_PASSWORD" not in text
