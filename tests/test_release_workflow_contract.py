from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_requires_exact_source_version_and_protected_controls():
    text = _workflow()

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
    assert '$tag = "${{' not in text
    assert 'git rev-parse --verify "refs/tags/$tag^{commit}"' in text
    assert "$tagCommit -ne $env:GITHUB_SHA" in text
    assert "production-release" in text
    assert "INNO_SETUP_LICENSE_CONFIRMED" in text
    assert "SIGNING_CERTIFICATE_PFX_BASE64" in text
    assert "SIGNING_CERTIFICATE_PASSWORD" in text
    assert "SIGNING_TIMESTAMP_URL" in text
    assert "-replace '#define MyAppVersion" not in text


def test_release_workflow_signs_payload_then_installer_and_verifies_both():
    text = _workflow()

    application_sign = text.index("dist\\lac\\lac.exe")
    installer_build = text.index("ISCC.exe")
    installer_sign = text.index("Installer signing failed")

    assert application_sign < installer_build < installer_sign
    assert text.count("Get-AuthenticodeSignature") == 2
    assert 'if ($signature.Status -ne "Valid")' in text
    assert "release-provenance.json" in text
    assert "SHA256SUMS.txt" in text
    assert "requirements-release.lock" in text
    assert "--require-hashes" in text
    assert "dependency_lock_sha256" in text
    assert "actions/attest-build-provenance@" in text
    assert "Get-Rfc3161SignatureEvidence" in text
    assert 'protocol = "RFC3161"' in text
    assert "TimeStamperCertificate" in text
    assert "timestamped_at_utc" in text
    assert 'python -m pytest -m "not live"' in text
    assert "detect-secrets-hook --baseline .secrets.baseline" in text
    assert "npx wrangler deploy --dry-run --env production" in text


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


def test_release_workflow_only_creates_a_draft_release():
    text = _workflow()

    assert "draft: true" in text
    assert "draft: false" not in text
    assert "target_commitish: ${{ github.sha }}" in text
    assert "contents: write" in text


def test_release_secrets_are_scoped_to_the_steps_that_need_them():
    text = _workflow()
    job_prefix = text.split("    steps:", 1)[0]

    assert "SIGNING_CERTIFICATE_PFX_BASE64" not in job_prefix
    assert "SIGNING_CERTIFICATE_PASSWORD" not in job_prefix
